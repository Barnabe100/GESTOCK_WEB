import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.platform.context import DbSession, RegistryDep, RequestContext, require_permission
from app.platform.tenancy.schemas import (
    ModuleOut,
    ModuleToggle,
    SiteCreate,
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


@router.patch("/tenant", response_model=TenantOut)
def update_tenant(body: TenantUpdate, ctx: TenantUpdateCtx, db: DbSession) -> TenantOut:
    TenantService(db, ctx).update(body)
    db.commit()
    return TenantOut.model_validate(ctx.tenant)


@router.get("/sites", response_model=list[SiteOut])
def list_sites(ctx: SiteView, db: DbSession) -> list[SiteOut]:
    return [SiteOut.model_validate(s) for s in SiteService(db, ctx).list_all()]


@router.post("/sites", response_model=SiteOut, status_code=status.HTTP_201_CREATED)
def create_site(body: SiteCreate, ctx: SiteManage, db: DbSession) -> SiteOut:
    site = SiteService(db, ctx).create(body)
    db.commit()
    return SiteOut.model_validate(site)


@router.get("/sites/{site_id}", response_model=SiteOut)
def get_site(site_id: uuid.UUID, ctx: SiteView, db: DbSession) -> SiteOut:
    return SiteOut.model_validate(SiteService(db, ctx).get(site_id))


@router.patch("/sites/{site_id}", response_model=SiteOut)
def update_site(site_id: uuid.UUID, body: SiteUpdate, ctx: SiteManage, db: DbSession) -> SiteOut:
    site = SiteService(db, ctx).update(site_id, body)
    db.commit()
    return SiteOut.model_validate(site)


@router.get("/modules", response_model=list[ModuleOut])
def list_modules(ctx: ModuleView, db: DbSession, registry: RegistryDep) -> list[ModuleOut]:
    return ModuleService(db, ctx, registry).list_all()


@router.put("/modules/{code}", status_code=status.HTTP_204_NO_CONTENT)
def toggle_module(
    code: str, body: ModuleToggle, ctx: ModuleManage, db: DbSession, registry: RegistryDep
) -> None:
    ModuleService(db, ctx, registry).set_enabled(code, body.enabled)
    db.commit()
