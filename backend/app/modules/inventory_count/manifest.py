from app.modules.catalog.api import (
    register_assortment_removal_check,
    register_lot_flags_check,
    register_packaging_usage,
)
from app.modules.inventory_count.footprint import inventory_footprint
from app.modules.inventory_count.router import router
from app.modules.inventory_count.service import (
    assortment_removal_check,
    lot_flags_check,
    packagings_used,
)
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE
P = "inventory_count.inventory"

# Lot 3-C : un conditionnement utilisé par un comptage a sa conversion figée.
register_packaging_usage("inventory_count", packagings_used)
# Lot 3-H : le suivi par lot d'un article en cours d'inventaire (mode figé) ne change pas.
register_lot_flags_check("inventory_count", lot_flags_check)
# Assortiment (ADR-0046, D4) : retrait refusé si un inventaire ouvert du site contient l'article.
register_assortment_removal_check("inventory_count", assortment_removal_check)

MANIFEST = ModuleManifest(
    code="inventory_count",
    # Stock : ajustements exclusivement via StockService ; catalogue : articles (API publique).
    depends_on=("catalog", "stock"),
    permissions=(
        PermissionDef(f"{P}.view", R),
        PermissionDef(f"{P}.create", W),
        PermissionDef(f"{P}.update", W),  # brouillon : commentaire, articles d'un ciblé
        PermissionDef(f"{P}.count", W),  # démarrer, saisir, terminer / reprendre le comptage
        PermissionDef(f"{P}.validate", W),  # appliquer les écarts au stock
        PermissionDef(f"{P}.cancel", W),  # abandon avant validation
    ),
    # Inventaire : fonctionnalité cœur du stock, incluse dans tous les plans (aucune feature).
    router=router,
    route_prefix="/inventories",
    site_footprint=inventory_footprint,
)
