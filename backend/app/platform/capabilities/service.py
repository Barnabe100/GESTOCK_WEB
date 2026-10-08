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

**Profil par site** (palier B) : le profil effectif d'un abonnement est celui de SON site
(``sites.business_profile_code``) ; l'abonnement pris à l'inscription, pas encore rattaché à un
site, utilise le profil d'origine du tenant. Sans site sélectionné, l'union porte sur les
abonnements des sites ACCESSIBLES au membre (et l'abonnement non rattaché) ; la présentation
utilise le profil du site de référence (site principal, ``reference_site_id``), jamais un profil
commun inventé.

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


class ProfileScope:
    SITE = "site"  # profil du site sélectionné
    REFERENCE = "reference"  # vue « Tous les sites » : profil du site de référence (D4)


@dataclass(frozen=True)
class Capabilities:
    # Profil effectif du site sélectionné, ou profil de référence sans site (``profile_scope``).
    profile_code: str
    profile_scope: str
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
    # Vue « Tous les sites » : site dont le profil sert de référence (``None`` : site
    # sélectionné, ou aucun site accessible).
    reference_site_id: uuid.UUID | None = None


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

    # --- Profils --------------------------------------------------------------------------

    def profile(self, code: str) -> BusinessProfile:
        profile = self.session.get(BusinessProfile, code)
        if profile is None:
            raise LookupError(f"profil introuvable : {code}")
        return profile

    def site_profile(self, site_id: uuid.UUID) -> BusinessProfile:
        """Profil effectif d'un site : ``sites.business_profile_code`` (jamais celui du tenant)."""
        site = self.session.get(Site, site_id)
        if site is None:
            raise LookupError("site introuvable")
        return self.profile(site.business_profile_code)

    def subscription_profile(self, subscription: Subscription, tenant: Tenant) -> BusinessProfile:
        """Profil d'un abonnement : celui de son site ; abonnement d'inscription pas encore
        rattaché à un site : profil d'origine du tenant."""
        if subscription.site_id is not None:
            return self.site_profile(subscription.site_id)
        return self.profile(tenant.business_profile_code)

    def reference_site_id(self, accessible: frozenset[uuid.UUID]) -> uuid.UUID | None:
        """Site de référence de la vue « Tous les sites » (= ``main_site_id``) : le plus ancien
        site ACTIF du tenant s'il est accessible au membre, sinon le premier site accessible
        (ordre alphabétique). Une règle de calcul, aucun indicateur en base."""
        oldest = self.session.scalar(
            select(Site.id)
            .where(Site.is_active.is_(True))
            .order_by(Site.created_at, Site.id)
            .limit(1)
        )
        if oldest is not None and oldest in accessible:
            return oldest
        if not accessible:
            return None
        return self.session.scalar(
            select(Site.id).where(Site.id.in_(accessible)).order_by(Site.name, Site.id).limit(1)
        )

    # --- Abonnements (suite) -----------------------------------------------------------------

    def grants(
        self,
        tenant: Tenant,
        site_id: uuid.UUID | None,
        now: datetime,
        accessible: frozenset[uuid.UUID] | None = None,
    ) -> list[SubscriptionGrant]:
        """Abonnement du site (profil du site), ou, sans site, ceux des sites accessibles
        (``accessible``) et l'abonnement non rattaché — chacun évalué avec le profil de SON site.
        Membre sans aucun site accessible : abonnements de l'entreprise (données communes)."""
        if site_id is not None:
            subscription = site_subscription(self.session, site_id)
            subscriptions = [subscription] if subscription is not None else []
        else:
            subscriptions = tenant_subscriptions(self.session)
            if accessible is not None:
                scoped = [s for s in subscriptions if s.site_id is None or s.site_id in accessible]
                subscriptions = scoped or subscriptions
        if not subscriptions:
            raise SubscriptionMissingError("aucun abonnement")
        return [self.grant(s, self.subscription_profile(s, tenant), now) for s in subscriptions]

    # --- Résolution complète --------------------------------------------------------------

    def resolve(
        self,
        *,
        tenant: Tenant,
        membership: TenantMembership,
        site_id: uuid.UUID | None,
        now: datetime,
    ) -> Capabilities:
        accessible = self.accessible_site_ids(membership)
        grants = self.grants(tenant, site_id, now, accessible)
        representative = max(grants, key=lambda g: g.rank)
        reference_site: uuid.UUID | None = None
        if site_id is not None:
            profile = self.site_profile(site_id)
            scope = ProfileScope.SITE
        else:
            reference_site = self.reference_site_id(accessible)
            profile = (
                self.site_profile(reference_site)
                if reference_site is not None
                else self.profile(tenant.business_profile_code)
            )
            scope = ProfileScope.REFERENCE

        permitted: set[str] = set()
        candidates: set[str] = set()
        for grant in grants:
            module_permissions = self.registry.available_permissions(grant.modules, grant.features)
            held = self.held_permissions(membership, site_id, grant.modules, grant.features)
            candidates |= held
            permitted |= {c for c in held if module_permissions[c].access.value in grant.access}

        return Capabilities(
            profile_code=profile.code,
            profile_scope=scope,
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
            accessible_site_ids=accessible,
            reference_site_id=reference_site,
        )
