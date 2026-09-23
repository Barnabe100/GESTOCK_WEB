import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.platform.access.schemas import (
    MemberCreate,
    MemberOut,
    MemberUpdate,
    PermissionOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)
from app.platform.access.service import MemberService, RoleService, member_out
from app.platform.context import (
    DbSession,
    RegistryDep,
    RequestContext,
    SettingsDep,
    require_permission,
)

router = APIRouter(tags=["users"])

MemberView = Annotated[RequestContext, Depends(require_permission("users.member.view"))]
MemberManage = Annotated[RequestContext, Depends(require_permission("users.member.manage"))]
RoleView = Annotated[RequestContext, Depends(require_permission("users.role.view"))]
RoleManage = Annotated[RequestContext, Depends(require_permission("users.role.manage"))]


@router.get("/members", response_model=list[MemberOut])
def list_members(
    ctx: MemberView, db: DbSession, registry: RegistryDep, settings: SettingsDep
) -> list[MemberOut]:
    return [member_out(m) for m in MemberService(db, ctx, registry, settings).list_all()]


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(
    body: MemberCreate,
    ctx: MemberManage,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
) -> MemberOut:
    membership = MemberService(db, ctx, registry, settings).create(body)
    db.commit()
    return member_out(membership)


@router.get("/members/{membership_id}", response_model=MemberOut)
def get_member(
    membership_id: uuid.UUID,
    ctx: MemberView,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
) -> MemberOut:
    return member_out(MemberService(db, ctx, registry, settings).get(membership_id))


@router.patch("/members/{membership_id}", response_model=MemberOut)
def update_member(
    membership_id: uuid.UUID,
    body: MemberUpdate,
    ctx: MemberManage,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
) -> MemberOut:
    membership = MemberService(db, ctx, registry, settings).update(membership_id, body)
    db.commit()
    return member_out(membership)


@router.get("/roles", response_model=list[RoleOut])
def list_roles(ctx: RoleView, db: DbSession, registry: RegistryDep) -> list[RoleOut]:
    return [RoleOut.model_validate(r) for r in RoleService(db, ctx, registry).list_all()]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(body: RoleCreate, ctx: RoleManage, db: DbSession, registry: RegistryDep) -> RoleOut:
    role = RoleService(db, ctx, registry).create(body)
    db.commit()
    return RoleOut.model_validate(role)


@router.get("/roles/{role_id}", response_model=RoleOut)
def get_role(role_id: uuid.UUID, ctx: RoleView, db: DbSession, registry: RegistryDep) -> RoleOut:
    return RoleOut.model_validate(RoleService(db, ctx, registry).get(role_id))


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: uuid.UUID, body: RoleUpdate, ctx: RoleManage, db: DbSession, registry: RegistryDep
) -> RoleOut:
    role = RoleService(db, ctx, registry).update(role_id, body)
    db.commit()
    return RoleOut.model_validate(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(role_id: uuid.UUID, ctx: RoleManage, db: DbSession, registry: RegistryDep) -> None:
    RoleService(db, ctx, registry).delete(role_id)
    db.commit()


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(ctx: RoleView, db: DbSession, registry: RegistryDep) -> list[PermissionOut]:
    return RoleService(db, ctx, registry).available_permissions()
