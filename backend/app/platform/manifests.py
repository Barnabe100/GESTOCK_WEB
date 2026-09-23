"""Modules du socle plateforme (toujours actifs)."""

from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, A, B = AccessKind.READ, AccessKind.ADMIN, AccessKind.BILLING

PLATFORM_MODULES: tuple[ModuleManifest, ...] = (
    ModuleManifest(code="dashboard", core=True),
    ModuleManifest(
        code="organization",
        core=True,
        permissions=(
            PermissionDef("organization.tenant.view", R),
            PermissionDef("organization.tenant.update", A),
            PermissionDef("organization.site.view", R),
            PermissionDef("organization.site.manage", A),
            PermissionDef("organization.module.view", R),
            PermissionDef("organization.module.manage", A),
        ),
    ),
    ModuleManifest(
        code="users",
        core=True,
        permissions=(
            PermissionDef("users.member.view", R),
            PermissionDef("users.member.manage", A),
            PermissionDef("users.role.view", R),
            PermissionDef("users.role.manage", A),
        ),
    ),
    ModuleManifest(
        code="audit",
        core=True,
        permissions=(PermissionDef("audit.log.view", R),),
    ),
    ModuleManifest(
        code="subscription",
        core=True,
        permissions=(PermissionDef("subscription.subscription.view", B),),
    ),
)
