"""API de la console TechNova (processus distinct : ``app.console.main``).

Toutes les routes, sauf la connexion, exigent un administrateur de la plateforme
(``PlatformAdmin``). Aucune ne dépend d'un tenant ni n'accède à ses données.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select

from app.console.auth import (
    ConsoleAuthService,
    ConsoleDb,
    ConsoleSettings,
    MetaDep,
    NowDep,
    PlatformAdmin,
    require_console_header,
)
from app.console.catalog import active_countries, build_catalog
from app.console.models import PlatformAuditLog
from app.console.plans import PlanCommercialService
from app.console.schemas import (
    ActivationIn,
    CatalogOut,
    DashboardOut,
    ExtensionIn,
    LoginIn,
    PlanChangeIn,
    PlanChoice,
    PlanCommercialUpdate,
    PlanDetailOut,
    PlanOut,
    PlatformAdminOut,
    PlatformAuditOut,
    PlatformDashboardTenants,
    ReasonIn,
    TenantActions,
    TenantDetailOut,
    TenantListItem,
    TenantSubscriptionOut,
    TenantUsage,
)
from app.console.tenants import ACTIVABLE, EXTENDABLE, TenantAdminService, TenantFilters
from app.core.config import Settings
from app.platform.catalog.models import BusinessProfile, Plan
from app.platform.registry import ModuleRegistry, ModuleStatus, get_registry
from app.platform.signup.service import self_service
from app.platform.subscriptions.models import SubscriptionStatus
from app.platform.tenancy.models import TenantStatus
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(dependencies=[Depends(require_console_header)])

Registry = Annotated[ModuleRegistry, Depends(get_registry)]


def _cookie_path(settings: Settings) -> str:
    return settings.platform_api_prefix


def _plan_out(plan: Plan) -> PlanOut:
    fields = {name: getattr(plan, name) for name in PlanOut.model_fields if name != "self_service"}
    return PlanOut(**fields, self_service=self_service(plan))


# --- Session ------------------------------------------------------------------------------------


@router.post("/auth/login", response_model=PlatformAdminOut, tags=["console-auth"])
def login(
    body: LoginIn,
    response: Response,
    db: ConsoleDb,
    settings: ConsoleSettings,
    now: NowDep,
    meta: MetaDep,
) -> PlatformAdminOut:
    user, token = ConsoleAuthService(db, settings, now).login(body.email, body.password, meta)
    db.commit()
    response.set_cookie(
        settings.platform_cookie_name,
        token,
        max_age=settings.platform_session_ttl_minutes * 60,
        httponly=True,
        secure=settings.platform_cookie_secure,
        samesite="strict",
        path=_cookie_path(settings),
    )
    response.headers["Cache-Control"] = "no-store"
    return PlatformAdminOut.model_validate(user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["console-auth"])
def logout(
    request: Request, db: ConsoleDb, settings: ConsoleSettings, now: NowDep, meta: MetaDep
) -> Response:
    ConsoleAuthService(db, settings, now).logout(
        request.cookies.get(settings.platform_cookie_name), meta
    )
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        settings.platform_cookie_name,
        path=_cookie_path(settings),
        secure=settings.platform_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return response


@router.get("/me", response_model=PlatformAdminOut, tags=["console-auth"])
def me(ctx: PlatformAdmin) -> PlatformAdminOut:
    return PlatformAdminOut.model_validate(ctx.user)


# --- Tableau de bord ----------------------------------------------------------------------------


@router.get("/dashboard", response_model=DashboardOut, tags=["console"])
def dashboard(ctx: PlatformAdmin, db: ConsoleDb, registry: Registry, now: NowDep) -> DashboardOut:
    """Indicateurs de la plateforme : offres, catalogue et agrégats des tenants et abonnements
    (comptages en base ; aucune donnée métier)."""
    plans = PlanCommercialService(db, registry).list()
    active = [p for p in plans if p.is_active]
    manifests = registry.all()
    return DashboardOut(
        admin=PlatformAdminOut.model_validate(ctx.user),
        plans_total=len(plans),
        plans_active=len(active),
        plans_listed=sum(1 for p in active if p.listed),
        plans_self_service=sum(1 for p in active if self_service(p)),
        plans_contact_required=sum(1 for p in active if p.listed and p.contact_required),
        modules_available=sum(1 for m in manifests if m.status is ModuleStatus.AVAILABLE),
        modules_planned=sum(1 for m in manifests if m.status is ModuleStatus.PLANNED),
        permissions=sum(len(m.permissions) for m in manifests),
        profiles_active=db.scalar(select(func.count()).where(BusinessProfile.is_active.is_(True)))
        or 0,
        active_countries=active_countries(db),
        tenants=PlatformDashboardTenants(**TenantAdminService(db, registry, now).counts()),
    )


# --- Offres & tarifs ----------------------------------------------------------------------------


@router.get("/plans", response_model=list[PlanOut], tags=["console-plans"])
def list_plans(ctx: PlatformAdmin, db: ConsoleDb, registry: Registry) -> list[PlanOut]:
    return [_plan_out(p) for p in PlanCommercialService(db, registry).list()]


def _detail(service: PlanCommercialService, plan: Plan) -> PlanDetailOut:
    return PlanDetailOut(**_plan_out(plan).model_dump(), structure=service.structure(plan))


@router.get("/plans/{code}", response_model=PlanDetailOut, tags=["console-plans"])
def get_plan(code: str, ctx: PlatformAdmin, db: ConsoleDb, registry: Registry) -> PlanDetailOut:
    service = PlanCommercialService(db, registry)
    return _detail(service, service.get(code))


@router.patch("/plans/{code}/commercial", response_model=PlanDetailOut, tags=["console-plans"])
def update_plan_commercial(
    code: str,
    body: PlanCommercialUpdate,
    ctx: PlatformAdmin,
    db: ConsoleDb,
    registry: Registry,
) -> PlanDetailOut:
    """Modifie les paramètres commerciaux d'un plan (raison obligatoire, audit avant/après).
    La structure technique n'est pas modifiable (``422 validation_error`` sur tout autre
    champ)."""
    service = PlanCommercialService(db, registry)
    plan = service.update_commercial(code, body, ctx.actor, ctx.meta)
    db.commit()
    return _detail(service, plan)


# --- Catalogue technique ------------------------------------------------------------------------


@router.get("/catalog", response_model=CatalogOut, tags=["console-catalog"])
def catalog(ctx: PlatformAdmin, db: ConsoleDb, registry: Registry) -> CatalogOut:
    return build_catalog(db, registry)


# --- Journal de la plateforme -------------------------------------------------------------------


@router.get("/audit", response_model=Page[PlatformAuditOut], tags=["console-audit"])
def list_audit(
    ctx: PlatformAdmin,
    db: ConsoleDb,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: Annotated[str | None, Query(max_length=100)] = None,
    target_type: Annotated[str | None, Query(max_length=50)] = None,
    target_id: Annotated[str | None, Query(max_length=64)] = None,
    tenant_id: uuid.UUID | None = None,
) -> Page[PlatformAuditOut]:
    conditions = []
    if tenant_id:
        conditions.append(PlatformAuditLog.tenant_id == tenant_id)
    if action:
        conditions.append(PlatformAuditLog.action.startswith(action, autoescape=True))
    if target_type:
        conditions.append(PlatformAuditLog.target_type == target_type)
    if target_id:
        conditions.append(PlatformAuditLog.target_id == target_id)
    total = db.scalar(select(func.count()).select_from(PlatformAuditLog).where(*conditions)) or 0
    rows = db.scalars(
        select(PlatformAuditLog)
        .where(*conditions)
        .order_by(PlatformAuditLog.occurred_at.desc(), PlatformAuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[PlatformAuditOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# --- Tenants et abonnements (Phase 3.2-G) -------------------------------------------------------


@router.get("/tenants", response_model=Page[TenantListItem], tags=["console-tenants"])
def list_tenants(
    ctx: PlatformAdmin,
    db: ConsoleDb,
    registry: Registry,
    now: NowDep,
    params: Annotated[PageParams, Depends(page_params)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    status: TenantStatus | None = None,
    plan_code: Annotated[str | None, Query(max_length=50)] = None,
    subscription_status: SubscriptionStatus | None = None,
) -> Page[TenantListItem]:
    """Entreprises clientes : métadonnées plateforme seulement (paginées, triées, filtrées côté
    serveur ; ``subscription_status`` filtre le statut **effectif**). Tri : ``name`` (défaut),
    ``created_at``, ``current_period_end``, ``status``."""
    rows, total = TenantAdminService(db, registry, now).search(
        TenantFilters(
            search=search,
            status=status,
            plan_code=plan_code,
            subscription_status=subscription_status,
        ),
        params,
    )
    return Page(
        items=[TenantListItem.model_validate(row, from_attributes=True) for row in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


def _tenant_detail(service: TenantAdminService, tenant_id: uuid.UUID) -> TenantDetailOut:
    row = service.row(tenant_id)
    identity = service.identity(tenant_id)
    subscription = service.subscription(tenant_id)
    plan = service.plan(subscription.plan_code)
    effective = service.effective(subscription)
    limits = service.limits(plan)
    used = {"max_sites": row.sites, "max_users": row.users}
    start, end = service.activation_proposal(subscription, identity.timezone)
    plans = [
        PlanChoice(code=p.code, name=p.name)
        for p in PlanCommercialService(service.db, service.registry).list()
        if p.is_active and p.code != subscription.plan_code
    ]
    return TenantDetailOut(
        **TenantListItem.model_validate(row, from_attributes=True).model_dump(),
        country_code=identity.country_code,
        country_name=identity.country_name,
        currency=identity.currency,
        locale=identity.locale,
        timezone=identity.timezone,
        usage={
            code: TenantUsage(used=used.get(code, 0), limit=limit) for code, limit in limits.items()
        },
        subscription=TenantSubscriptionOut(
            id=subscription.id,
            plan_code=plan.code,
            plan_name=plan.name,
            billing_period=subscription.billing_period.value,
            status=subscription.status.value,
            effective_status=effective.value,
            started_at=subscription.started_at,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            cancelled_at=subscription.cancelled_at,
            grace_days=plan.grace_days,
            price_at_subscription=subscription.price_at_subscription,
            currency_at_subscription=subscription.currency_at_subscription,
        ),
        actions=TenantActions(
            can_suspend=identity.status is TenantStatus.ACTIVE,
            can_reactivate=identity.status is TenantStatus.SUSPENDED,
            can_activate=subscription.status in ACTIVABLE,
            can_extend=subscription.status in EXTENDABLE,
            can_change_plan=bool(plans),
            activation_start=start,
            activation_end=end,
            extension_end=service.extension_proposal(subscription, identity.timezone),
            available_plans=plans,
        ),
    )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailOut, tags=["console-tenants"])
def get_tenant(
    tenant_id: uuid.UUID, ctx: PlatformAdmin, db: ConsoleDb, registry: Registry, now: NowDep
) -> TenantDetailOut:
    return _tenant_detail(TenantAdminService(db, registry, now), tenant_id)


@router.post(
    "/tenants/{tenant_id}/suspend", response_model=TenantDetailOut, tags=["console-tenants"]
)
def suspend_tenant(
    tenant_id: uuid.UUID,
    body: ReasonIn,
    ctx: PlatformAdmin,
    db: ConsoleDb,
    registry: Registry,
    now: NowDep,
) -> TenantDetailOut:
    """Suspend l'entreprise (accès refusé à tous ses utilisateurs ; aucune donnée supprimée)."""
    service = TenantAdminService(db, registry, now)
    service.suspend(tenant_id, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _tenant_detail(service, tenant_id)


@router.post(
    "/tenants/{tenant_id}/reactivate", response_model=TenantDetailOut, tags=["console-tenants"]
)
def reactivate_tenant(
    tenant_id: uuid.UUID,
    body: ReasonIn,
    ctx: PlatformAdmin,
    db: ConsoleDb,
    registry: Registry,
    now: NowDep,
) -> TenantDetailOut:
    service = TenantAdminService(db, registry, now)
    service.reactivate(tenant_id, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _tenant_detail(service, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/activate",
    response_model=TenantDetailOut,
    tags=["console-tenants"],
)
def activate_subscription(
    tenant_id: uuid.UUID,
    body: ActivationIn,
    ctx: PlatformAdmin,
    db: ConsoleDb,
    registry: Registry,
    now: NowDep,
) -> TenantDetailOut:
    """Activation manuelle **transitoire** (en attente d'activation ou essai → actif) : aucun
    paiement n'est créé ni confirmé ; remplacée par l'activation par licence (3.3-B)."""
    service = TenantAdminService(db, registry, now)
    service.activate(
        tenant_id,
        period_start=body.period_start,
        period_end_on=body.period_end,
        reason=body.reason,
        actor=ctx.actor,
        meta=ctx.meta,
    )
    db.commit()
    return _tenant_detail(service, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/extend",
    response_model=TenantDetailOut,
    tags=["console-tenants"],
)
def extend_subscription(
    tenant_id: uuid.UUID,
    body: ExtensionIn,
    ctx: PlatformAdmin,
    db: ConsoleDb,
    registry: Registry,
    now: NowDep,
) -> TenantDetailOut:
    service = TenantAdminService(db, registry, now)
    service.extend(
        tenant_id,
        period_end_on=body.period_end,
        reason=body.reason,
        actor=ctx.actor,
        meta=ctx.meta,
    )
    db.commit()
    return _tenant_detail(service, tenant_id)


@router.post(
    "/tenants/{tenant_id}/subscription/change-plan",
    response_model=TenantDetailOut,
    tags=["console-tenants"],
)
def change_tenant_plan(
    tenant_id: uuid.UUID,
    body: PlanChangeIn,
    ctx: PlatformAdmin,
    db: ConsoleDb,
    registry: Registry,
    now: NowDep,
) -> TenantDetailOut:
    """Change le plan : prix et devise figés à ce moment ; période inchangée ; données
    conservées (ce que le nouveau plan n'inclut pas cesse d'être accordé)."""
    service = TenantAdminService(db, registry, now)
    service.change_plan(
        tenant_id, plan_code=body.plan_code, reason=body.reason, actor=ctx.actor, meta=ctx.meta
    )
    db.commit()
    return _tenant_detail(service, tenant_id)
