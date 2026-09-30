from app.modules.sales.payment_method_router import router as payment_method_router
from app.modules.sales.payment_methods import ensure_default_payment_methods
from app.modules.sales.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W, A = AccessKind.READ, AccessKind.WRITE, AccessKind.ADMIN

MANIFEST = ModuleManifest(
    code="sales",
    # Stock : sortie via StockService ; clients : client facultatif (API publique, FK). La
    # caisse n'est PAS une dépendance : elle implémente le port ``cash_port`` (paiements CASH).
    depends_on=("catalog", "stock", "customers"),
    permissions=(
        # Consultation : ses propres ventes ; ``view_all`` : toutes les ventes du site (Lot 1).
        PermissionDef("sales.sale.view", R),
        PermissionDef("sales.sale.view_all", R),
        PermissionDef("sales.sale.create", W),
        PermissionDef("sales.sale.update", W),
        PermissionDef("sales.sale.validate", W),
        # Vente à crédit (reste dû à la validation ; client obligatoire) et dépassement
        # exceptionnel de la limite de crédit (justification, audit) — Lot 1.
        PermissionDef("sales.sale.credit_create", W),
        PermissionDef("sales.sale.credit_override", W),
        # Annulation (remise en stock d'une vente validée) : réservée à l'administration.
        PermissionDef("sales.sale.cancel", W),
        # Paiements (Phase 2.7) : encaissement indépendant de la validation de la vente.
        PermissionDef("sales.payment.view", R),
        PermissionDef("sales.payment.create", W),
        # Annulation d'un paiement (correction) : réservée à l'administration par défaut.
        PermissionDef("sales.payment.cancel", W),
        # Moyens de paiement configurables (Lot 1) : configuration de l'entreprise.
        PermissionDef("sales.payment_method.manage", A),
    ),
    router=router,
    extra_routers=(("/payment-methods", payment_method_router),),
    tenant_setup=ensure_default_payment_methods,
)
