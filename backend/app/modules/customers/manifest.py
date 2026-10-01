from app.modules.customers.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W, A = AccessKind.READ, AccessKind.WRITE, AccessKind.ADMIN

MANIFEST = ModuleManifest(
    code="customers",
    permissions=(
        PermissionDef("customers.customer.view", R),
        PermissionDef("customers.customer.create", W),
        PermissionDef("customers.customer.update", W),
        # Activation / désactivation (jamais de suppression).
        PermissionDef("customers.customer.status", W),
        # Limite de crédit (Lot 2) : distincte de la modification du client.
        PermissionDef("customers.credit_limit.manage", A),
    ),
    router=router,
)
