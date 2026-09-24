"""Calcul unique des capacités d'un utilisateur dans un tenant (et un site).

modules effectifs   = modules core ∪ fermeture_dépendances(profil ∩ plan ∩ activations tenant)
permissions         = permissions accordées (propriétaire : toutes) ∩ permissions des modules
                      effectifs, puis filtrées par la politique du statut d'abonnement.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.access.models import Role, TenantMembership
from app.platform.access.permissions import effective_role_permissions
from app.platform.catalog.models import BusinessProfile, Plan
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.models import Subscription, SubscriptionStatus
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.service import allowed_access, effective_status
from app.platform.tenancy.models import Site, Tenant, TenantModule


@dataclass(frozen=True)
class Capabilities:
    profile_code: str
    plan_code: str
    subscription_status: SubscriptionStatus
    allowed_access: frozenset[str]
    modules: frozenset[str]
    permissions: frozenset[str]
    # Accordées mais bloquées par le statut de l'abonnement.
    restricted_permissions: frozenset[str]
    navigation: tuple[str, ...]
    terminology: dict[str, Any]
    # Fonctionnalités optionnelles du plan, pour les modules effectifs.
    features: frozenset[str]
    accessible_site_ids: frozenset[uuid.UUID]


class CapabilityService:
    def __init__(self, session: Session, registry: ModuleRegistry) -> None:
        self.session = session
        self.registry = registry

    # --- Modules --------------------------------------------------------------------------

    def offered_modules(self, profile: BusinessProfile, plan: Plan) -> set[str]:
        """Modules que le tenant peut activer : proposés par le profil ET inclus dans le plan."""
        return {m.module_code for m in profile.modules} & {m.module_code for m in plan.modules}

    def enabled_module_codes(self) -> set[str]:
        return set(
            self.session.scalars(
                select(TenantModule.module_code).where(TenantModule.enabled.is_(True))
            )
        )

    def effective_modules(self, profile: BusinessProfile, plan: Plan) -> set[str]:
        candidates = self.offered_modules(profile, plan) & self.enabled_module_codes()
        return self.registry.core_codes() | self.registry.resolve_dependencies(candidates)

    # --- Permissions ----------------------------------------------------------------------

    def granted_permissions(
        self, membership: TenantMembership, site_id: uuid.UUID | None
    ) -> set[str] | None:
        """Permissions accordées par les rôles (``None`` = propriétaire : toutes)."""
        if membership.is_owner:
            return None
        role_ids = {
            link.role_id
            for link in membership.role_links
            if link.site_id is None or (site_id is not None and link.site_id == site_id)
        }
        if not role_ids:
            return set()
        granted: set[str] = set()
        for role in self.session.scalars(select(Role).where(Role.id.in_(role_ids))):
            granted |= effective_role_permissions(role, self.registry)
        return granted

    def held_permissions(
        self,
        membership: TenantMembership,
        site_id: uuid.UUID | None,
        modules: set[str],
    ) -> set[str]:
        """Permissions détenues sur une portée (tout le tenant si ``site_id`` est nul), limitées
        aux modules effectifs, avant filtrage par le statut d'abonnement. Sert à la fois au
        calcul des capacités et au contrôle anti-escalade."""
        module_permissions = self.registry.permissions_of(modules)
        granted = self.granted_permissions(membership, site_id)
        if granted is None:
            return set(module_permissions)
        return {code for code in granted if code in module_permissions}

    def accessible_site_ids(self, membership: TenantMembership) -> frozenset[uuid.UUID]:
        active = set(self.session.scalars(select(Site.id).where(Site.is_active.is_(True))))
        if membership.is_owner or membership.all_sites:
            return frozenset(active)
        return frozenset(link.site_id for link in membership.site_links) & frozenset(active)

    # --- Résolution complète --------------------------------------------------------------

    def resolve(
        self,
        *,
        tenant: Tenant,
        membership: TenantMembership,
        subscription: Subscription,
        site_id: uuid.UUID | None,
        now: datetime,
    ) -> Capabilities:
        profile = self.session.get(BusinessProfile, tenant.business_profile_code)
        plan = self.session.get(Plan, subscription.plan_code)
        if profile is None or plan is None:
            raise LookupError("profil ou plan introuvable pour le tenant")

        status = effective_status(subscription, plan.grace_days, now)
        access = allowed_access(self.session, status)

        modules = self.effective_modules(profile, plan)
        module_permissions = self.registry.permissions_of(modules)
        candidates = self.held_permissions(membership, site_id, modules)
        permitted = {c for c in candidates if module_permissions[c].access.value in access}

        navigation = tuple(code for code in profile.navigation if code in modules)
        navigation += tuple(
            code for code in sorted(self.registry.core_codes()) if code not in navigation
        )

        return Capabilities(
            profile_code=profile.code,
            plan_code=plan.code,
            subscription_status=status,
            allowed_access=access,
            modules=frozenset(modules),
            permissions=frozenset(permitted),
            restricted_permissions=frozenset(candidates - permitted),
            navigation=navigation,
            terminology=profile.terminology,
            features=PlanPolicy(self.session, plan, self.registry).features(modules),
            accessible_site_ids=self.accessible_site_ids(membership),
        )
