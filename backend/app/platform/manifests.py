"""Modules du socle plateforme (toujours actifs)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.registry import AccessKind, LimitDef, ModuleManifest, PermissionDef
from app.platform.tenancy.models import Site


def _count_active_sites(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Site).where(Site.is_active.is_(True))) or 0


def _count_active_members(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(TenantMembership.status == MembershipStatus.ACTIVE)
        )
        or 0
    )


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
            # Profil d'activité (Phase 3.1) : consulter le catalogue / changer de profil.
            PermissionDef("organization.profile.view", R),
            PermissionDef("organization.profile.manage", A),
        ),
        limits=(LimitDef("max_sites", _count_active_sites),),
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
        limits=(LimitDef("max_users", _count_active_members),),
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
