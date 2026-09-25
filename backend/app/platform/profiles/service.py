"""Changement contrôlé du profil d'activité d'un tenant (API d'administration et CLI TechNova).

Règles (ADR-0024) :
- le nouveau profil doit exister et être actif ;
- **aucune donnée n'est supprimée ni modifiée** : seul ``tenants.business_profile_code``
  change ; les activations de modules existantes sont conservées ;
- refus (``409 profile_change_incompatible``) si un module **implémenté** et **activé** par le
  tenant n'est pas proposé par le nouveau profil (il faut d'abord le désactiver : ses données
  restent intactes) ;
- les modules proposés par défaut par le nouveau profil, inclus au plan et jamais paramétrés
  par le tenant, sont activés (comme à la création du tenant) ;
- audité (``tenant.profile_changed``).
Le plan, les permissions, les rôles, les sites et la RLS ne sont pas concernés.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError
from app.platform.audit.service import RequestMeta, record_audit
from app.platform.catalog.models import BusinessProfile
from app.platform.registry import ModuleRegistry, ModuleStatus
from app.platform.subscriptions.service import current_plan
from app.platform.tenancy.models import Tenant, TenantModule


@dataclass(frozen=True)
class ProfileChange:
    previous: str
    profile: str
    enabled_modules: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.previous != self.profile


def change_business_profile(
    session: Session,
    registry: ModuleRegistry,
    tenant: Tenant,
    code: str,
    *,
    actor: str,
    user_id: uuid.UUID | None = None,
    meta: RequestMeta | None = None,
) -> ProfileChange:
    """Change le profil du tenant actif (la session est déjà dans son contexte RLS)."""
    profile = session.get(BusinessProfile, code)
    if profile is None or not profile.is_active:
        raise BusinessRuleError(f"Profil inconnu : {code}", code="unknown_profile")
    previous = tenant.business_profile_code
    if previous == profile.code:
        return ProfileChange(previous=previous, profile=profile.code)

    rows = {row.module_code: row for row in session.scalars(select(TenantModule))}
    offered = {link.module_code for link in profile.modules}
    blocking = sorted(
        code
        for code, row in rows.items()
        if row.enabled
        and code in registry
        and not registry.get(code).core
        and registry.get(code).status == ModuleStatus.AVAILABLE
        and code not in offered
    )
    if blocking:
        raise ConflictError(
            "Des modules activés ne sont pas proposés par ce profil",
            code="profile_change_incompatible",
            extra={"modules": blocking},
        )

    plan_modules = {m.module_code for m in current_plan(session).modules}
    enabled: list[str] = []
    for link in profile.modules:
        if (
            link.default_enabled
            and link.module_code in plan_modules
            and link.module_code not in rows
        ):
            session.add(TenantModule(tenant_id=tenant.id, module_code=link.module_code))
            enabled.append(link.module_code)

    tenant.business_profile_code = profile.code
    record_audit(
        session,
        action="tenant.profile_changed",
        tenant_id=tenant.id,
        user_id=user_id,
        entity_type="tenant",
        entity_id=tenant.id,
        data={
            "actor": actor,
            "previous_profile": previous,
            "profile": profile.code,
            "sector": profile.sector_code,
            "enabled_modules": enabled,
        },
        meta=meta,
    )
    session.flush()
    return ProfileChange(previous=previous, profile=profile.code, enabled_modules=enabled)
