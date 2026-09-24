from app.modules.catalog.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE

MANIFEST = ModuleManifest(
    code="catalog",
    permissions=(
        PermissionDef("catalog.category.view", R),
        PermissionDef("catalog.category.create", W),
        PermissionDef("catalog.category.update", W),
        PermissionDef("catalog.category.status", W),
        PermissionDef("catalog.article.view", R),
        PermissionDef("catalog.article.create", W),
        PermissionDef("catalog.article.update", W),
        PermissionDef("catalog.article.status", W),
    ),
    router=router,
)
