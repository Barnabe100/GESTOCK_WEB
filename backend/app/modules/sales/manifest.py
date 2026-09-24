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
        # Paiements (Phase 2.7) : encaissement indépendant de la validation de la vente.
        PermissionDef("sales.payment.view", R),
        PermissionDef("sales.payment.create", W),
        # Annulation d'un paiement (correction) : réservée à l'administration par défaut.
        PermissionDef("sales.payment.cancel", W),
    ),
    router=router,
)
