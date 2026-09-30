"""Calcul unique des capacités d'un utilisateur dans un tenant (et un site).

modules effectifs   = modules core ∪ fermeture_dépendances(profil ∩ plan ∩ activations tenant)
permissions         = permissions accordées (propriétaire : toutes) ∩ permissions des modules
                      effectifs, puis filtrées par la politique du statut d'abonnement.

**1 site = 1 abonnement** (ADR-0033) : sur un site, plan, statut, modules et fonctionnalités
sont ceux de l'abonnement de CE site. Sans site (données de l'entreprise : catalogue,
clients, utilisateurs… ou vue consolidée), les capacités sont l'**union** de celles de chaque
abonnement de l'entreprise (une permission n'est retenue que si UN abonnement l'accorde, avec
son propre statut) ; toute écriture portant sur un site est revérifiée pour ce site
(``RequestContext.ensure_site_allows``).

Le profil d'activité ne fait que **proposer** des modules : il n'accorde aucune permission et
ne contourne ni le plan, ni l'abonnement, ni les rôles. La présentation (navigation, tableau
de bord, terminologie, thème) est résolue à part, pour l'interface seulement
(``app.platform.profiles.registry``, ADR-0024).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.access.models import Role, TenantMembership
from app.platform.access.permissions import effective_role_permissions
from app.platform.catalog.models import BusinessProfile, Plan
from app.platform.licensing.service import subscription_terms
from app.platform.registry import ModuleRegistry
from app.platform.subscriptions.models import Subscription, SubscriptionStatus
from app.platform.subscriptions.plan_policy import PlanPolicy, PlanTerms
from app.platform.subscriptions.service import (
    allowed_access,
    effective_status,
    site_subscription,
    tenant_subscriptions,
)
from app.platform.tenancy.models import Site, Tenant, TenantModule


@dataclass(frozen=True)
class Capabilities:
    profile_code: str
    # Classification du profil (reporting, présentation) ; jamais une règle d'accès.
    sector_code: str | None
    ux_profile_code: str | None
    # Abonnement représentatif (celui du site ; sans site, le plus permissif) : plan et
    # statut affichés. Les règles d'accès utilisent chaque abonnement séparément.
    plan_code: str
    subscription_status: SubscriptionStatus
    subscription_id: uuid.UUID
    allowed_access: frozenset[str]
    modules: frozenset[str]
    permissions: frozenset[str]
    # Accordées mais bloquées par le statut de l'abonnement.
    restricted_permissions: frozenset[str]
    # Fonctionnalités optionnelles du plan, pour les modules effectifs.
    features: frozenset[str]
    accessible_site_ids: frozenset[uuid.UUID]


@dataclass(frozen=True)
class SubscriptionGrant:
    """Ce qu'accorde un abonnement à l'instant donné (statut effectif, politique, modules,
    fonctionnalités)."""

    subscription: Subscription
    plan: Plan
    # Conditions en vigueur : licence en vigueur (figées), sinon plan (ADR-0034).
    terms: PlanTerms
    status: SubscriptionStatus
    access: frozenset[str]
    modules: frozenset[str]
    features: frozenset[str]

    @property
    def rank(self) -> tuple[int, int]:
        return len(self.access), _STATUS_ORDER.get(self.status, 0)


# Départage de l'abonnement représentatif, à politique d'accès égale.
_STATUS_ORDER = {
    SubscriptionStatus.ACTIVE: 6,
    SubscriptionStatus.TRIAL: 5,
    SubscriptionStatus.PAST_DUE: 4,
    SubscriptionStatus.PENDING_ACTIVATION: 3,
    SubscriptionStatus.EXPIRED: 2,
    SubscriptionStatus.SUSPENDED: 1,
    SubscriptionStatus.CANCELLED: 0,
}


class SubscriptionMissingError(LookupError):
    pass


class CapabilityService:
    def __init__(self, session: Session, registry: ModuleRegistry) -> None:
        self.session = session
        self.registry = registry

    # --- Modules --------------------------------------------------------------------------

    def offered_modules(self, profile: BusinessProfile, plan: Plan | PlanTerms) -> set[str]:
        """Modules que le tenant peut activer : proposés par le profil ET inclus dans le plan
        (ou dans la licence en vigueur, qui fige ceux du plan à son émission)."""
        terms = plan if isinstance(plan, PlanTerms) else PlanTerms.of_plan(plan)
        return {m.module_code for m in profile.modules} & set(terms.modules)

    def enabled_module_codes(self) -> set[str]:
        return set(
            self.session.scalars(
                select(TenantModule.module_code).where(TenantModule.enabled.is_(True))
            )
        )

    def effective_modules(self, profile: BusinessProfile, plan: Plan | PlanTerms) -> set[str]:
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
        # Un rôle inactif n'accorde rien ; ses attributions sont conservées (ADR-0015).
        active_roles = select(Role).where(Role.id.in_(role_ids), Role.is_active.is_(True))
        for role in self.session.scalars(active_roles):
            granted |= effective_role_permissions(role, self.registry)
        return granted

    def held_permissions(
        self,
        membership: TenantMembership,
        site_id: uuid.UUID | None,
        modules: set[str] | frozenset[str],
        features: set[str] | frozenset[str],
    ) -> set[str]:
        """Permissions détenues sur une portée (tout le tenant si ``site_id`` est nul), limitées
        aux modules effectifs et aux fonctionnalités du plan, avant filtrage par le statut
        d'abonnement. Sert au calcul des capacités et au contrôle anti-escalade."""
        module_permissions = self.registry.available_permissions(modules, features)
        granted = self.granted_permissions(membership, site_id)
        if granted is None:
            return set(module_permissions)
        return {code for code in granted if code in module_permissions}

    def accessible_site_ids(self, membership: TenantMembership) -> frozenset[uuid.UUID]:
        active = set(self.session.scalars(select(Site.id).where(Site.is_active.is_(True))))
        if membership.is_owner or membership.all_sites:
            return frozenset(active)
        return frozenset(link.site_id for link in membership.site_links) & frozenset(active)

    # --- Abonnements -----------------------------------------------------------------------

    def grant(
        self, subscription: Subscription, profile: BusinessProfile, now: datetime
    ) -> SubscriptionGrant:
        plan = self.session.get(Plan, subscription.plan_code)
        if plan is None:
            raise LookupError("plan introuvable pour l'abonnement")
        status = effective_status(subscription, plan.grace_days, now)
        terms = subscription_terms(self.session, subscription, plan, now)
        modules = frozenset(self.effective_modules(profile, terms))
        return SubscriptionGrant(
            subscription=subscription,
            plan=plan,
            terms=terms,
            status=status,
            access=allowed_access(self.session, status),
            modules=modules,
            features=PlanPolicy(self.session, terms, self.registry).features(modules),
        )

    def grants(
        self, profile: BusinessProfile, site_id: uuid.UUID | None, now: datetime
    ) -> list[SubscriptionGrant]:
        """Abonnement du site, ou tous les abonnements de l'entreprise (sans site)."""
        if site_id is not None:
            subscription = site_subscription(self.session, site_id)
            subscriptions = [subscription] if subscription is not None else []
        else:
            subscriptions = tenant_subscriptions(self.session)
        if not subscriptions:
            raise SubscriptionMissingError("aucun abonnement")
        return [self.grant(s, profile, now) for s in subscriptions]

    # --- Résolution complète --------------------------------------------------------------

    def resolve(
        self,
        *,
        tenant: Tenant,
        membership: TenantMembership,
        site_id: uuid.UUID | None,
        now: datetime,
    ) -> Capabilities:
        profile = self.session.get(BusinessProfile, tenant.business_profile_code)
        if profile is None:
            raise LookupError("profil introuvable pour le tenant")
        grants = self.grants(profile, site_id, now)
        representative = max(grants, key=lambda g: g.rank)

        permitted: set[str] = set()
        candidates: set[str] = set()
        for grant in grants:
            module_permissions = self.registry.available_permissions(grant.modules, grant.features)
            held = self.held_permissions(membership, site_id, grant.modules, grant.features)
            candidates |= held
            permitted |= {c for c in held if module_permissions[c].access.value in grant.access}

        return Capabilities(
            profile_code=profile.code,
            sector_code=profile.sector_code,
            ux_profile_code=profile.ux_profile_code,
            # Offre dont les droits sont en vigueur (licence en vigueur, sinon plan) : R4.
            plan_code=representative.terms.plan_code,
            subscription_status=representative.status,
            subscription_id=representative.subscription.id,
            allowed_access=frozenset().union(*(g.access for g in grants)),
            modules=frozenset().union(*(g.modules for g in grants)),
            permissions=frozenset(permitted),
            restricted_permissions=frozenset(candidates - permitted),
            features=frozenset().union(*(g.features for g in grants)),
            accessible_site_ids=self.accessible_site_ids(membership),
        )
