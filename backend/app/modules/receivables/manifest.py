from app.modules.receivables.router import customer_router, router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R = AccessKind.READ

MANIFEST = ModuleManifest(
    code="receivables",
    # Ventes et paiements : unique source de vérité (API publique ``sales.api``) ; clients :
    # débiteurs et limite de crédit ; stock : périmètre des sites (``stock.api``).
    depends_on=("sales", "customers", "stock"),
    permissions=(
        # Consultation seule : les encaissements restent dans ``sales`` (sales.payment.*).
        PermissionDef("receivables.receivable.view", R),
    ),
    router=router,
    # Sous-ressources des clients : /customers/{customer_id}/receivables, /credit-exposure.
    extra_routers=(("/customers", customer_router),),
)
