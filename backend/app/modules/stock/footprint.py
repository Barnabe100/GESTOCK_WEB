"""Empreinte du stock sur un site (palier D) : lecture seule, tables du module seulement."""

import uuid
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.modules.stock.models import (
    DocumentStatus,
    StockEntry,
    StockExit,
    StockLevel,
    StockLocation,
    StockLotLevel,
    StockMovement,
    StockTransfer,
)
from app.platform.footprint import SiteFootprint, capped_count

_CLOSED = (DocumentStatus.VALIDATED, DocumentStatus.CANCELLED)


def stock_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    """Mouvements, documents clos, stock et soldes de lots non nuls (historique) ; documents
    brouillons, transferts compris côté source ou destination (ouverts, toujours compatibles :
    le stock est proposé par tous les profils)."""
    transfer_of_site = or_(
        StockTransfer.source_site_id == site_id, StockTransfer.destination_site_id == site_id
    )

    def count(stmt: Select[Any]) -> int:
        return capped_count(session, stmt)

    return SiteFootprint.of(
        history={
            "stock_movements": count(
                select(StockMovement.id).where(StockMovement.site_id == site_id)
            ),
            "stock_entries": count(
                select(StockEntry.id).where(
                    StockEntry.site_id == site_id, StockEntry.status.in_(_CLOSED)
                )
            ),
            "stock_exits": count(
                select(StockExit.id).where(
                    StockExit.site_id == site_id, StockExit.status.in_(_CLOSED)
                )
            ),
            "stock_transfers": count(
                select(StockTransfer.id).where(transfer_of_site, StockTransfer.status.in_(_CLOSED))
            ),
            "stock_levels": count(
                select(StockLevel.id).where(StockLevel.site_id == site_id, StockLevel.quantity != 0)
            ),
            "stock_lot_levels": count(
                select(StockLotLevel.id).where(
                    StockLotLevel.site_id == site_id, StockLotLevel.quantity != 0
                )
            ),
        },
        open={
            "stock_entries_draft": count(
                select(StockEntry.id).where(
                    StockEntry.site_id == site_id, StockEntry.status == DocumentStatus.DRAFT
                )
            ),
            "stock_exits_draft": count(
                select(StockExit.id).where(
                    StockExit.site_id == site_id, StockExit.status == DocumentStatus.DRAFT
                )
            ),
            "stock_transfers_draft": count(
                select(StockTransfer.id).where(
                    transfer_of_site, StockTransfer.status == DocumentStatus.DRAFT
                )
            ),
        },
        info={
            "stock_locations": count(
                select(StockLocation.id).where(StockLocation.site_id == site_id)
            )
        },
    )
