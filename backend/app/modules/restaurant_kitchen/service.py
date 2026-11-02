"""Cuisine d'un site de restauration (palier R6, ADR-0050) : postes de préparation, affectations
du menu, priorité et poste des lignes, écran cuisine.

Contrôles du serveur :

- postes : nom unique par site insensible à la casse (base : ``409`` en concurrence), jamais
  supprimés ; désactivation refusée tant que des lignes non finales y sont routées
  (``409 station_has_open_lines``, comptées APRÈS le verrou exclusif du poste) ;
- affectations : section OU élément du menu du MÊME site, poste ACTIF du même site ; une
  affectation au plus par cible ; retirer une affectation supprime sa ligne (audit avant / après) ;
- priorité et poste d'une ligne : écrits par le SERVICE DES COMMANDES (``restaurant_orders.api``)
  sous ses verrous, avec son historique ; ce module valide et verrouille le poste visé ;
- écran : commandes ouvertes du site, ``restaurant.orders.order.view`` exigée sur CE site.

Verrous (ADR-0050 K6) : poste en PARTAGE (affectation, correction, routage par le port) ou en
EXCLUSIF (désactivation) ; correction : poste → commande → lignes. Ne valide pas la transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.modules.restaurant_kitchen.models import (
    ROUTE_ITEM_INDEX,
    ROUTE_SECTION_INDEX,
    STATION_NAME_INDEX,
    PrepStation,
    StationRoute,
)
from app.modules.restaurant_kitchen.permissions import LINE_ROUTE, PRIORITY_SET
from app.modules.restaurant_kitchen.schemas import (
    BoardCounts,
    BoardLineOut,
    BoardOut,
    BoardStationOut,
    BoardTicketOut,
    LinesRoute,
    PrioritySet,
    RouteOut,
    RouteSet,
    TicketState,
)
from app.modules.restaurant_kitchen.sites import (
    ensure_row_site,
    operation_site,
    readable_site_ids,
    screen_site,
)
from app.modules.restaurant_menu.api import menu_items_of_site, menu_section_ids_of_site
from app.modules.restaurant_orders.api import (
    ORDER_VIEW,
    PRIORITY_RANK,
    KitchenLine,
    OrderOut,
    StationRef,
    kitchen_lines,
    open_routed_lines,
    order_detail,
    route_order_lines,
    set_order_priority,
)
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort
from app.shared.schemas import StatusFilter

STATION_NAME_TAKEN = ("station_name_taken", "Un poste de ce nom existe déjà sur ce site")
STATION_NOT_FOUND = "station_not_found"
ROUTE_CHANGED = ("station_route_changed", "Affectation modifiée en même temps : réessayez")
# Ordre des colonnes de l'écran (ligne la MOINS avancée d'un ticket).
TICKET_ORDER = (TicketState.RECEIVED, TicketState.IN_PREPARATION, TicketState.READY)


@dataclass(frozen=True)
class StationRow:
    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    sort_order: int
    is_active: bool
    route_count: int
    created_at: datetime
    updated_at: datetime


def _ref(station: PrepStation) -> StationRef:
    return StationRef(
        id=station.id, site_id=station.site_id, name=station.name, is_active=station.is_active
    )


class KitchenService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Commun -------------------------------------------------------------------------------

    def _flush(self) -> None:
        """Les unicités font foi en base : une écriture concurrente échoue ici (``409``)."""
        try:
            self.db.flush()
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == STATION_NAME_INDEX:
                raise ConflictError(STATION_NAME_TAKEN[1], code=STATION_NAME_TAKEN[0]) from exc
            if constraint in (ROUTE_SECTION_INDEX, ROUTE_ITEM_INDEX):
                raise ConflictError(ROUTE_CHANGED[1], code=ROUTE_CHANGED[0]) from exc
            raise

    def _audit(
        self, action: str, entity_type: str, entity_id: uuid.UUID, site_id: uuid.UUID, data: Any
    ) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"{entity_type}.{action}",
            entity_type=entity_type,
            entity_id=entity_id,
            site_id=site_id,
            data=data,
        )

    # --- Postes : lecture ---------------------------------------------------------------------

    def _station_query(self) -> Select[Any]:
        counts = (
            select(StationRoute.station_id, func.count().label("route_count"))
            .group_by(StationRoute.station_id)
            .subquery("restaurant_station_route_counts")
        )
        return (
            select(
                PrepStation.id,
                PrepStation.site_id,
                Site.name.label("site_name"),
                PrepStation.name,
                PrepStation.sort_order,
                PrepStation.is_active,
                func.coalesce(counts.c.route_count, 0).label("route_count"),
                PrepStation.created_at,
                PrepStation.updated_at,
            )
            .join(
                Site, (Site.tenant_id == PrepStation.tenant_id) & (Site.id == PrepStation.site_id)
            )
            .outerjoin(counts, counts.c.station_id == PrepStation.id)
            .where(PrepStation.tenant_id == self.ctx.tenant_id)
        )

    def search_stations(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        site_id: uuid.UUID | None,
    ) -> tuple[list[StationRow], int]:
        stmt = self._station_query().where(
            PrepStation.site_id.in_(readable_site_ids(self.ctx, site_id))
        )
        condition = search_filter(search, PrepStation.name)
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(PrepStation.is_active.is_(status is StatusFilter.ACTIVE))
        if params.sort in (None, "position"):
            stmt = stmt.order_by(
                PrepStation.sort_order, text_sort(PrepStation.name), PrepStation.id
            )
        else:
            sortable = {
                "position": PrepStation.sort_order,
                "name": text_sort(PrepStation.name),
                "site": text_sort(Site.name),
                "created_at": PrepStation.created_at,
            }
            stmt = apply_sort(stmt, params.sort, sortable, "position", PrepStation.id)
        rows, total = paginate_rows(self.db, stmt, params)
        return [StationRow(**row._mapping) for row in rows], total

    def station_row(self, station_id: uuid.UUID) -> StationRow:
        row = self.db.execute(self._station_query().where(PrepStation.id == station_id)).one()
        return StationRow(**row._mapping)

    def _get_station(self, station_id: uuid.UUID, *, lock: str | None = None) -> PrepStation:
        """Poste d'un site lisible par le membre ; sinon introuvable (jamais révélé)."""
        stmt = select(PrepStation).where(
            PrepStation.tenant_id == self.ctx.tenant_id, PrepStation.id == station_id
        )
        if lock is not None:
            stmt = stmt.with_for_update(read=lock == "share").execution_options(
                populate_existing=True
            )
        station = self.db.scalars(stmt).one_or_none()
        if station is None:
            raise NotFoundError("Poste introuvable", code=STATION_NOT_FOUND)
        ensure_row_site(self.ctx, station.site_id, STATION_NOT_FOUND)
        return station

    def get_station(self, station_id: uuid.UUID) -> StationRow:
        return self.station_row(self._get_station(station_id).id)

    # --- Postes : écriture --------------------------------------------------------------------

    def _ensure_name_free(
        self, site_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None
    ) -> None:
        stmt = select(PrepStation.id).where(
            PrepStation.tenant_id == self.ctx.tenant_id,
            PrepStation.site_id == site_id,
            func.lower(PrepStation.name) == name.lower(),
        )
        if exclude is not None:
            stmt = stmt.where(PrepStation.id != exclude)
        if self.db.scalar(stmt) is not None:
            raise ConflictError(STATION_NAME_TAKEN[1], code=STATION_NAME_TAKEN[0])

    def create_station(self, site_id: uuid.UUID | None, name: str, sort_order: int) -> PrepStation:
        site = operation_site(self.ctx, site_id)
        self._ensure_name_free(site, name)
        station = PrepStation(
            tenant_id=self.ctx.tenant_id, site_id=site, name=name, sort_order=sort_order
        )
        self.db.add(station)
        self._flush()
        self._audit(
            "created",
            "restaurant_prep_station",
            station.id,
            site,
            {"name": name, "sort_order": sort_order},
        )
        return station

    def update_station(self, station_id: uuid.UUID, name: str, sort_order: int) -> PrepStation:
        station = self._get_station(station_id, lock="update")
        before = {"name": station.name, "sort_order": station.sort_order}
        after = {"name": name, "sort_order": sort_order}
        if before == after:
            return station
        if station.name != name:
            self._ensure_name_free(station.site_id, name, exclude=station.id)
        station.name = name
        station.sort_order = sort_order
        self._flush()
        self._audit(
            "updated",
            "restaurant_prep_station",
            station.id,
            station.site_id,
            {"before": before, "after": after},
        )
        return station

    def set_station_active(self, station_id: uuid.UUID, active: bool) -> PrepStation:
        """Désactivation refusée tant que des lignes non finales y sont routées (comptées APRÈS
        le verrou exclusif : une ligne routée entre-temps a pris le poste en partage)."""
        station = self._get_station(station_id, lock="update")
        if station.is_active == active:
            return station
        if not active:
            open_lines = open_routed_lines(self.db, station.site_id, station.id)
            if open_lines:
                raise ConflictError(
                    "Des lignes en cours sont routées vers ce poste : servez-les, annulez-les ou "
                    "changez-les de poste d'abord",
                    code="station_has_open_lines",
                    extra={"open_lines": open_lines},
                )
        station.is_active = active
        self._flush()
        self._audit(
            "activated" if active else "deactivated",
            "restaurant_prep_station",
            station.id,
            station.site_id,
            {"name": station.name},
        )
        return station

    # --- Affectations du menu -----------------------------------------------------------------

    def routes(self, site_id: uuid.UUID | None) -> list[RouteOut]:
        rows = self.db.execute(
            select(StationRoute, PrepStation.name, PrepStation.is_active)
            .join(
                PrepStation,
                (PrepStation.tenant_id == StationRoute.tenant_id)
                & (PrepStation.id == StationRoute.station_id),
            )
            .where(
                StationRoute.tenant_id == self.ctx.tenant_id,
                StationRoute.site_id.in_(readable_site_ids(self.ctx, site_id)),
            )
            .order_by(StationRoute.created_at, StationRoute.id)
        ).tuples()
        return [
            RouteOut(
                id=route.id,
                site_id=route.site_id,
                station_id=route.station_id,
                station_name=name,
                station_active=active,
                section_id=route.section_id,
                menu_item_id=route.menu_item_id,
                updated_at=route.updated_at,
            )
            for route, name, active in rows
        ]

    def set_route(self, data: RouteSet) -> RouteOut | None:
        """Affecte la section ou l'élément du menu à un poste actif du site (``station_id`` nul :
        retire l'affectation). Même poste : rien ne change."""
        site = operation_site(self.ctx, data.site_id)
        if data.section_id is not None:
            if data.section_id not in menu_section_ids_of_site(
                self.db, self.ctx, site, {data.section_id}
            ):
                raise NotFoundError("Section du menu introuvable", code="menu_section_not_found")
            target = StationRoute.section_id == data.section_id
            target_data = {"section_id": str(data.section_id)}
        else:
            assert data.menu_item_id is not None  # garanti par le schéma
            if not menu_items_of_site(self.db, self.ctx, site, {data.menu_item_id}):
                raise NotFoundError("Élément du menu introuvable", code="menu_item_not_found")
            target = StationRoute.menu_item_id == data.menu_item_id
            target_data = {"menu_item_id": str(data.menu_item_id)}
        station: PrepStation | None = None
        if data.station_id is not None:
            station = self._get_station(data.station_id, lock="share")
            if station.site_id != site:
                raise NotFoundError("Poste introuvable", code=STATION_NOT_FOUND)
            if not station.is_active:
                raise BusinessRuleError("Ce poste est désactivé", code="station_inactive")
        route = self.db.scalars(
            select(StationRoute)
            .where(
                StationRoute.tenant_id == self.ctx.tenant_id, StationRoute.site_id == site, target
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        previous = str(route.station_id) if route is not None else None
        if station is None:
            if route is None:
                return None
            self.db.delete(route)
            self._flush()
            self._audit(
                "removed",
                "restaurant_station_route",
                route.id,
                site,
                {**target_data, "previous_station_id": previous},
            )
            return None
        if route is not None and route.station_id == station.id:
            return self._route_out(route, station)
        if route is None:
            route = StationRoute(
                tenant_id=self.ctx.tenant_id,
                site_id=site,
                station_id=station.id,
                section_id=data.section_id,
                menu_item_id=data.menu_item_id,
            )
            self.db.add(route)
        else:
            route.station_id = station.id
        self._flush()
        self._audit(
            "set",
            "restaurant_station_route",
            route.id,
            site,
            {
                **target_data,
                "previous_station_id": previous,
                "station_id": str(station.id),
                "station_name": station.name,
            },
        )
        return self._route_out(route, station)

    @staticmethod
    def _route_out(route: StationRoute, station: PrepStation) -> RouteOut:
        return RouteOut(
            id=route.id,
            site_id=route.site_id,
            station_id=station.id,
            station_name=station.name,
            station_active=station.is_active,
            section_id=route.section_id,
            menu_item_id=route.menu_item_id,
            updated_at=route.updated_at,
        )

    # --- Commandes : priorité et poste des lignes ---------------------------------------------

    def order(self, order_id: uuid.UUID) -> OrderOut:
        """Commande telle que l'API des commandes la renvoie (après validation)."""
        return order_detail(self.db, self.ctx, self.now, order_id)

    def set_priority(self, order_id: uuid.UUID, data: PrioritySet) -> None:
        set_order_priority(
            self.db, self.ctx, self.now, order_id, data.priority, data.reason, PRIORITY_SET
        )

    def route_lines(self, order_id: uuid.UUID, data: LinesRoute) -> None:
        """Poste ACTIF du même site que la commande (contrôlé par les commandes), pris en
        PARTAGE avant la commande ; ``None`` : « Sans poste »."""
        station: StationRef | None = None
        if data.station_id is not None:
            found = self._get_station(data.station_id, lock="share")
            if not found.is_active:
                raise BusinessRuleError("Ce poste est désactivé", code="station_inactive")
            station = _ref(found)
        route_order_lines(
            self.db, self.ctx, self.now, order_id, data.line_ids, station, data.reason, LINE_ROUTE
        )

    # --- Écran cuisine ------------------------------------------------------------------------

    def board(
        self, site_id: uuid.UUID | None, station_id: uuid.UUID | None, unrouted: bool
    ) -> BoardOut:
        """Tickets du site (commande ouverte × poste), classés par priorité décroissante PUIS
        ancienneté ; filtre facultatif par poste ou « Sans poste »."""
        site = screen_site(self.ctx, site_id)
        if not self.ctx.has_site_permission(site, ORDER_VIEW):
            raise ForbiddenError("Permission insuffisante", code="permission_denied")
        stations = list(
            self.db.scalars(
                select(PrepStation)
                .where(PrepStation.tenant_id == self.ctx.tenant_id, PrepStation.site_id == site)
                .order_by(PrepStation.sort_order, text_sort(PrepStation.name), PrepStation.id)
            )
        )
        names = {s.id: s.name for s in stations}
        lines = kitchen_lines(self.db, self.ctx, site)
        used = {line.station_id for line in lines if line.station_id is not None}
        groups: dict[tuple[uuid.UUID, uuid.UUID | None], list[KitchenLine]] = {}
        for line in lines:
            if unrouted and line.station_id is not None:
                continue
            if station_id is not None and line.station_id != station_id:
                continue
            groups.setdefault((line.order_id, line.station_id), []).append(line)
        tickets = [self._ticket(group, names) for group in groups.values()]
        tickets.sort(
            key=lambda t: (
                -PRIORITY_RANK[t.priority],
                t.order_created_at,
                t.daily_number,
                (names.get(t.station_id) or "") if t.station_id else "",
            )
        )
        counts = BoardCounts(
            received=sum(t.state is TicketState.RECEIVED for t in tickets),
            in_preparation=sum(t.state is TicketState.IN_PREPARATION for t in tickets),
            ready=sum(t.state is TicketState.READY for t in tickets),
        )
        site_name = self.db.scalar(select(Site.name).where(Site.id == site)) or ""
        return BoardOut(
            site_id=site,
            site_name=site_name,
            stations=[
                BoardStationOut(id=s.id, name=s.name, is_active=s.is_active)
                for s in stations
                if s.is_active or s.id in used
            ],
            counts=counts,
            tickets=tickets,
        )

    @staticmethod
    def _ticket(group: list[KitchenLine], names: dict[uuid.UUID, str]) -> BoardTicketOut:
        first = group[0]
        statuses = {line.status.value for line in group}
        state = next(s for s in TICKET_ORDER if s.value in statuses)
        return BoardTicketOut(
            order_id=first.order_id,
            daily_number=first.daily_number,
            business_date=first.business_date,
            service_mode=first.service_mode.value,
            table_label=first.table_label,
            call_name=first.call_name,
            priority=first.priority,
            assigned_name=first.assigned_name,
            awaiting_payment=first.awaiting_payment,
            station_id=first.station_id,
            station_name=names.get(first.station_id) if first.station_id else None,
            state=state,
            received_at=min(line.received_at for line in group),
            order_created_at=first.order_created_at,
            lines=[
                BoardLineOut(
                    id=line.line_id,
                    line_no=line.line_no,
                    label=line.label,
                    packaging_name=line.packaging_name,
                    quantity=line.quantity,
                    unit=line.unit,
                    note=line.note,
                    status=line.status.value,
                    received_at=line.received_at,
                    prepared_at=line.prepared_at,
                    ready_at=line.ready_at,
                    article_id=line.article_id,
                    packaging_id=line.packaging_id,
                )
                for line in group
            ],
        )
