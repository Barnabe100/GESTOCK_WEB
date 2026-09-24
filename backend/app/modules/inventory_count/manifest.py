from app.modules.inventory_count.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE
P = "inventory_count.inventory"

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
)
