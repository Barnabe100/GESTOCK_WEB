from app.modules.sales.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE

MANIFEST = ModuleManifest(
    code="sales",
    # Stock : sortie via StockService ; clients : client facultatif (API publique, FK).
    depends_on=("catalog", "stock", "customers"),
    permissions=(
        PermissionDef("sales.sale.view", R),
        PermissionDef("sales.sale.create", W),
        PermissionDef("sales.sale.update", W),
        PermissionDef("sales.sale.validate", W),
        # Annulation (remise en stock d'une vente validée) : réservée à l'administration.
        PermissionDef("sales.sale.cancel", W),
    ),
    router=router,
)
