"""Changement contrôlé du profil d'ORIGINE d'un tenant (API d'administration et CLI TechNova).

Règles (ADR-0024, profils / modules par site) :
- le nouveau profil doit exister et être actif ;
- **aucune donnée n'est supprimée ni modifiée** : seul ``tenants.business_profile_code`` change ;
- le profil d'activité EFFECTIF est celui de chaque site (``sites.business_profile_code``) :
  ce changement est refusé dès qu'un site existe (``409 profile_is_per_site``, D2), quel que
  soit l'appelant — API ou CLI TechNova (palier F, D8 : aucun contournement ; le profil d'un
  site ne change que par ``PUT /sites/{id}/business-profile``, ADR-0048) ;
- les activations de modules sont portées par les sites (``site_modules``, palier C) ;
  ``tenant_modules`` (legacy) n'est ni lu ni écrit : aucun contrôle d'incompatibilité ici (une
  entreprise sans site n'a encore aucun module activé) ;
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
from app.platform.registry import ModuleRegistry
from app.platform.tenancy.models import Site, Tenant


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
    """Change le profil d'origine du tenant actif (la session est déjà dans son contexte RLS),
    seulement tant qu'il n'a aucun site."""
    has_site = session.scalar(select(Site.id).where(Site.tenant_id == tenant.id).limit(1))
    if has_site is not None:
        raise ConflictError("Le profil d'activité est défini par site", code="profile_is_per_site")
    profile = session.get(BusinessProfile, code)
    if profile is None or not profile.is_active:
        raise BusinessRuleError(f"Profil inconnu : {code}", code="unknown_profile")
    previous = tenant.business_profile_code
    if previous == profile.code:
        return ProfileChange(previous=previous, profile=profile.code)

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
            "enabled_modules": [],
        },
        meta=meta,
    )
    session.flush()
    return ProfileChange(previous=previous, profile=profile.code)
