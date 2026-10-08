import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.errors import ConflictError
from app.platform.context import DbSession, RegistryDep, RequestContext, require_permission
from app.platform.onboarding.service import OnboardingService
from app.platform.tenancy.identity import document_identity
from app.platform.tenancy.schemas import (
    DocumentIdentityOut,
    ModuleOut,
    ModuleToggle,
    SiteCreate,
    SiteModuleOut,
    SiteOut,
    SiteUpdate,
    TenantOut,
    TenantUpdate,
)
from app.platform.tenancy.service import ModuleService, SiteService, TenantService

router = APIRouter(tags=["organization"])


TenantView = Annotated[RequestContext, Depends(require_permission("organization.tenant.view"))]
TenantUpdateCtx = Annotated[
    RequestContext, Depends(require_permission("organization.tenant.update"))
]
SiteView = Annotated[RequestContext, Depends(require_permission("organization.site.view"))]
SiteManage = Annotated[RequestContext, Depends(require_permission("organization.site.manage"))]
ModuleView = Annotated[RequestContext, Depends(require_permission("organization.module.view"))]
ModuleManage = Annotated[RequestContext, Depends(require_permission("organization.module.manage"))]


@router.get("/tenant", response_model=TenantOut)
def get_tenant(ctx: TenantView) -> TenantOut:
    return TenantOut.model_validate(ctx.tenant)


@router.get("/tenant/document-identity", response_model=DocumentIdentityOut)
def get_document_identity(ctx: TenantView, db: DbSession) -> DocumentIdentityOut:
    """Identité de l'entreprise telle qu'elle figurera sur les documents (source : tenant)."""
    return DocumentIdentityOut.model_validate(document_identity(db, ctx.tenant))


@router.patch("/tenant", response_model=TenantOut)
def update_tenant(
    body: TenantUpdate, ctx: TenantUpdateCtx, db: DbSession, registry: RegistryDep
) -> TenantOut:
    TenantService(db, ctx).update(body)
    OnboardingService(db, registry).refresh_for(ctx, "tenant.updated")
    db.commit()
    return TenantOut.model_validate(ctx.tenant)


@router.get("/sites", response_model=list[SiteOut])
def list_sites(ctx: SiteView, db: DbSession) -> list[SiteOut]:
    return [SiteOut.model_validate(s) for s in SiteService(db, ctx).list_all()]


@router.post("/sites", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
def create_site(body: SiteCreate, ctx: SiteManage, db: DbSession, registry: RegistryDep) -> SiteOut:
    site = SiteService(db, ctx).create(body)
    OnboardingService(db, registry).refresh_for(ctx, "site.created")
    db.commit()
    return SiteOut.model_validate(site)


@router.get("/sites/{site_id}", response_model=SiteOut)
def get_site(site_id: uuid.UUID, ctx: SiteView, db: DbSession) -> SiteOut:
    return SiteOut.model_validate(SiteService(db, ctx).get(site_id))


@router.patch("/sites/{site_id}", response_model=SiteOut)
def update_site(
    site_id: uuid.UUID, body: SiteUpdate, ctx: SiteManage, db: DbSession, registry: RegistryDep
) -> SiteOut:
    site = SiteService(db, ctx).update(site_id, body)
    OnboardingService(db, registry).refresh_for(ctx, "site.updated")
    db.commit()
    return SiteOut.model_validate(site)


@router.get("/modules", response_model=list[ModuleOut])
def list_modules(ctx: ModuleView, db: DbSession, registry: RegistryDep) -> list[ModuleOut]:
    return ModuleService(db, ctx, registry).list_all()


@router.get("/sites/{site_id}/modules", response_model=list[SiteModuleOut])
def list_site_modules(
    site_id: uuid.UUID, ctx: ModuleView, db: DbSession, registry: RegistryDep
) -> list[SiteModuleOut]:
    """Modules d'un site : profil du site, abonnement du site, activation du site (palier C)."""
    return ModuleService(db, ctx, registry).list_for_site(site_id)


@router.put("/sites/{site_id}/modules/{code}", status_code=status.HTTP_204_NO_CONTENT)
def toggle_site_module(
    site_id: uuid.UUID,
    code: str,
    body: ModuleToggle,
    ctx: ModuleManage,
    db: DbSession,
    registry: RegistryDep,
) -> None:
    """Active / désactive un module sur CE site seulement (portée explicite dans l'URL) ;
    permission revérifiée pour ce site, profil et abonnement du site contrôlés."""
    ModuleService(db, ctx, registry).set_enabled_for_site(site_id, code, body.enabled)
    db.commit()


@router.put("/modules/{code}", status_code=status.HTTP_204_NO_CONTENT)
def toggle_module(code: str, body: ModuleToggle, ctx: ModuleManage) -> None:
    """Activation au niveau de l'entreprise retirée (palier C) : un module s'active par site,
    ``PUT /sites/{site_id}/modules/{code}``."""
    raise ConflictError("Les modules s'activent pour chaque site", code="module_is_per_site")
