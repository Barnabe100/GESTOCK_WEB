"""Lecture du stock et de l'assortiment des sites pour les recettes (ADR-0053, D11 / D12) —
interfaces publiques du stock et du catalogue seulement ; aucune écriture."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog.api import assortment_view
from app.modules.stock.api import levels_view

Key = tuple[uuid.UUID, uuid.UUID]  # (site, article)


@dataclass(frozen=True)
class SiteStock:
    quantity: Decimal
    average_cost: Decimal


def site_stock(
    db: Session, tenant_id: uuid.UUID, site_ids: set[uuid.UUID], article_ids: set[uuid.UUID]
) -> dict[Key, SiteStock]:
    if not site_ids or not article_ids:
        return {}
    level = levels_view()
    rows = db.execute(
        select(level.c.site_id, level.c.article_id, level.c.quantity, level.c.average_cost).where(
            level.c.tenant_id == tenant_id,
            level.c.site_id.in_(site_ids),
            level.c.article_id.in_(article_ids),
        )
    ).tuples()
    return {
        (site, article): SiteStock(quantity=quantity, average_cost=cost)
        for site, article, quantity, cost in rows
    }


def site_assortment(
    db: Session, tenant_id: uuid.UUID, site_ids: set[uuid.UUID], article_ids: set[uuid.UUID]
) -> set[Key]:
    if not site_ids or not article_ids:
        return set()
    view = assortment_view()
    return set(
        db.execute(
            select(view.c.site_id, view.c.article_id).where(
                view.c.tenant_id == tenant_id,
                view.c.site_id.in_(site_ids),
                view.c.article_id.in_(article_ids),
            )
        ).tuples()
    )
