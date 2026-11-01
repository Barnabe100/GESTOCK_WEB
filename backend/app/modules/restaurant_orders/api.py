"""Interface publique des commandes de restauration (palier R5-A) : utilisée par les modules qui
dépendent des commandes (``restaurant.tables``, ``restaurant.kitchen``), qui ne lisent jamais
leurs modèles (règle d'architecture 10). Lecture, ports des tables (R5-B) et des postes (R6), et
les deux écritures de la cuisine (priorité, poste d'une ligne), exécutées par le service des
commandes sous ses verrous, avec son historique et son audit."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.modules.restaurant_orders.models import (
    ACTIVE_ORDER_STATUSES,
    FINAL_LINE_STATUSES,
    PRIORITY_RANK,
    LineStatus,
    OrderPriority,
    OrderStatus,
    PaymentTiming,
    PrepStatus,
    RestaurantOrder,
    RestaurantOrderLine,
    ServiceMode,
    SettlementStatus,
)
from app.modules.restaurant_orders.permissions import ORDER_PREPARE, ORDER_VIEW
from app.modules.restaurant_orders.schemas import OrderOut
from app.modules.restaurant_orders.station_port import (
    StationRef,
    StationRouter,
    register_station_router,
)
from app.modules.restaurant_orders.table_port import (
    TableDirectory,
    TableRef,
    register_table_directory,
    table_directory,
)
from app.platform.context import RequestContext
from app.platform.footprint import capped_count
from app.platform.identity.models import User

__all__ = [
    "ORDER_PREPARE",
    "ORDER_VIEW",
    "PRIORITY_RANK",
    "KitchenLine",
    "OccupyingOrder",
    "OrderOut",
    "OrderPriority",
    "StationRef",
    "StationRouter",
    "TableDirectory",
    "TableRef",
    "kitchen_lines",
    "occupying_orders",
    "open_orders_by_table",
    "open_orders_on_tables",
    "open_routed_lines",
    "order_detail",
    "register_station_router",
    "register_table_directory",
    "route_order_lines",
    "set_order_priority",
    "table_directory",
]


def _open_with_table(site_id: uuid.UUID) -> Select[Any]:
    return select(RestaurantOrder.id).where(
        RestaurantOrder.site_id == site_id,
        RestaurantOrder.table_id.is_not(None),
        RestaurantOrder.status.in_(ACTIVE_ORDER_STATUSES),
    )


def open_orders_on_tables(session: Session, site_id: uuid.UUID) -> int:
    """Commandes non finales (à confirmer, ouvertes) rattachées à une table du site (comptage
    plafonné, empreinte du module des tables)."""
    return capped_count(session, _open_with_table(site_id))


def open_orders_by_table(
    session: Session, site_id: uuid.UUID, table_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Nombre de commandes non finales par table du site (tables sans commande : absentes)."""
    ids = set(table_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(RestaurantOrder.table_id, func.count())
        .where(
            RestaurantOrder.site_id == site_id,
            RestaurantOrder.table_id.in_(ids),
            RestaurantOrder.status.in_(ACTIVE_ORDER_STATUSES),
        )
        .group_by(RestaurantOrder.table_id)
    ).tuples()
    return {table_id: count for table_id, count in rows if table_id is not None}


@dataclass(frozen=True)
class OccupyingOrder:
    """Commande qui OCCUPE sa table (R5, B1 / C2) : à confirmer ou ouverte, rattachée à une table.

    ``to_settle`` : ouverte, NON réglée et tout servi (lignes non annulées toutes servies) — elle
    n'attend plus que son règlement. Toute autre commande occupante est « en cours » (lignes à
    préparer ou à servir, réglée non servie, à confirmer, sans ligne active). Annulée, refusée
    ou close : la commande n'occupe plus sa table (absente)."""

    id: uuid.UUID
    table_id: uuid.UUID
    business_date: date
    daily_number: int
    call_name: str | None
    status: str
    prep_status: str
    settlement_status: str
    created_at: datetime

    @property
    def to_settle(self) -> bool:
        return (
            self.status == OrderStatus.OPEN
            and self.settlement_status == SettlementStatus.UNSETTLED
            and self.prep_status == PrepStatus.SERVED
        )


def occupying_orders(session: Session, site_id: uuid.UUID) -> list[OccupyingOrder]:
    """Commandes qui occupent une table du site (plan de salle), de la plus ancienne à la plus
    récente ; calculées à la lecture, jamais enregistrées."""
    rows = session.execute(
        select(
            RestaurantOrder.id,
            RestaurantOrder.table_id,
            RestaurantOrder.business_date,
            RestaurantOrder.daily_number,
            RestaurantOrder.call_name,
            RestaurantOrder.status,
            RestaurantOrder.prep_status,
            RestaurantOrder.settlement_status,
            RestaurantOrder.created_at,
        )
        .where(
            RestaurantOrder.site_id == site_id,
            RestaurantOrder.table_id.is_not(None),
            RestaurantOrder.status.in_(ACTIVE_ORDER_STATUSES),
        )
        .order_by(RestaurantOrder.created_at, RestaurantOrder.id)
    ).tuples()
    return [
        OccupyingOrder(
            id=order_id,
            table_id=table_id,
            business_date=day,
            daily_number=number,
            call_name=call_name,
            status=str(status),
            prep_status=str(prep),
            settlement_status=str(settlement),
            created_at=created_at,
        )
        for order_id, table_id, day, number, call_name, status, prep, settlement, created_at in rows
        if table_id is not None
    ]


# --- Cuisine (R6, ADR-0050) ----------------------------------------------------------------------


def open_routed_lines(
    session: Session, site_id: uuid.UUID, station_id: uuid.UUID | None = None
) -> int:
    """Lignes NON finales routées vers un poste du site (ou vers ``station_id``) : empreinte du
    module de la cuisine et refus de désactiver un poste (comptage plafonné)."""
    stmt = select(RestaurantOrderLine.id).where(
        RestaurantOrderLine.site_id == site_id,
        RestaurantOrderLine.station_id.is_not(None),
        RestaurantOrderLine.status.not_in(FINAL_LINE_STATUSES),
    )
    if station_id is not None:
        stmt = stmt.where(RestaurantOrderLine.station_id == station_id)
    return capped_count(session, stmt)


@dataclass(frozen=True)
class KitchenLine:
    """Ligne à préparer ou à servir d'une commande OUVERTE (écran cuisine, lecture seule)."""

    order_id: uuid.UUID
    daily_number: int
    business_date: date
    service_mode: ServiceMode
    table_label: str | None
    call_name: str | None
    priority: OrderPriority
    order_created_at: datetime
    assigned_name: str | None
    # Paiement à la commande non réglé : préparation refusée par le serveur.
    awaiting_payment: bool
    line_id: uuid.UUID
    line_no: int
    label: str
    packaging_name: str | None
    quantity: Decimal
    unit: str
    note: str | None
    status: LineStatus
    station_id: uuid.UUID | None
    received_at: datetime
    prepared_at: datetime | None
    ready_at: datetime | None


def kitchen_lines(db: Session, ctx: RequestContext, site_id: uuid.UUID) -> list[KitchenLine]:
    """Lignes reçues, en préparation ou prêtes des commandes ouvertes du site (le site est
    contrôlé par l'appelant : ``restaurant.orders.order.view`` sur CE site)."""
    rows = db.execute(
        select(RestaurantOrder, RestaurantOrderLine, User.full_name)
        .join(
            RestaurantOrderLine,
            (RestaurantOrderLine.tenant_id == RestaurantOrder.tenant_id)
            & (RestaurantOrderLine.order_id == RestaurantOrder.id),
        )
        .outerjoin(User, User.id == RestaurantOrder.assigned_user_id)
        .where(
            RestaurantOrder.tenant_id == ctx.tenant_id,
            RestaurantOrder.site_id == site_id,
            RestaurantOrder.status == OrderStatus.OPEN,
            RestaurantOrderLine.status.in_(
                (LineStatus.RECEIVED, LineStatus.IN_PREPARATION, LineStatus.READY)
            ),
        )
        .order_by(RestaurantOrder.created_at, RestaurantOrder.id, RestaurantOrderLine.line_no)
    ).tuples()
    data = list(rows)
    table_ids = {order.table_id for order, _, _ in data if order.table_id is not None}
    directory = table_directory(db, ctx) if table_ids else None
    tables = directory.labels(table_ids) if directory is not None else {}
    result: list[KitchenLine] = []
    for order, line, assigned_name in data:
        table = tables.get(order.table_id) if order.table_id is not None else None
        result.append(
            KitchenLine(
                order_id=order.id,
                daily_number=order.daily_number,
                business_date=order.business_date,
                service_mode=order.service_mode,
                table_label=table.label if table is not None else None,
                call_name=order.call_name,
                priority=order.priority,
                order_created_at=order.created_at,
                assigned_name=assigned_name,
                awaiting_payment=order.payment_timing is PaymentTiming.AT_ORDER
                and order.settlement_status is not SettlementStatus.SETTLED,
                line_id=line.id,
                line_no=line.line_no,
                label=line.label,
                packaging_name=line.packaging_name,
                quantity=line.quantity,
                unit=line.unit,
                note=line.note,
                status=line.status,
                station_id=line.station_id,
                received_at=line.created_at,
                prepared_at=line.prepared_at,
                ready_at=line.ready_at,
            )
        )
    return result


def _service(db: Session, ctx: RequestContext, now: datetime) -> Any:
    from app.modules.restaurant_orders.service import OrderService  # import local : aucun cycle

    return OrderService(db, ctx, now)


def set_order_priority(
    db: Session,
    ctx: RequestContext,
    now: datetime,
    order_id: uuid.UUID,
    priority: OrderPriority,
    reason: str | None,
    permission: str,
) -> None:
    """Priorité d'une commande ouverte (permission du module appelant, revérifiée sur le site)."""
    _service(db, ctx, now).set_priority(order_id, priority, reason, permission)


def route_order_lines(
    db: Session,
    ctx: RequestContext,
    now: datetime,
    order_id: uuid.UUID,
    line_ids: Sequence[uuid.UUID],
    station: StationRef | None,
    reason: str | None,
    permission: str,
) -> None:
    """Poste de lignes non finales d'une commande ouverte (poste validé par l'appelant)."""
    _service(db, ctx, now).route_lines(order_id, line_ids, station, reason, permission)


def order_detail(db: Session, ctx: RequestContext, now: datetime, order_id: uuid.UUID) -> OrderOut:
    """Commande telle que l'API des commandes la renvoie (lignes comprises)."""
    detail: OrderOut = _service(db, ctx, now).detail(order_id)
    return detail
