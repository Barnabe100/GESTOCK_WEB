import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.platform.catalog.models import BusinessProfile, Plan
from app.platform.context import DbSession, RegistryDep, TenantContext
from app.platform.identity.schemas import UserOut
from app.platform.subscriptions.plan_policy import PlanPolicy
from app.platform.subscriptions.service import get_subscription
from app.platform.tenancy.models import Site, SiteKind

router = APIRouter(tags=["capabilities"])


class NamedCode(BaseModel):
    code: str
    name: str


class TenantInfo(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    currency: str
    locale: str
    timezone: str


class SubscriptionInfo(BaseModel):
    status: str
    billing_period: str
    current_period_end: datetime
    allowed_access: list[str]


class SiteInfo(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    kind: SiteKind


class ModuleInfo(BaseModel):
    code: str
    status: str
    core: bool


class LimitInfo(BaseModel):
    limit: int | None
    used: int


class CapabilitiesOut(BaseModel):
    user: UserOut
    tenant: TenantInfo
    is_owner: bool
    profile: NamedCode
    plan: NamedCode
    subscription: SubscriptionInfo
    site: SiteInfo | None
    sites: list[SiteInfo]
    modules: list[ModuleInfo]
    permissions: list[str]
    restricted_permissions: list[str]
    navigation: list[str]
    terminology: dict[str, Any]
    features: list[str]
    limits: dict[str, LimitInfo]


@router.get("/me/capabilities", response_model=CapabilitiesOut)
def get_capabilities(ctx: TenantContext, db: DbSession, registry: RegistryDep) -> CapabilitiesOut:
    """Capacités effectives de l'utilisateur dans le tenant (et le site ``X-Site-Id``).

    Le frontend construit menus et routes à partir de cette réponse ; le backend applique
    indépendamment les mêmes règles sur chaque requête."""
    caps = ctx.capabilities
    profile = db.get(BusinessProfile, caps.profile_code)
    plan = db.get(Plan, caps.plan_code)
    subscription = get_subscription(db)
    assert profile is not None and plan is not None and subscription is not None
    sites = db.scalars(
        select(Site).where(Site.id.in_(caps.accessible_site_ids)).order_by(Site.name)
    ).all()

    def site_info(site: Site) -> SiteInfo:
        return SiteInfo(id=site.id, name=site.name, code=site.code, kind=site.kind)

    return CapabilitiesOut(
        user=UserOut.model_validate(ctx.user),
        tenant=TenantInfo.model_validate(ctx.tenant, from_attributes=True),
        is_owner=ctx.membership.is_owner,
        profile=NamedCode(code=profile.code, name=profile.name),
        plan=NamedCode(code=plan.code, name=plan.name),
        subscription=SubscriptionInfo(
            status=caps.subscription_status.value,
            billing_period=subscription.billing_period.value,
            current_period_end=subscription.current_period_end,
            allowed_access=sorted(caps.allowed_access),
        ),
        site=site_info(ctx.site) if ctx.site else None,
        sites=[site_info(s) for s in sites],
        modules=[
            ModuleInfo(code=m.code, status=m.status.value, core=m.core)
            for m in registry.all()
            if m.code in caps.modules
        ],
        permissions=sorted(caps.permissions),
        restricted_permissions=sorted(caps.restricted_permissions),
        navigation=list(caps.navigation),
        terminology=caps.terminology,
        features=sorted(caps.features),
        limits={
            code: LimitInfo(limit=usage.limit, used=usage.used)
            for code, usage in PlanPolicy(db, plan, registry).snapshot().items()
        },
    )
