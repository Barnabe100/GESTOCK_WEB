"""Interface publique des commandes de restauration (palier R5-A) : utilisée par les modules qui
dépendent des commandes (``restaurant.tables``), qui ne lisent jamais leurs modèles (règle
d'architecture 10). Lecture seule, plus l'enregistrement du port des tables (R5-B)."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.modules.restaurant_orders.models import (
    ACTIVE_ORDER_STATUSES,
    OrderStatus,
    PrepStatus,
    RestaurantOrder,
    SettlementStatus,
)
from app.modules.restaurant_orders.permissions import ORDER_VIEW
from app.modules.restaurant_orders.table_port import (
    TableDirectory,
    TableRef,
    register_table_directory,
)
from app.platform.footprint import capped_count

__all__ = [
    "ORDER_VIEW",
    "OccupyingOrder",
    "TableDirectory",
    "TableRef",
    "occupying_orders",
    "open_orders_by_table",
    "open_orders_on_tables",
    "register_table_directory",
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
