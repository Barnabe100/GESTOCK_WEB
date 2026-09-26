"""Modules du socle plateforme (toujours actifs)."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.access.site_access import site_access_condition
from app.platform.onboarding.steps import ORGANIZATION_STEPS, SUBSCRIPTION_STEPS, USERS_STEPS
from app.platform.registry import AccessKind, LimitDef, ModuleManifest, PermissionDef
from app.platform.tenancy.models import Site


def _count_active_sites(db: Session, site_id: uuid.UUID | None) -> int:
    """Sites couverts : l'abonnement d'un site couvre ce seul site (1 site = 1 abonnement,
    ADR-0033). ``max_sites`` n'est plus appliquée à la création d'un site (chaque site a son
    propre abonnement) ; elle reste une donnée de présentation de l'offre."""
    query = select(func.count()).select_from(Site).where(Site.is_active.is_(True))
    if site_id is not None:
        query = query.where(Site.id == site_id)
    return db.scalar(query) or 0


def _count_active_members(db: Session, site_id: uuid.UUID | None) -> int:
    """Utilisateurs actifs de l'entreprise, ou ayant accès au site ``site_id`` (limite de
    l'abonnement de ce site, ADR-0033 ; voir ``members_with_site_access``)."""
    query = (
        select(func.count())
        .select_from(TenantMembership)
        .where(TenantMembership.status == MembershipStatus.ACTIVE)
    )
    if site_id is not None:
        query = query.where(site_access_condition(site_id))
    return db.scalar(query) or 0


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
            # Onboarding (Phase 3.2-B) : consulter la progression / démarrer une étape. Aucune
            # permission ne permet de déclarer une étape terminée (validation automatique).
            PermissionDef("organization.onboarding.view", R),
            PermissionDef("organization.onboarding.manage", A),
        ),
        limits=(LimitDef("max_sites", _count_active_sites),),
        onboarding=ORGANIZATION_STEPS,
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
        onboarding=USERS_STEPS,
    ),
    ModuleManifest(
        code="audit",
        core=True,
        permissions=(PermissionDef("audit.log.view", R),),
    ),
    ModuleManifest(
        code="subscription",
        core=True,
        permissions=(
            PermissionDef("subscription.subscription.view", B),
            # Déclarer un paiement d'abonnement à TechNova (Phase 3.3-A) : écriture de nature
            # « facturation » (autorisée même abonnement expiré ou en attente d'activation).
            PermissionDef("subscription.payment.declare", B),
        ),
        onboarding=SUBSCRIPTION_STEPS,
    ),
)
