from app.modules.customers.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE

MANIFEST = ModuleManifest(
    code="customers",
    permissions=(
        PermissionDef("customers.customer.view", R),
        PermissionDef("customers.customer.create", W),
        PermissionDef("customers.customer.update", W),
        # Activation / désactivation (jamais de suppression).
        PermissionDef("customers.customer.status", W),
    ),
    router=router,
)
