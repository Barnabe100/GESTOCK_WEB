"""API publique du module Stock pour les autres modules (alertes, ventes, rapports…), qui
n'importent jamais ses modèles directement (règle d'architecture 10). Toute modification du
stock passe par ``StockService`` (règle d'architecture 5)."""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.stock.level_service import (
    LevelRow,
    LevelState,
    StateFilter,
    count_alerts,
    levels_view,
    list_levels,
)
from app.modules.stock.location_service import locations_view
from app.modules.stock.lot_service import (
    ExpiryContext,
    LotAllocation,
    LotInfo,
    available_lots,
    expiry_context,
    is_expired_lot,
    lot_allocations,
    lot_infos,
)
from app.modules.stock.models import MovementType
from app.modules.stock.schemas import AvailableLotOut, AvailableLotsOut, LevelOut
from app.modules.stock.sites import (
    ensure_document_site,
    filter_site_ids,
    operation_site,
    sees_all_sites,
    tenant_today,
    visible_site_ids,
)
from app.modules.stock.stock_service import (
    ConsumptionRequest,
    LotPick,
    MovementRef,
    MovementRequest,
    PackagingSnapshot,
    StockService,
    inverse_packaging,
    refuse_unmanaged,
    round_money,
)
from app.platform.context import RequestContext

__all__ = [
    "AvailableLotsOut",
    "ConsumptionRequest",
    "ExpiryContext",
    "LotAllocation",
    "LotInfo",
    "LotPick",
    "available_lots_out",
    "expiry_context",
    "inverse_packaging",
    "is_expired_lot",
    "lot_allocations",
    "lot_infos",
    "LevelOut",
    "MovementRef",
    "MovementRequest",
    "MovementType",
    "PackagingSnapshot",
    "StockService",
    "ensure_document_site",
    "filter_site_ids",
    "operation_site",
    "refuse_unmanaged",
    "round_money",
    "sees_all_sites",
    "tenant_today",
    "visible_site_ids",
    "LevelRow",
    "LevelState",
    "StateFilter",
    "count_alerts",
    "levels_view",
    "list_levels",
    "locations_view",
]


def available_lots_out(
    db: Session, ctx: RequestContext, now: datetime, site_id: uuid.UUID, article_id: uuid.UUID
) -> AvailableLotsOut:
    """Lots disponibles d'un article sur un site (Lot 3-H-A, H-D18) — POS, dérogation d'une
    vente, choix manuel d'une sortie : mêmes règles et même ordre que le moteur de
    consommation. Le site doit avoir été contrôlé par l'appelant (``operation_site``)."""
    result = available_lots(db, ctx, now, site_id, article_id)
    return AvailableLotsOut(
        article_id=result.article_id,
        lot_tracked=result.lot_tracked,
        expiry_tracked=result.expiry_tracked,
        lots=[AvailableLotOut.model_validate(lot, from_attributes=True) for lot in result.lots],
    )
