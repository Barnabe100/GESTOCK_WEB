"""Interface publique des commandes de restauration (palier R5-A) : utilisée par les modules qui
dépendent des commandes (``restaurant.tables``), qui ne lisent jamais leurs modèles (règle
d'architecture 10). Lecture seule."""

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.modules.restaurant_orders.models import ACTIVE_ORDER_STATUSES, RestaurantOrder
from app.platform.footprint import capped_count

__all__ = ["open_orders_by_table", "open_orders_on_tables"]


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
