"""Empreinte des commandes sur un site (palier D) et ports du catalogue (palier R2-B).

Empreinte (lecture seule, tables du module seulement) :
- ``active`` : commandes non finales — travail en cours : un changement de profil qui ferait
  disparaître le module est BLOQUÉ, la désactivation du module est refusée
  (``module_has_open_operations``) ;
- ``history`` : commandes closes, annulées ou refusées (STRONG, jamais bloquant) ;
- ``info`` : réglages du site (configuration).
Sous une reconfiguration (``lock``), les réglages du site sont pris en exclusif : la création
d'une commande les prend en partage, elle attend donc la fin de la reconfiguration.

Ports du catalogue : les commandes non finales comptent comme des documents ouverts (retrait de
l'assortiment, changement du suivi par lot) ; toute ligne fige la conversion de son
conditionnement.
"""

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.modules.catalog.api import (
    BlockerKind,
    LotFlagsBlocker,
    RemovalBlocker,
    RemovalBlockerKind,
)
from app.modules.restaurant_orders.models import (
    ACTIVE_ORDER_STATUSES,
    FINAL_LINE_STATUSES,
    FINAL_ORDER_STATUSES,
    RestaurantOrder,
    RestaurantOrderLine,
    RestaurantSiteSettings,
)
from app.platform.footprint import SiteFootprint, capped_count


def orders_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    if lock:
        session.execute(
            select(RestaurantSiteSettings.id)
            .where(RestaurantSiteSettings.site_id == site_id)
            .with_for_update()
        )
    orders = select(RestaurantOrder.id).where(RestaurantOrder.site_id == site_id)
    return SiteFootprint.of(
        active={
            "restaurant_orders": capped_count(
                session, orders.where(RestaurantOrder.status.in_(ACTIVE_ORDER_STATUSES))
            )
        },
        history={
            "restaurant_orders": capped_count(
                session, orders.where(RestaurantOrder.status.in_(FINAL_ORDER_STATUSES))
            )
        },
        info={
            "restaurant_order_settings": capped_count(
                session,
                select(RestaurantSiteSettings.id).where(RestaurantSiteSettings.site_id == site_id),
            )
        },
    )


def _label(order_date: object, number: int) -> str:
    return f"#{number} ({order_date})"


def _open_lines() -> Select[Any]:
    return (
        select(
            RestaurantOrderLine.article_id,
            RestaurantOrder.business_date,
            RestaurantOrder.daily_number,
        )
        .join(
            RestaurantOrder,
            (RestaurantOrder.tenant_id == RestaurantOrderLine.tenant_id)
            & (RestaurantOrder.id == RestaurantOrderLine.order_id),
        )
        .where(
            RestaurantOrder.status.in_(ACTIVE_ORDER_STATUSES),
            RestaurantOrderLine.status.not_in(FINAL_LINE_STATUSES),
        )
        .distinct()
    )


def assortment_removal_check(
    db: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, article_ids: set[uuid.UUID]
) -> list[RemovalBlocker]:
    """Un article d'une commande non finale de ce site ne quitte pas l'assortiment (elle ne
    pourrait plus être réglée)."""
    rows = db.execute(
        _open_lines().where(
            RestaurantOrder.tenant_id == tenant_id,
            RestaurantOrder.site_id == site_id,
            RestaurantOrderLine.article_id.in_(article_ids),
        )
    ).all()
    return [
        RemovalBlocker(RemovalBlockerKind.OPEN_DOCUMENT, article_id, _label(day, number))
        for article_id, day, number in rows
    ]


def lot_flags_check(
    db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID, enabling: bool
) -> list[LotFlagsBlocker]:
    """Un article d'une commande non finale ne change pas de suivi par lot ou de péremption."""
    rows = db.execute(
        _open_lines()
        .where(
            RestaurantOrder.tenant_id == tenant_id,
            RestaurantOrderLine.article_id == article_id,
        )
        .limit(20)
    ).all()
    return [
        LotFlagsBlocker(BlockerKind.OPEN_DOCUMENT, _label(day, number)) for _, day, number in rows
    ]


def packagings_used(db: Session, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Conditionnements utilisés par au moins une ligne de commande (conversion figée)."""
    if not ids:
        return set()
    return set(
        db.scalars(
            select(RestaurantOrderLine.packaging_id)
            .where(RestaurantOrderLine.packaging_id.in_(ids))
            .distinct()
        )
    )
