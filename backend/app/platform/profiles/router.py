import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.platform.catalog.models import BusinessProfile
from app.platform.context import DbSession, RegistryDep, RequestContext, require_permission
from app.platform.footprint import COUNT_CAP
from app.platform.onboarding.service import OnboardingService
from app.platform.profiles.registry import BusinessProfileRegistry
from app.platform.profiles.schemas import (
    BusinessProfileCatalogOut,
    BusinessProfileChange,
    BusinessProfileDetail,
    BusinessProfileOut,
    DefaultUxOut,
    FootprintItemOut,
    ModuleStateOut,
    PlanCompatibilityOut,
    ProfileChangeSummaryOut,
    ProfileModuleChangeOut,
    ProfileRefOut,
    SectorOut,
    SiteProfileChangeIn,
    SiteProfileChangeOut,
    SiteProfilePreviewOut,
)
from app.platform.profiles.service import change_business_profile
from app.platform.profiles.site_change import (
    CONFIRMATION_TEXT,
    ChangeLevel,
    FootprintItem,
    ProfileChangeBlockedError,
    ProfileChangePlan,
    SiteProfileChangeService,
)
from app.platform.tenancy.schemas import TenantOut

router = APIRouter(tags=["business-profiles"])

ProfileView = Annotated[RequestContext, Depends(require_permission("organization.profile.view"))]
ProfileManage = Annotated[
    RequestContext, Depends(require_permission("organization.profile.manage"))
]


def _out(profile: BusinessProfile) -> BusinessProfileOut:
    return BusinessProfileOut(
        code=profile.code,
        name=profile.name,
        description=profile.description,
        sector=profile.sector_code,
        ux_profile=profile.ux_profile_code,
        sort_order=profile.sort_order,
        is_active=profile.is_active,
        default_modules=[m.module_code for m in profile.modules if m.default_enabled],
        optional_modules=[m.module_code for m in profile.modules if not m.default_enabled],
    )


@router.get("/business-profiles", response_model=BusinessProfileCatalogOut)
def list_business_profiles(
    ctx: ProfileView, db: DbSession, registry: RegistryDep
) -> BusinessProfileCatalogOut:
    """Catalogue des profils actifs, groupables par secteur (catalogue global, identique pour
    tous les tenants)."""
    profiles = BusinessProfileRegistry(db, registry)
    return BusinessProfileCatalogOut(
        sectors=[SectorOut.model_validate(s) for s in profiles.list_sectors()],
        profiles=[_out(p) for p in profiles.list_profiles()],
    )


@router.get("/business-profiles/{code}", response_model=BusinessProfileDetail)
def get_business_profile(
    code: str, ctx: ProfileView, db: DbSession, registry: RegistryDep
) -> BusinessProfileDetail:
    """Configuration **par défaut** d'un profil (ce qu'il propose). L'expérience effective du
    tenant (plan, activations, permissions) est dans ``GET /me/capabilities``."""
    profiles = BusinessProfileRegistry(db, registry)
    profile = profiles.get(code)
    return BusinessProfileDetail(
        **_out(profile).model_dump(),
        ux=DefaultUxOut.from_config(profile.ux_profile_code, profiles.default_ux(profile)),
        upcoming=list(profiles.upcoming(profile)),
    )


@router.put("/tenant/business-profile", response_model=TenantOut)
def change_tenant_business_profile(
    body: BusinessProfileChange, ctx: ProfileManage, db: DbSession, registry: RegistryDep
) -> TenantOut:
    """Profil d'origine du tenant du jeton (jamais d'un autre), modifiable seulement tant que
    l'entreprise n'a aucun site : le profil d'activité est ensuite propre à chaque site (D2,
    ``409 profile_is_per_site``, règle du service commune à la CLI). Contrôlé, audité, sans
    suppression de données."""
    change_business_profile(
        db,
        registry,
        ctx.tenant,
        body.code,
        actor="api",
        user_id=ctx.user.id,
        meta=ctx.meta,
    )
    OnboardingService(db, registry).refresh_for(ctx, "business_profile.changed")
    db.commit()
    return TenantOut.model_validate(ctx.tenant)


# --- Profil d'un site (palier D, ADR-0048) ---------------------------------------------------


def _ref(profile: BusinessProfile) -> ProfileRefOut:
    return ProfileRefOut(code=profile.code, name=profile.name, sector=profile.sector_code)


def _items(items: list[FootprintItem]) -> list[FootprintItemOut]:
    return [
        FootprintItemOut(
            kind=i.kind,
            module=i.module,
            count=min(i.count, COUNT_CAP),
            capped=i.capped,
            blocking=i.blocking,
        )
        for i in items
    ]


def _preview_out(plan: ProfileChangePlan) -> SiteProfilePreviewOut:
    not_in_plan = plan.modules_not_in_plan
    return SiteProfilePreviewOut(
        site_id=str(plan.site.id),
        site_name=plan.site.name,
        current_profile=_ref(plan.current),
        target_profile=_ref(plan.target),
        level=plan.level.value,
        fingerprint=plan.fingerprint,
        confirmation_text=CONFIRMATION_TEXT if plan.level is ChangeLevel.STRONG else None,
        plan=PlanCompatibilityOut(
            code=plan.plan_code,
            compatibility="PARTIAL" if not_in_plan else "FULL",
            modules_not_in_plan=not_in_plan,
        ),
        modules=[
            ProfileModuleChangeOut(
                code=m.code,
                status=m.status,
                change=m.change,
                action=m.action,
                reason=m.reason,
                before=ModuleStateOut(**vars(m.before)),
                after=ModuleStateOut(**vars(m.after)),
            )
            for m in plan.modules
        ],
        summary=ProfileChangeSummaryOut(
            added=plan.codes(change="added"),
            removed=plan.codes(change="removed"),
            kept=plan.codes(change="kept"),
            not_in_plan=not_in_plan,
            activated=plan.codes(action="enable"),
            deactivated=plan.codes(action="disable"),
        ),
        history=_items(plan.history),
        open_operations=_items(plan.open_operations),
        blockers=_items(plan.blockers),
        configuration=_items(plan.configuration),
    )


@router.get("/sites/{site_id}/business-profile/preview", response_model=SiteProfilePreviewOut)
def preview_site_business_profile(
    site_id: uuid.UUID,
    ctx: ProfileManage,
    db: DbSession,
    registry: RegistryDep,
    profile_code: Annotated[str, Query(min_length=1, max_length=50)],
) -> SiteProfilePreviewOut:
    """Aperçu du changement de profil d'UN site (portée dans l'URL, jamais ``X-Site-Id``) :
    niveau SIMPLE / STRONG / BLOCKED, modules, abonnement, données du site. Aucune écriture."""
    plan = SiteProfileChangeService(db, ctx, registry).preview(site_id, profile_code)
    return _preview_out(plan)


@router.put("/sites/{site_id}/business-profile", response_model=SiteProfileChangeOut)
def change_site_business_profile(
    site_id: uuid.UUID,
    body: SiteProfileChangeIn,
    ctx: ProfileManage,
    db: DbSession,
    registry: RegistryDep,
) -> SiteProfileChangeOut:
    """Change le profil de CE site seulement (transaction unique, sous verrou du site) ; un
    refus BLOCKED est journalisé dans sa propre transaction."""
    service = SiteProfileChangeService(db, ctx, registry)
    try:
        plan = service.change(
            site_id, body.profile_code, body.preview_fingerprint, body.confirmation
        )
    except ProfileChangeBlockedError as exc:
        db.rollback()
        service.record_refusal(exc.refusal)
        db.commit()
        raise
    OnboardingService(db, registry).refresh_for(ctx, "site.profile_changed")
    db.commit()
    return SiteProfileChangeOut(
        site_id=str(plan.site.id),
        previous_profile=plan.current.code,
        business_profile_code=plan.target.code,
        level=plan.level.value,
        activated=plan.codes(action="enable"),
        deactivated=plan.codes(action="disable"),
    )
