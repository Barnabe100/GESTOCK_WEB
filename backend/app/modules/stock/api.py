"""API publique du module Stock pour les autres modules (alertes, ventes, rapports…), qui
n'importent jamais ses modèles directement (règle d'architecture 10). Toute modification du
stock passe par ``StockService`` (règle d'architecture 5)."""

from app.modules.stock.level_service import (
    LevelRow,
    LevelState,
    StateFilter,
    count_alerts,
    levels_view,
    list_levels,
)
from app.modules.stock.models import MovementType
from app.modules.stock.schemas import LevelOut
from app.modules.stock.sites import (
    ensure_document_site,
    filter_site_ids,
    operation_site,
    tenant_today,
    visible_site_ids,
)
from app.modules.stock.stock_service import (
    MovementRef,
    MovementRequest,
    StockService,
    round_money,
)

__all__ = [
    "LevelOut",
    "MovementRef",
    "MovementRequest",
    "MovementType",
    "StockService",
    "ensure_document_site",
    "filter_site_ids",
    "operation_site",
    "round_money",
    "tenant_today",
    "visible_site_ids",
    "LevelRow",
    "LevelState",
    "StateFilter",
    "count_alerts",
    "levels_view",
    "list_levels",
]
