import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.platform.audit.service import record_audit
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.models import BusinessProfile, GeoCountry, Plan
from app.platform.context import RequestContext
from app.platform.registry import ModuleRegistry, get_registry
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.service import current_plan
from app.platform.tenancy.models import Site, TenantModule
from app.platform.tenancy.schemas import ModuleOut, SiteCreate, SiteUpdate, TenantUpdate


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

    def _check_site_limit(self) -> None:
        PlanPolicy(self.db, current_plan(self.db), get_registry()).ensure_capacity("max_sites")

    def _flush(self) -> None:
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Ce code de site existe déjà", code="site_code_taken") from exc

    def create(self, data: SiteCreate) -> Site:
        self._check_site_limit()
        site = Site(tenant_id=self.ctx.tenant_id, **data.model_dump())
        self.db.add(site)
        self._flush()
        record_audit(
            self.db,
            action="site.created",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            entity_type="site",
            entity_id=site.id,
            data=data.model_dump(mode="json"),
            meta=self.ctx.meta,
        )
        return site

    def update(self, site_id: uuid.UUID, data: SiteUpdate) -> Site:
        site = self.get(site_id)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("is_active") and not site.is_active:
            self._check_site_limit()
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

    def _profile_and_plan(self) -> tuple[BusinessProfile, Plan]:
        profile = self.db.get(BusinessProfile, self.ctx.tenant.business_profile_code)
        if profile is None:
            raise BusinessRuleError("Profil introuvable", code="unknown_profile")
        return profile, current_plan(self.db)

    def list_all(self) -> list[ModuleOut]:
        profile, plan = self._profile_and_plan()
        in_profile = {m.module_code for m in profile.modules}
        in_plan = {m.module_code for m in plan.modules}
        enabled = self.capabilities.enabled_module_codes()
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

    def set_enabled(self, code: str, enabled: bool) -> None:
        if code not in self.registry or self.registry.get(code).core:
            raise BusinessRuleError("Module non paramétrable", code="module_not_configurable")
        profile, plan = self._profile_and_plan()
        if code not in self.capabilities.offered_modules(profile, plan):
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
