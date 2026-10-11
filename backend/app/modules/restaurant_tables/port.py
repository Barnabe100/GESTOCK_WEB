"""Implémentation du port « tables » des commandes (palier R5-B ; port déclaré par
``restaurant_orders/table_port.py``, enregistré par le manifeste de ce module).

Verrous (``RESTAURANT.md`` §7, R5) : la table visée est prise en PARTAGE par l'écriture qui la
rattache à une commande, après le verrou du site (partage de clé) et les réglages du site ; sa
désactivation (``TableService.set_table_active``) la prend en EXCLUSIF puis compte les commandes
ouvertes : aucune commande n'est jamais rattachée à une table devenue inactive. La zone n'est pas
verrouillée (seulement lue pour son nom)."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, NotFoundError
from app.modules.restaurant_orders.api import TableRef
from app.modules.restaurant_tables.models import RestaurantTable, RestaurantZone
from app.platform.context import RequestContext

TABLE_NOT_FOUND = "table_not_found"


class TableDirectoryService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def _zone_name(self, zone_id: uuid.UUID | None) -> str | None:
        if zone_id is None:
            return None
        return self.db.scalar(select(RestaurantZone.name).where(RestaurantZone.id == zone_id))

    def lock_for_order(self, site_id: uuid.UUID, table_id: uuid.UUID) -> TableRef:
        table = self.db.scalars(
            select(RestaurantTable)
            .where(
                RestaurantTable.tenant_id == self.ctx.tenant_id,
                RestaurantTable.id == table_id,
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        ).one_or_none()
        # Table d'un autre site : jamais révélée (la clé étrangère composite le garantit aussi).
        if table is None or table.site_id != site_id:
            raise NotFoundError("Table introuvable", code=TABLE_NOT_FOUND)
        if not table.is_active:
            raise BusinessRuleError("Cette table est désactivée", code="table_inactive")
        return TableRef(
            id=table.id,
            site_id=table.site_id,
            label=table.label,
            zone_name=self._zone_name(table.zone_id),
            is_active=table.is_active,
        )

    def labels(self, table_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, TableRef]:
        ids = set(table_ids)
        if not ids:
            return {}
        rows = self.db.execute(
            select(
                RestaurantTable.id,
                RestaurantTable.site_id,
                RestaurantTable.label,
                RestaurantZone.name,
                RestaurantTable.is_active,
            )
            .outerjoin(
                RestaurantZone,
                (RestaurantZone.tenant_id == RestaurantTable.tenant_id)
                & (RestaurantZone.id == RestaurantTable.zone_id),
            )
            .where(RestaurantTable.tenant_id == self.ctx.tenant_id, RestaurantTable.id.in_(ids))
        ).tuples()
        return {
            table_id: TableRef(
                id=table_id, site_id=site, label=label, zone_name=zone, is_active=active
            )
            for table_id, site, label, zone, active in rows
        }
