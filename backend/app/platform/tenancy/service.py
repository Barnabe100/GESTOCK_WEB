import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from app.platform.audit.service import record_audit
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.models import BusinessProfile, GeoCountry, Plan
from app.platform.context import RequestContext
from app.platform.footprint import module_footprint
from app.platform.licensing.service import current_terms
from app.platform.registry import ModuleRegistry, ModuleStatus, get_registry
from app.platform.sequences.service import site_has_numbers
from app.platform.subscriptions.models import Subscription, SubscriptionStatus
from app.platform.subscriptions.plan_policy import PlanTerms
from app.platform.subscriptions.service import (
    freeze_tariff,
    period_enabled,
    plan_tariff,
    self_service,
    site_subscription,
    tenant_subscriptions,
    unattached_subscription,
)
from app.platform.tenancy.models import Site, SiteModule
from app.platform.tenancy.schemas import (
    ModuleOut,
    SiteCreate,
    SiteModuleOut,
    SiteUpdate,
    TenantUpdate,
)
from app.platform.tenancy.site_modules import init_site_modules, lock_site
from app.shared.clock import utcnow
from app.shared.ids import new_id


class TenantService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    # Jamais effacés : null ignoré (nom, fuseau) ou refusé (pays).
    _NOT_CLEARABLE = ("name", "timezone")

    def update(self, data: TenantUpdate) -> None:
        tenant = self.ctx.tenant
        changes = data.model_dump(exclude_unset=True)
        for key in self._NOT_CLEARABLE:
            if key in changes and changes[key] is None:
                del changes[key]
        if "country_code" in changes:
            changes["country_code"] = self._country(changes["country_code"])
        before = {k: getattr(tenant, k) for k in changes}
        for key, value in changes.items():
            setattr(tenant, key, value)
        record_audit(
            self.db,
            action="tenant.updated",
            tenant_id=tenant.id,
            user_id=self.ctx.user.id,
            entity_type="tenant",
            entity_id=tenant.id,
            data={"before": before, "after": changes},
            meta=self.ctx.meta,
        )

    def _country(self, code: str | None) -> str:
        """Pays renseigné une fois, jamais effacé ; un nouveau pays doit être actif dans le
        référentiel (le pays actuel reste accepté même s'il a été désactivé depuis)."""
        if code is None:
            raise BusinessRuleError("Le pays est obligatoire", code="country_required")
        code = code.upper()
        if code == self.ctx.tenant.country_code:
            return code
        country = self.db.get(GeoCountry, code)
        if country is None or not country.is_active:
            raise BusinessRuleError(f"Pays inconnu : {code}", code="unknown_country")
        return code


MODULE_MANAGE = "organization.module.manage"
PROFILE_MANAGE = "organization.profile.manage"


class SiteService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def list_all(self) -> list[Site]:
        return list(self.db.scalars(select(Site).order_by(Site.name)))

    def get(self, site_id: uuid.UUID) -> Site:
        site = self.db.get(Site, site_id)  # RLS : un site d'un autre tenant est invisible.
        if site is None:
            raise NotFoundError("Site introuvable", code="site_not_found")
        return site

    def _flush(self) -> None:
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Ce code de site existe déjà", code="site_code_taken") from exc

    def create(self, data: SiteCreate) -> Site:
        """Nouveau site et **son abonnement** (1 site = 1 abonnement, ADR-0033). Le premier site
        d'une inscription reçoit l'abonnement choisi à l'inscription ; tout autre site, un
        abonnement « en attente d'activation » au plan publié choisi par l'administrateur (sans
        essai) : il n'est opérationnel qu'après paiement confirmé et licence."""
        profile_code = self._new_site_profile(data.business_profile_code)
        pending = unattached_subscription(self.db, for_update=True)
        plan: Plan | None = None
        if pending is not None:
            if data.plan_code is not None or data.billing_period is not None:
                raise BusinessRuleError(
                    "L'abonnement de ce premier site a été choisi à l'inscription",
                    code="site_subscription_preselected",
                )
        else:
            if data.plan_code is None or data.billing_period is None:
                raise BusinessRuleError(
                    "Choisissez l'offre et la période de facturation du site",
                    code="site_plan_required",
                )
            plan = self.db.get(Plan, data.plan_code)
            if (
                plan is None
                or not self_service(plan)
                or not period_enabled(plan, data.billing_period)
            ):
                raise BusinessRuleError("Offre indisponible", code="plan_not_available")
        fields = data.model_dump(
            exclude={
                "plan_code",
                "billing_period",
                "requested_activations",
                "business_profile_code",
            }
        )
        site = Site(
            id=new_id(),
            tenant_id=self.ctx.tenant_id,
            business_profile_code=profile_code,
            **fields,
        )
        self.db.add(site)
        self._flush()
        if pending is not None:
            subscription = pending
            subscription.site_id = site.id
            subscription.requested_activations = data.requested_activations
            action = "subscription.site_attached"
        else:
            assert plan is not None and data.billing_period is not None
            now = utcnow()
            subscription = Subscription(
                tenant_id=self.ctx.tenant_id,
                site_id=site.id,
                plan_code=plan.code,
                billing_period=data.billing_period,
                status=SubscriptionStatus.PENDING_ACTIVATION,
                started_at=now,
                current_period_start=now,
                current_period_end=now,
                requested_activations=data.requested_activations,
            )
            freeze_tariff(subscription, plan_tariff(plan, data.billing_period))
            self.db.add(subscription)
            action = "subscription.created"
        self.db.flush()
        # Modules du site (palier C) : défauts du profil DU SITE ∩ abonnement DU SITE, dans la
        # même transaction ; rien n'est copié d'un autre site.
        modules = init_site_modules(
            self.db,
            self.ctx.tenant_id,
            site.id,
            CapabilityService(self.db, get_registry()).site_profile(site.id),
            current_terms(self.db, subscription).modules,
        )
        self.db.flush()
        record_audit(
            self.db,
            action=action,
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            site_id=site.id,
            entity_type="subscription",
            entity_id=subscription.id,
            data={
                "site_id": str(site.id),
                "plan": subscription.plan_code,
                "billing_period": subscription.billing_period.value,
                "status": subscription.status.value,
                "requested_activations": subscription.requested_activations,
            },
            meta=self.ctx.meta,
        )
        record_audit(
            self.db,
            action="site.created",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            entity_type="site",
            entity_id=site.id,
            data=fields
            | {
                "kind": site.kind.value,
                "business_profile": site.business_profile_code,
                "modules": sorted(modules),
            },
            meta=self.ctx.meta,
        )
        return site

    def _new_site_profile(self, code: str | None) -> str:
        """Profil d'un nouveau site (palier D) : le profil d'origine par défaut ; un autre
        profil actif du catalogue, choisi avec ``organization.profile.manage`` (la même
        permission que le changement de profil d'un site)."""
        origin = self.ctx.tenant.business_profile_code
        if code is None or code == origin:
            return origin
        profile = self.db.get(BusinessProfile, code)
        if profile is None or not profile.is_active:
            raise BusinessRuleError(f"Profil inconnu : {code}", code="unknown_profile")
        if PROFILE_MANAGE not in self.ctx.capabilities.permissions:
            if PROFILE_MANAGE in self.ctx.capabilities.restricted_permissions:
                raise ForbiddenError(
                    "Action indisponible avec le statut de l'abonnement",
                    code="subscription_restricted",
                )
            raise ForbiddenError(
                "Permission insuffisante pour choisir le profil du site",
                code="permission_denied",
            )
        return profile.code

    def update(self, site_id: uuid.UUID, data: SiteUpdate) -> Site:
        site = self.get(site_id)
        changes = data.model_dump(exclude_unset=True)
        if (
            "code" in changes
            and changes["code"] != site.code
            and site_has_numbers(self.db, site.id)
        ):
            # Le code figure dans des numéros déjà émis (VENT-{CODE}-…) : il reste stable.
            raise ConflictError(
                "Ce site a déjà émis des numéros portant son code : le code ne peut plus changer",
                code="site_code_locked",
            )
        before = {k: getattr(site, k) for k in changes}
        for key, value in changes.items():
            setattr(site, key, value)
        self._flush()
        record_audit(
            self.db,
            action="site.updated",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            entity_type="site",
            entity_id=site.id,
            data={"before": _jsonable(before), "after": _jsonable(changes)},
            meta=self.ctx.meta,
        )
        return site


class ModuleService:
    """Modules par site (palier C) : l'activation est une configuration de CHAQUE site
    (``site_modules``), dans les limites du profil du site et de l'abonnement du site.
    PLAN ≠ PROFIL ≠ ACTIVATION SITE ; ``tenant_modules`` (legacy) n'est ni lu ni écrit."""

    def __init__(self, db: Session, ctx: RequestContext, registry: ModuleRegistry) -> None:
        self.db = db
        self.ctx = ctx
        self.registry = registry
        self.capabilities = CapabilityService(db, registry)

    def _offers(self) -> list[tuple[uuid.UUID | None, BusinessProfile, PlanTerms]]:
        """Site, profil et conditions de chaque abonnement de l'entreprise : profil de SON site
        (abonnement d'inscription non rattaché : profil d'origine) et conditions en vigueur
        (licence, sinon plan)."""
        return [
            (
                s.site_id,
                self.capabilities.subscription_profile(s, self.ctx.tenant),
                current_terms(self.db, s),
            )
            for s in tenant_subscriptions(self.db)
        ]

    def list_all(self) -> list[ModuleOut]:
        """Synthèse de l'entreprise (lecture) : proposé par le profil d'au moins un site, inclus
        dans au moins un abonnement ; « activé » = activé sur au moins un site ACCESSIBLE
        (entreprise sans site : défauts du profil d'origine) ; ``effective`` : contexte de la
        requête."""
        offers = self._offers()
        in_profile = {m.module_code for _, profile, _ in offers for m in profile.modules}
        in_plan = {code for _, _, terms in offers for code in terms.modules}
        accessible = self.ctx.capabilities.accessible_site_ids
        site_ids = [site for site, _, _ in offers if site is not None and site in accessible]
        enabled: set[str] = set()
        if site_ids:
            enabled = set(
                self.db.scalars(
                    select(SiteModule.module_code).where(
                        SiteModule.site_id.in_(site_ids), SiteModule.enabled.is_(True)
                    )
                )
            )
        elif all(site is None for site, _, _ in offers):
            enabled = {
                c
                for _, profile, _ in offers
                for c in CapabilityService.default_module_codes(profile)
            }
        effective = self.ctx.capabilities.modules
        return [
            ModuleOut(
                code=m.code,
                status=m.status.value,
                core=m.core,
                depends_on=list(m.depends_on),
                in_profile=m.core or m.code in in_profile,
                in_plan=m.core or m.code in in_plan,
                enabled=m.core or m.code in enabled,
                effective=m.code in effective,
            )
            for m in self.registry.all()
            if m.core or m.code in in_profile
        ]

    def _site_offer(self, site_id: uuid.UUID) -> tuple[Site, BusinessProfile, PlanTerms]:
        site = SiteService(self.db, self.ctx).get(site_id)  # RLS : autre tenant introuvable
        if site.id not in self.ctx.capabilities.accessible_site_ids:
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")
        subscription = site_subscription(self.db, site.id)
        if subscription is None:
            raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
        return (
            site,
            self.capabilities.site_profile(site.id),
            current_terms(self.db, subscription),
        )

    def list_for_site(self, site_id: uuid.UUID) -> list[SiteModuleOut]:
        """Modules d'UN site : ``in_profile`` (profil du site), ``in_plan`` (abonnement du
        site), ``activated_for_site`` (``site_modules``), ``effective`` (les trois, dépendances
        résolues : capacités du site)."""
        site, profile, terms = self._site_offer(site_id)
        effective = self.ctx.site_capabilities(site.id).modules
        in_profile = {m.module_code for m in profile.modules}
        activated = self.capabilities.site_module_codes(site.id)
        return [
            SiteModuleOut(
                code=m.code,
                status=m.status.value,
                core=m.core,
                depends_on=list(m.depends_on),
                in_profile=m.core or m.code in in_profile,
                in_plan=m.core or m.code in terms.modules,
                activated_for_site=m.core or m.code in activated,
                effective=m.code in effective,
            )
            for m in self.registry.all()
            if m.core or m.code in in_profile
        ]

    def _ensure_manage(self, site_id: uuid.UUID) -> None:
        """``organization.module.manage`` détenue SUR CE SITE (rôles de l'entreprise ou du
        site), filtrée par le statut de l'abonnement du site."""
        capabilities = self.ctx.site_capabilities(site_id)
        if MODULE_MANAGE in capabilities.permissions:
            return
        extra = {"site_id": str(site_id)}
        if MODULE_MANAGE in capabilities.restricted_permissions:
            raise ForbiddenError(
                "Action indisponible avec le statut de l'abonnement de ce site",
                code="subscription_restricted",
                extra=extra,
            )
        raise ForbiddenError(
            "Permission insuffisante sur ce site", code="permission_denied", extra=extra
        )

    def set_enabled_for_site(self, site_id: uuid.UUID, code: str, enabled: bool) -> None:
        """Active / désactive un module sur CE site seulement (aucun autre site touché). Seul
        point d'activation explicite d'un module (``PUT /sites/{id}/modules/{code}`` ; la route
        de l'entreprise est retirée, la console n'active aucun module). Verrou du site d'abord
        (même ordre que le changement de profil : site → ``site_modules`` → verrous du module) ;
        un module « Bientôt disponible » n'est jamais activé (``module_not_implemented``,
        palier E.1) ; une désactivation est refusée tant qu'une opération en cours du module
        deviendrait impossible (session de caisse ouverte… ; palier D)."""
        site, profile, terms = self._site_offer(site_id)
        self._ensure_manage(site.id)
        lock_site(self.db, site.id)
        if code not in self.registry or self.registry.get(code).core:
            raise BusinessRuleError("Module non paramétrable", code="module_not_configurable")
        if enabled and code not in self.capabilities.offered_modules(profile, terms):
            # Le plan reste la limite : un module hors profil du site ou hors abonnement du
            # site n'est jamais activé.
            raise BusinessRuleError(
                "Module non inclus dans le profil ou l'abonnement de ce site",
                code="module_not_offered",
            )
        if enabled and self.registry.get(code).status is not ModuleStatus.AVAILABLE:
            # Palier E.1 : un module déclaré mais non implémenté (« Bientôt disponible »,
            # ``ModuleStatus.PLANNED``) reste visible au catalogue mais n'est jamais activé,
            # même proposé par le profil et l'abonnement du site, quel que soit le client
            # (l'interface n'est pas une frontière de sécurité). Contrôlé après l'offre : hors
            # plan et non proposé gardent leur code (``module_not_offered``).
            raise BusinessRuleError(
                "Ce module n'est pas encore disponible", code="module_not_implemented"
            )
        currently = self.capabilities.site_module_codes(site.id) | self.registry.core_codes()
        if enabled:
            missing = [d for d in self.registry.get(code).depends_on if d not in currently]
            if missing:
                raise ConflictError(
                    "Modules requis non activés sur ce site",
                    code="module_dependency_missing",
                    extra={"missing": missing},
                )
        else:
            # Seuls les modules DISPONIBLES comptent : l'activation inerte d'un module non livré
            # (défaut du profil écrit pendant sa période « Bientôt disponible », E.1) ne bloque
            # jamais la désactivation d'un module livré (ADR-0049, D10). Ses lignes restent
            # inchangées ; sa migration de livraison les remet à ``false``.
            dependents = sorted(
                d
                for d in self.registry.dependents_of(code) & currently
                if self.registry.get(d).status is ModuleStatus.AVAILABLE
            )
            if dependents:
                raise ConflictError(
                    "D'autres modules activés sur ce site en dépendent",
                    code="module_has_dependents",
                    extra={"dependents": dependents},
                )
            active = module_footprint(self.registry, self.db, code, site.id, lock=True).active
            if active:
                raise ConflictError(
                    "Des opérations de ce module sont en cours sur ce site : terminez-les avant "
                    "de le désactiver",
                    code="module_has_open_operations",
                    extra={"module": code, "operations": dict(active)},
                )
        row = self.db.scalars(
            select(SiteModule)
            .where(SiteModule.site_id == site.id, SiteModule.module_code == code)
            .with_for_update()
        ).one_or_none()
        if row is None:
            row = SiteModule(
                tenant_id=self.ctx.tenant_id, site_id=site.id, module_code=code, enabled=enabled
            )
            self.db.add(row)
        previous = row.enabled if row.id is not None else None
        row.enabled = enabled
        self.db.flush()
        record_audit(
            self.db,
            action="module.enabled" if enabled else "module.disabled",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            site_id=site.id,
            entity_type="module",
            entity_id=code,
            data={"site_id": str(site.id), "previous": previous, "enabled": enabled},
            meta=self.ctx.meta,
        )


def _jsonable(values: dict[str, object]) -> dict[str, object]:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in values.items()}
