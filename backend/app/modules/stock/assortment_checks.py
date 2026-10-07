"""Contrôles du module Stock pour le retrait d'un article de l'assortiment d'un site (Recette,
étape 1, ADR-0046, D4 ; port ``catalog.assortment_port``) — sur CE site seulement :

- **stock** : niveau ou solde de lot non nul (le stock ne doit jamais devenir invisible) ;
- **documents ouverts** : brouillon d'entrée ou de sortie du site, brouillon de transfert dont le
  site est la source OU la destination (ils ne pourraient plus être validés).
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.catalog.api import RemovalBlocker, RemovalBlockerKind
from app.modules.stock.models import (
    DocumentStatus,
    StockEntry,
    StockEntryLine,
    StockExit,
    StockExitLine,
    StockLevel,
    StockLotLevel,
    StockTransfer,
    StockTransferLine,
)


def assortment_removal_check(
    db: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, article_ids: set[uuid.UUID]
) -> list[RemovalBlocker]:
    blockers: list[RemovalBlocker] = []
    stocked = set(
        db.scalars(
            select(StockLevel.article_id).where(
                StockLevel.tenant_id == tenant_id,
                StockLevel.site_id == site_id,
                StockLevel.article_id.in_(article_ids),
                StockLevel.quantity != 0,
            )
        )
    ) | set(
        db.scalars(
            select(StockLotLevel.article_id).where(
                StockLotLevel.tenant_id == tenant_id,
                StockLotLevel.site_id == site_id,
                StockLotLevel.article_id.in_(article_ids),
                StockLotLevel.quantity != 0,
            )
        )
    )
    blockers.extend(RemovalBlocker(RemovalBlockerKind.STOCK, a) for a in sorted(stocked))
    drafts = [
        select(StockEntryLine.article_id, StockEntry.number)
        .join(StockEntry, StockEntry.id == StockEntryLine.entry_id)
        .where(
            StockEntry.tenant_id == tenant_id,
            StockEntry.site_id == site_id,
            StockEntry.status == DocumentStatus.DRAFT,
            StockEntryLine.article_id.in_(article_ids),
        ),
        select(StockExitLine.article_id, StockExit.number)
        .join(StockExit, StockExit.id == StockExitLine.exit_id)
        .where(
            StockExit.tenant_id == tenant_id,
            StockExit.site_id == site_id,
            StockExit.status == DocumentStatus.DRAFT,
            StockExitLine.article_id.in_(article_ids),
        ),
        select(StockTransferLine.article_id, StockTransfer.number)
        .join(StockTransfer, StockTransfer.id == StockTransferLine.transfer_id)
        .where(
            StockTransfer.tenant_id == tenant_id,
            or_(
                StockTransfer.source_site_id == site_id,
                StockTransfer.destination_site_id == site_id,
            ),
            StockTransfer.status == DocumentStatus.DRAFT,
            StockTransferLine.article_id.in_(article_ids),
        ),
    ]
    for stmt in drafts:
        blockers.extend(
            RemovalBlocker(RemovalBlockerKind.OPEN_DOCUMENT, article_id, number)
            for article_id, number in db.execute(stmt.distinct())
        )
    return blockers
