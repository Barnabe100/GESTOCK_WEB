"""API de la console TechNova (processus distinct : ``app.console.main``).

Toutes les routes, sauf la connexion, exigent un administrateur de la plateforme
(``PlatformAdmin``). Aucune ne dépend d'un tenant ni n'accède à ses données.
"""

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
    CatalogOut,
    DashboardOut,
    LoginIn,
    PlanCommercialUpdate,
    PlanDetailOut,
    PlanOut,
    PlatformAdminOut,
    PlatformAuditOut,
)
from app.core.config import Settings
from app.platform.catalog.models import BusinessProfile, Plan
from app.platform.registry import ModuleRegistry, ModuleStatus, get_registry
from app.platform.signup.service import self_service
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
def dashboard(ctx: PlatformAdmin, db: ConsoleDb, registry: Registry) -> DashboardOut:
    """Indicateurs de la plateforme (offres et catalogue). Aucune donnée de tenant."""
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
) -> Page[PlatformAuditOut]:
    conditions = []
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
