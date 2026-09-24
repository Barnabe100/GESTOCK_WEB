from app.modules.suppliers.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE

MANIFEST = ModuleManifest(
    code="suppliers",
    permissions=(
        PermissionDef("suppliers.supplier.view", R),
        PermissionDef("suppliers.supplier.create", W),
        PermissionDef("suppliers.supplier.update", W),
        PermissionDef("suppliers.supplier.status", W),
    ),
    router=router,
)
