"""Zones et tables d'un site de restauration (palier R5-A, ADR-0049 ; ``RESTAURANT.md`` §4).

Configuration seulement : aucune commande n'est créée ni modifiée ici (rattachement d'une table
à une commande : palier R5-B, par le port des commandes). Contrôles du serveur :

- zone d'une table : facultative (A4), du MÊME site (FK composite en base) et active ;
- unicités (nom de zone, libellé de table, par site, insensibles à la casse) garanties par la
  base : une création concurrente donne un ``409`` ;
- désactivation d'une zone refusée tant qu'elle contient des tables actives ; désactivation
  d'une table refusée tant qu'une commande non finale y est rattachée (lue par l'interface
  publique des commandes) ; réactivation d'une table refusée dans une zone inactive ;
- rien n'est jamais supprimé ; chaque changement est audité (avant / après).

Verrous (``RESTAURANT.md`` §7, R5) : une zone est prise en PARTAGE pour y rattacher une table
(création, modification, réactivation) et en EXCLUSIF pour la désactiver ; une table est prise en
EXCLUSIF pour la modifier, l'activer ou la désactiver (puis sa zone en partage) — ordre table →
zone, jamais l'inverse. Les comptages qui suivent un verrou exclusif lisent les écritures
validées entre-temps (nouvel instantané par instruction, READ COMMITTED). Portée : ``sites.py``.
Ne valide pas la transaction (ADR-0008).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.restaurant_orders.api import open_orders_by_table
from app.modules.restaurant_tables.models import (
    TABLE_LABEL_INDEX,
    ZONE_NAME_INDEX,
    RestaurantTable,
    RestaurantZone,
)
from app.modules.restaurant_tables.schemas import TableCreate, TableUpdate
from app.modules.restaurant_tables.sites import ensure_row_site, operation_site, readable_site_ids
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort
from app.shared.schemas import StatusFilter

ZONE_NAME_TAKEN = ("zone_name_taken", "Une zone de ce nom existe déjà sur ce site")
TABLE_LABEL_TAKEN = ("table_label_taken", "Une table de ce libellé existe déjà sur ce site")
ZONE_NOT_FOUND = "zone_not_found"
TABLE_NOT_FOUND = "table_not_found"


@dataclass(frozen=True)
class ZoneRow:
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    sort_order: int
    is_active: bool
    table_count: int
    active_table_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TableRow:
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    zone_id: uuid.UUID | None
    zone_name: str | None
    zone_active: bool | None
    label: str
    seats: int | None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


def _table_state(table: RestaurantTable) -> dict[str, Any]:
    return {
        "label": table.label,
        "zone_id": str(table.zone_id) if table.zone_id else None,
        "seats": table.seats,
        "sort_order": table.sort_order,
    }


class TableService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    # --- Commun -------------------------------------------------------------------------------

    def _flush(self) -> None:
        """Les unicités font foi en base : une création concurrente échoue ici (``409``)."""
        try:
            self.db.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == ZONE_NAME_INDEX:
                raise ConflictError(ZONE_NAME_TAKEN[1], code=ZONE_NAME_TAKEN[0]) from exc
            if constraint == TABLE_LABEL_INDEX:
                raise ConflictError(TABLE_LABEL_TAKEN[1], code=TABLE_LABEL_TAKEN[0]) from exc
            raise

    def _audit(self, action: str, entity_type: str, entity: Any, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"{entity_type}.{action}",
            entity_type=entity_type,
            entity_id=entity.id,
            site_id=entity.site_id,
            data=data,
        )

    # --- Zones : lecture ----------------------------------------------------------------------

    def _zone_query(self) -> Select[Any]:
        counts = (
            select(
                RestaurantTable.zone_id,
                func.count().label("table_count"),
                func.count().filter(RestaurantTable.is_active).label("active_table_count"),
            )
            .where(RestaurantTable.zone_id.is_not(None))
            .group_by(RestaurantTable.zone_id)
            .subquery("restaurant_zone_table_counts")
        )
        return (
            select(
                RestaurantZone.id,
                RestaurantZone.site_id,
                Site.name.label("site_name"),
                RestaurantZone.name,
                RestaurantZone.sort_order,
                RestaurantZone.is_active,
                func.coalesce(counts.c.table_count, 0).label("table_count"),
                func.coalesce(counts.c.active_table_count, 0).label("active_table_count"),
                RestaurantZone.created_at,
                RestaurantZone.updated_at,
            )
            .join(
                Site,
                (Site.tenant_id == RestaurantZone.tenant_id) & (Site.id == RestaurantZone.site_id),
            )
            .outerjoin(counts, counts.c.zone_id == RestaurantZone.id)
            .where(RestaurantZone.tenant_id == self.ctx.tenant_id)
        )

    def search_zones(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        site_id: uuid.UUID | None,
    ) -> tuple[list[ZoneRow], int]:
        stmt = self._zone_query().where(
            RestaurantZone.site_id.in_(readable_site_ids(self.ctx, site_id))
        )
        condition = search_filter(search, RestaurantZone.name)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(RestaurantZone.is_active.is_(status is StatusFilter.ACTIVE))
        if params.sort in (None, "position"):
            stmt = stmt.order_by(
                RestaurantZone.sort_order, text_sort(RestaurantZone.name), RestaurantZone.id
            )
        else:
            sortable = {
                "position": RestaurantZone.sort_order,
                "name": text_sort(RestaurantZone.name),
                "site": text_sort(Site.name),
                "created_at": RestaurantZone.created_at,
            }
            stmt = apply_sort(stmt, params.sort, sortable, "position", RestaurantZone.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [ZoneRow(**row._mapping) for row in rows], total

    def zone_row(self, zone_id: uuid.UUID) -> ZoneRow:
        row = self.db.execute(self._zone_query().where(RestaurantZone.id == zone_id)).one()
        return ZoneRow(**row._mapping)

    def _get_zone(self, zone_id: uuid.UUID, *, lock: bool = False) -> RestaurantZone:
        """Zone d'un site lisible par le membre ; sinon introuvable (jamais révélée)."""
        stmt = select(RestaurantZone).where(RestaurantZone.id == zone_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        zone = self.db.scalars(stmt).one_or_none()
        if zone is None:
            raise NotFoundError("Zone introuvable", code=ZONE_NOT_FOUND)
        ensure_row_site(self.ctx, zone.site_id, ZONE_NOT_FOUND)
        return zone

    def get_zone(self, zone_id: uuid.UUID) -> ZoneRow:
        return self.zone_row(self._get_zone(zone_id).id)

    # --- Zones : écriture ---------------------------------------------------------------------

    def _ensure_zone_name_free(
        self, site_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = select(RestaurantZone.id).where(
            RestaurantZone.site_id == site_id, func.lower(RestaurantZone.name) == name.lower()
        )
        if exclude is not None:
            stmt = stmt.where(RestaurantZone.id != exclude)
        if self.db.scalar(stmt) is not None:
            raise ConflictError(ZONE_NAME_TAKEN[1], code=ZONE_NAME_TAKEN[0])

    def create_zone(self, site_id: uuid.UUID | None, name: str, sort_order: int) -> RestaurantZone:
        site = operation_site(self.ctx, site_id)
        self._ensure_zone_name_free(site, name)
        zone = RestaurantZone(
            tenant_id=self.ctx.tenant_id, site_id=site, name=name, sort_order=sort_order
        )
        self.db.add(zone)
        self._flush()
        self._audit("created", "restaurant_zone", zone, {"name": name, "sort_order": sort_order})
        return zone

    def update_zone(self, zone_id: uuid.UUID, name: str, sort_order: int) -> RestaurantZone:
        zone = self._get_zone(zone_id, lock=True)
        before = {"name": zone.name, "sort_order": zone.sort_order}
        after = {"name": name, "sort_order": sort_order}
        if before == after:
            return zone
        if zone.name != name:
            self._ensure_zone_name_free(zone.site_id, name, exclude=zone.id)
        zone.name = name
        zone.sort_order = sort_order
        self._flush()
        self._audit("updated", "restaurant_zone", zone, {"before": before, "after": after})
        return zone

    def set_zone_active(self, zone_id: uuid.UUID, active: bool) -> RestaurantZone:
        """Désactivation refusée tant que la zone contient des tables actives (comptées APRÈS le
        verrou exclusif : une table rattachée entre-temps a pris la zone en partage)."""
        zone = self._get_zone(zone_id, lock=True)
        if zone.is_active == active:
            return zone
        if not active:
            active_tables = self.db.scalar(
                select(func.count()).where(
                    RestaurantTable.zone_id == zone.id, RestaurantTable.is_active.is_(True)
                )
            )
            if active_tables:
                raise ConflictError(
                    "Cette zone contient des tables actives : déplacez-les ou désactivez-les "
                    "d'abord",
                    code="zone_has_active_tables",
                    extra={"active_tables": int(active_tables)},
                )
        zone.is_active = active
        self._flush()
        self._audit(
            "activated" if active else "deactivated", "restaurant_zone", zone, {"name": zone.name}
        )
        return zone

    # --- Tables : lecture ---------------------------------------------------------------------

    def _table_query(self) -> Select[Any]:
        return (
            select(
                RestaurantTable.id,
                RestaurantTable.site_id,
                Site.name.label("site_name"),
                RestaurantTable.zone_id,
                RestaurantZone.name.label("zone_name"),
                RestaurantZone.is_active.label("zone_active"),
                RestaurantTable.label,
                RestaurantTable.seats,
                RestaurantTable.sort_order,
                RestaurantTable.is_active,
                RestaurantTable.created_at,
                RestaurantTable.updated_at,
            )
            .join(
                Site,
                (Site.tenant_id == RestaurantTable.tenant_id)
                & (Site.id == RestaurantTable.site_id),
            )
            .outerjoin(
                RestaurantZone,
                (RestaurantZone.tenant_id == RestaurantTable.tenant_id)
                & (RestaurantZone.id == RestaurantTable.zone_id),
            )
            .where(RestaurantTable.tenant_id == self.ctx.tenant_id)
        )

    def search_tables(
        self,
        params: PageParams,
        *,
        search: str | None,
        status: StatusFilter,
        site_id: uuid.UUID | None,
        zone_id: uuid.UUID | None,
        without_zone: bool,
    ) -> tuple[list[TableRow], int]:
        stmt = self._table_query().where(
            RestaurantTable.site_id.in_(readable_site_ids(self.ctx, site_id))
        )
        condition = search_filter(search, RestaurantTable.label)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(RestaurantTable.is_active.is_(status is StatusFilter.ACTIVE))
        if zone_id is not None:
            stmt = stmt.where(RestaurantTable.zone_id == zone_id)
        if without_zone:
            stmt = stmt.where(RestaurantTable.zone_id.is_(None))
        if params.sort in (None, "position"):
            # Ordre du plan de salle : zones dans leur ordre (sans zone en dernier), puis tables.
            stmt = stmt.order_by(
                RestaurantZone.sort_order.asc().nulls_last(),
                text_sort(RestaurantZone.name),
                RestaurantTable.sort_order,
                text_sort(RestaurantTable.label),
                RestaurantTable.id,
            )
        else:
            sortable = {
                "position": RestaurantTable.sort_order,
                "label": text_sort(RestaurantTable.label),
                "zone": text_sort(RestaurantZone.name),
                "seats": RestaurantTable.seats,
                "site": text_sort(Site.name),
                "created_at": RestaurantTable.created_at,
            }
            stmt = apply_sort(stmt, params.sort, sortable, "position", RestaurantTable.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [TableRow(**row._mapping) for row in rows], total

    def table_row(self, table_id: uuid.UUID) -> TableRow:
        row = self.db.execute(self._table_query().where(RestaurantTable.id == table_id)).one()
        return TableRow(**row._mapping)

    def _get_table(self, table_id: uuid.UUID, *, lock: bool = False) -> RestaurantTable:
        stmt = select(RestaurantTable).where(RestaurantTable.id == table_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        table = self.db.scalars(stmt).one_or_none()
        if table is None:
            raise NotFoundError("Table introuvable", code=TABLE_NOT_FOUND)
        ensure_row_site(self.ctx, table.site_id, TABLE_NOT_FOUND)
        return table

    def get_table(self, table_id: uuid.UUID) -> TableRow:
        return self.table_row(self._get_table(table_id).id)

    # --- Tables : écriture --------------------------------------------------------------------

    def _active_zone_of_site(self, zone_id: uuid.UUID, site_id: uuid.UUID) -> RestaurantZone:
        """Zone ACTIVE du MÊME site, prise en partage (sa désactivation attend ; la FK composite
        garantit aussi le site en base)."""
        zone = self.db.scalars(
            select(RestaurantZone)
            .where(RestaurantZone.id == zone_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        if zone is None or zone.site_id != site_id:
            raise NotFoundError("Zone introuvable", code=ZONE_NOT_FOUND)
        if not zone.is_active:
            raise BusinessRuleError("Cette zone est désactivée", code="zone_inactive")
        return zone

    def _ensure_label_free(
        self, site_id: uuid.UUID, label: str, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = select(RestaurantTable.id).where(
            RestaurantTable.site_id == site_id, func.lower(RestaurantTable.label) == label.lower()
        )
        if exclude is not None:
            stmt = stmt.where(RestaurantTable.id != exclude)
        if self.db.scalar(stmt) is not None:
            raise ConflictError(TABLE_LABEL_TAKEN[1], code=TABLE_LABEL_TAKEN[0])

    def create_table(self, data: TableCreate) -> RestaurantTable:
        site = operation_site(self.ctx, data.site_id)
        if data.zone_id is not None:
            self._active_zone_of_site(data.zone_id, site)
        self._ensure_label_free(site, data.label)
        table = RestaurantTable(
            tenant_id=self.ctx.tenant_id,
            site_id=site,
            zone_id=data.zone_id,
            label=data.label,
            seats=data.seats,
            sort_order=data.sort_order,
        )
        self.db.add(table)
        self._flush()
        self._audit("created", "restaurant_table", table, _table_state(table))
        return table

    def update_table(self, table_id: uuid.UUID, data: TableUpdate) -> RestaurantTable:
        table = self._get_table(table_id, lock=True)
        before = _table_state(table)
        if data.zone_id is not None and data.zone_id != table.zone_id:
            self._active_zone_of_site(data.zone_id, table.site_id)
        if table.label != data.label:
            self._ensure_label_free(table.site_id, data.label, exclude=table.id)
        table.zone_id = data.zone_id
        table.label = data.label
        table.seats = data.seats
        table.sort_order = data.sort_order
        after = _table_state(table)
        if before == after:
            return table
        self._flush()
        self._audit("updated", "restaurant_table", table, {"before": before, "after": after})
        return table

    def set_table_active(self, table_id: uuid.UUID, active: bool) -> RestaurantTable:
        """Réactivation refusée dans une zone inactive ; désactivation refusée tant qu'une
        commande non finale y est rattachée (comptée APRÈS le verrou exclusif de la table : les
        écritures qui rattachent une table la prennent en partage, palier R5-B)."""
        table = self._get_table(table_id, lock=True)
        if table.is_active == active:
            return table
        if active and table.zone_id is not None:
            self._active_zone_of_site(table.zone_id, table.site_id)
        if not active:
            open_orders = open_orders_by_table(self.db, table.site_id, [table.id]).get(table.id, 0)
            if open_orders:
                raise ConflictError(
                    "Des commandes en cours sont rattachées à cette table : terminez-les ou "
                    "changez-les de table avant de la désactiver",
                    code="table_has_open_orders",
                    extra={"open_orders": open_orders},
                )
        table.is_active = active
        self._flush()
        self._audit(
            "activated" if active else "deactivated",
            "restaurant_table",
            table,
            {"label": table.label},
        )
        return table
