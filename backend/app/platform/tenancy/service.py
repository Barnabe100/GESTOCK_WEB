import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.platform.audit.service import record_audit
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.models import BusinessProfile, GeoCountry, Plan
from app.platform.context import RequestContext
from app.platform.licensing.service import current_terms
from app.platform.registry import ModuleRegistry
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
from app.platform.tenancy.models import Site, TenantModule
from app.platform.tenancy.schemas import ModuleOut, SiteCreate, SiteUpdate, TenantUpdate
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
        fields = data.model_dump(exclude={"plan_code", "billing_period", "requested_activations"})
        # Profil du site : celui de l'entreprise (profil par site, palier A ; le choix du
        # profil à la création viendra avec le palier B).
        site = Site(
            id=new_id(),
            tenant_id=self.ctx.tenant_id,
            business_profile_code=self.ctx.tenant.business_profile_code,
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
            data=fields | {"kind": site.kind.value},
            meta=self.ctx.meta,
        )
        return site

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
    def __init__(self, db: Session, ctx: RequestContext, registry: ModuleRegistry) -> None:
        self.db = db
        self.ctx = ctx
        self.registry = registry
        self.capabilities = CapabilityService(db, registry)

    def _offers(self) -> list[tuple[BusinessProfile, PlanTerms]]:
        """Profil et conditions de chaque abonnement de l'entreprise : profil de SON site
        (jamais celui du tenant ; abonnement d'inscription non rattaché : profil d'origine) et
        conditions en vigueur (licence, sinon plan). Un module est activable s'il est proposé
        par le profil ET inclus dans les conditions d'au moins un même abonnement."""
        return [
            (self.capabilities.subscription_profile(s, self.ctx.tenant), current_terms(self.db, s))
            for s in tenant_subscriptions(self.db)
        ]

    def _rows(
        self,
        in_profile: set[str],
        in_plan: set[str],
        effective: frozenset[str],
    ) -> list[ModuleOut]:
        enabled = self.capabilities.enabled_module_codes()
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

    def list_all(self) -> list[ModuleOut]:
        """Vue de l'entreprise : modules proposés par le profil d'au moins un site, inclus dans
        au moins un abonnement ; ``effective`` : contexte de la requête (site ou sites
        accessibles)."""
        offers = self._offers()
        in_profile = {m.module_code for profile, _ in offers for m in profile.modules}
        in_plan = {code for _, terms in offers for code in terms.modules}
        return self._rows(in_profile, in_plan, self.ctx.capabilities.modules)

    def list_for_site(self, site_id: uuid.UUID) -> list[ModuleOut]:
        """Modules d'UN site : ``in_profile`` selon le profil du site, ``in_plan`` selon les
        conditions de l'abonnement du site, ``effective`` selon les capacités de ce site."""
        site = SiteService(self.db, self.ctx).get(site_id)  # RLS : autre tenant introuvable
        capabilities = self.ctx.site_capabilities(site.id)  # site non accessible : refus
        subscription = site_subscription(self.db, site.id)
        if subscription is None:
            raise BusinessRuleError("Aucun abonnement", code="subscription_missing")
        profile = self.capabilities.site_profile(site.id)
        terms = current_terms(self.db, subscription)
        return self._rows(
            {m.module_code for m in profile.modules}, set(terms.modules), capabilities.modules
        )

    def set_enabled(self, code: str, enabled: bool) -> None:
        if code not in self.registry or self.registry.get(code).core:
            raise BusinessRuleError("Module non paramétrable", code="module_not_configurable")
        if not any(
            code in self.capabilities.offered_modules(profile, terms)
            for profile, terms in self._offers()
        ):
            raise BusinessRuleError(
                "Module non inclus dans votre profil ou votre abonnement",
                code="module_not_offered",
            )
        currently = self.capabilities.enabled_module_codes() | self.registry.core_codes()
        if enabled:
            missing = [d for d in self.registry.get(code).depends_on if d not in currently]
            if missing:
                raise ConflictError(
                    "Modules requis non activés",
                    code="module_dependency_missing",
                    extra={"missing": missing},
                )
        else:
            dependents = sorted(self.registry.dependents_of(code) & currently)
            if dependents:
                raise ConflictError(
                    "D'autres modules activés en dépendent",
                    code="module_has_dependents",
                    extra={"dependents": dependents},
                )
        row = self.db.get(TenantModule, (self.ctx.tenant_id, code))
        if row is None:
            row = TenantModule(tenant_id=self.ctx.tenant_id, module_code=code)
            self.db.add(row)
        row.enabled = enabled
        record_audit(
            self.db,
            action="module.enabled" if enabled else "module.disabled",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            entity_type="module",
            entity_id=code,
            meta=self.ctx.meta,
        )


def _jsonable(values: dict[str, object]) -> dict[str, object]:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in values.items()}
