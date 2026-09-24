from app.modules.alerts.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

MANIFEST = ModuleManifest(
    code="alerts",
    depends_on=("stock",),
    permissions=(PermissionDef("alerts.stock.view", AccessKind.READ),),
    router=router,
)
