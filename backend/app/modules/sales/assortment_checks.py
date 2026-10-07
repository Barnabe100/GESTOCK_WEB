"""Contrôle du module Ventes pour le retrait d'un article de l'assortiment d'un site (Recette,
étape 1, ADR-0046, D4 ; port ``catalog.assortment_port``) : un brouillon de vente de CE site qui
contient l'article ne pourrait plus être validé — retrait refusé. Les ventes validées ou annulées
ne bloquent rien (historique)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog.api import RemovalBlocker, RemovalBlockerKind
from app.modules.sales.models import Sale, SaleLine, SaleStatus


def assortment_removal_check(
    db: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, article_ids: set[uuid.UUID]
) -> list[RemovalBlocker]:
    rows = db.execute(
        select(SaleLine.article_id, Sale.id)
        .join(Sale, (Sale.id == SaleLine.sale_id) & (Sale.tenant_id == SaleLine.tenant_id))
        .where(
            Sale.tenant_id == tenant_id,
            Sale.site_id == site_id,
            Sale.status == SaleStatus.DRAFT,
            SaleLine.article_id.in_(article_ids),
        )
        .distinct()
    ).all()
    # Brouillon non numéroté (numéro attribué à la validation, Lot 1) : identifiant court.
    return [
        RemovalBlocker(RemovalBlockerKind.OPEN_DOCUMENT, article_id, f"VENTE-{str(sale_id)[:8]}")
        for article_id, sale_id in rows
    ]
