"""Contrôles du module Stock pour le changement de suivi par lot d'un article (Lot 3-H, port
``catalog.lot_flags_port``) :

- **documents ouverts** : brouillon de réception portant un numéro de lot, brouillon de sortie
  ou de transfert portant une répartition par lot — ils deviendraient incohérents ;
- **historique sans lot** (activation seulement) : document validé encore annulable (vente,
  réception, sortie, transfert) dont un mouvement de l'article n'a pas de lot et n'a pas été
  annulé — son annulation exigerait un lot qu'il n'a jamais eu (limitation 3-H-A).
"""

import uuid

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, aliased

from app.modules.catalog.api import BlockerKind, LotFlagsBlocker
from app.modules.stock.models import (
    DocumentStatus,
    MovementType,
    StockEntry,
    StockEntryLine,
    StockExit,
    StockExitLine,
    StockExitLineLot,
    StockMovement,
    StockTransfer,
    StockTransferLine,
    StockTransferLineLot,
)

# Sources de mouvements dont le document validé peut encore être annulé (un inventaire validé
# est immuable : ses ajustements ne s'annulent jamais).
CANCELLABLE_SOURCES = ("sale", "stock_entry", "stock_exit", "stock_transfer")


def lot_flags_check(
    db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID, enabling: bool
) -> list[LotFlagsBlocker]:
    blockers: list[LotFlagsBlocker] = []
    drafts = [
        select(StockEntry.number)
        .join(StockEntryLine, StockEntryLine.entry_id == StockEntry.id)
        .where(
            StockEntry.status == DocumentStatus.DRAFT,
            StockEntryLine.article_id == article_id,
            StockEntryLine.lot_number.is_not(None),
        ),
        select(StockExit.number)
        .join(StockExitLine, StockExitLine.exit_id == StockExit.id)
        .join(StockExitLineLot, StockExitLineLot.exit_line_id == StockExitLine.id)
        .where(StockExit.status == DocumentStatus.DRAFT, StockExitLine.article_id == article_id),
        select(StockTransfer.number)
        .join(StockTransferLine, StockTransferLine.transfer_id == StockTransfer.id)
        .join(StockTransferLineLot, StockTransferLineLot.transfer_line_id == StockTransferLine.id)
        .where(
            StockTransfer.status == DocumentStatus.DRAFT,
            StockTransferLine.article_id == article_id,
        ),
    ]
    for stmt in drafts:
        blockers.extend(
            LotFlagsBlocker(BlockerKind.OPEN_DOCUMENT, number)
            for number in db.scalars(stmt.distinct().limit(20))
        )
    if not enabling:
        return blockers
    cancellation = aliased(StockMovement)
    history = db.scalars(
        select(StockMovement.source_number)
        .where(
            StockMovement.article_id == article_id,
            StockMovement.lot_id.is_(None),
            StockMovement.movement_type != MovementType.CANCELLATION,
            StockMovement.source_type.in_(CANCELLABLE_SOURCES),
            ~exists().where(
                and_(
                    cancellation.source_id == StockMovement.source_id,
                    cancellation.article_id == StockMovement.article_id,
                    cancellation.movement_type == MovementType.CANCELLATION,
                )
            ),
        )
        .distinct()
        .limit(20)
    )
    blockers.extend(
        LotFlagsBlocker(BlockerKind.UNTRACKED_HISTORY, number or "?") for number in history
    )
    return blockers
