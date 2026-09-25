from typing import Annotated

from fastapi import APIRouter, Depends

from app.platform.catalog.models import BusinessProfile
from app.platform.context import DbSession, RegistryDep, RequestContext, require_permission
from app.platform.onboarding.service import OnboardingService
from app.platform.profiles.registry import BusinessProfileRegistry
from app.platform.profiles.schemas import (
    BusinessProfileCatalogOut,
    BusinessProfileChange,
    BusinessProfileDetail,
    BusinessProfileOut,
    DefaultUxOut,
    SectorOut,
)
from app.platform.profiles.service import change_business_profile
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
    """Change le profil d'activité du tenant du jeton (jamais d'un autre) : contrôlé, audité,
    sans suppression de données."""
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
