import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.config import Settings
from app.platform.access.schemas import (
    MemberCreate,
    MemberOut,
    MemberUpdate,
    PermissionOut,
    RoleCreate,
    RoleDeactivate,
    RoleDuplicate,
    RoleFromTemplate,
    RoleMemberOut,
    RoleOut,
    RoleTemplateOut,
    RoleUpdate,
)
from app.platform.access.service import (
    MemberService,
    RoleKind,
    RoleService,
    member_out,
    role_out,
)
from app.platform.context import (
    DbSession,
    RegistryDep,
    RequestContext,
    SettingsDep,
    require_permission,
)
from app.platform.onboarding.service import OnboardingService
from app.platform.registry import ModuleRegistry
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["users"])

Paging = Annotated[PageParams, Depends(page_params)]

MemberView = Annotated[RequestContext, Depends(require_permission("users.member.view"))]
MemberManage = Annotated[RequestContext, Depends(require_permission("users.member.manage"))]
RoleView = Annotated[RequestContext, Depends(require_permission("users.role.view"))]
RoleManage = Annotated[RequestContext, Depends(require_permission("users.role.manage"))]


@router.get("/members", response_model=Page[MemberOut])
def list_members(
    ctx: MemberView,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
    paging: Paging,
    search: str | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = StatusFilter.ALL,
    role_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
) -> Page[MemberOut]:
    """Appartenances du tenant : recherche (nom, e-mail), statut, rôle, site ; tri
    ``full_name`` (défaut), ``email``, ``created_at``, ``status``."""
    items, total = MemberService(db, ctx, registry, settings).search(
        paging, search, status_filter, role_id, site_id
    )
    return Page(
        items=[member_out(m) for m in items], total=total, limit=paging.limit, offset=paging.offset
    )


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(
    body: MemberCreate,
    ctx: MemberManage,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
) -> MemberOut:
    membership = MemberService(db, ctx, registry, settings).create(body)
    OnboardingService(db, registry).refresh_for(ctx, "member.created")
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


def _set_active(
    membership_id: uuid.UUID,
    active: bool,
    ctx: RequestContext,
    db: DbSession,
    registry: ModuleRegistry,
    settings: Settings,
) -> MemberOut:
    membership = MemberService(db, ctx, registry, settings).set_active(membership_id, active)
    db.commit()
    return member_out(membership)


@router.post("/members/{membership_id}/activate", response_model=MemberOut)
def activate_member(
    membership_id: uuid.UUID,
    ctx: MemberManage,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
) -> MemberOut:
    """Réactive l'appartenance à CE tenant (limite d'utilisateurs du plan vérifiée)."""
    return _set_active(membership_id, True, ctx, db, registry, settings)


@router.post("/members/{membership_id}/deactivate", response_model=MemberOut)
def deactivate_member(
    membership_id: uuid.UUID,
    ctx: MemberManage,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
) -> MemberOut:
    """Désactive l'appartenance à CE tenant seulement : compte global et autres entreprises de
    l'utilisateur inchangés ; rôles, sites et historique conservés."""
    return _set_active(membership_id, False, ctx, db, registry, settings)


@router.get("/roles", response_model=list[RoleOut])
def list_roles(
    ctx: RoleView,
    db: DbSession,
    registry: RegistryDep,
    kind: RoleKind | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = StatusFilter.ALL,
) -> list[RoleOut]:
    service = RoleService(db, ctx, registry)
    counts = service.member_counts()
    return [
        role_out(r, registry, counts.get(r.id, 0)) for r in service.list_all(kind, status_filter)
    ]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role(body: RoleCreate, ctx: RoleManage, db: DbSession, registry: RegistryDep) -> RoleOut:
    service = RoleService(db, ctx, registry)
    role = service.create(body)
    db.commit()
    return service.out(role)


@router.get("/roles/{role_id}", response_model=RoleOut)
def get_role(role_id: uuid.UUID, ctx: RoleView, db: DbSession, registry: RegistryDep) -> RoleOut:
    service = RoleService(db, ctx, registry)
    return service.out(service.get(role_id))


@router.patch("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: uuid.UUID, body: RoleUpdate, ctx: RoleManage, db: DbSession, registry: RegistryDep
) -> RoleOut:
    service = RoleService(db, ctx, registry)
    role = service.update(role_id, body)
    db.commit()
    return service.out(role)


@router.post(
    "/roles/{role_id}/duplicate", response_model=RoleOut, status_code=status.HTTP_201_CREATED
)
def duplicate_role(
    role_id: uuid.UUID, body: RoleDuplicate, ctx: RoleManage, db: DbSession, registry: RegistryDep
) -> RoleOut:
    service = RoleService(db, ctx, registry)
    role = service.duplicate(role_id, body)
    db.commit()
    return service.out(role)


@router.post("/roles/{role_id}/activate", response_model=RoleOut)
def activate_role(
    role_id: uuid.UUID, ctx: RoleManage, db: DbSession, registry: RegistryDep
) -> RoleOut:
    service = RoleService(db, ctx, registry)
    role = service.activate(role_id)
    db.commit()
    return service.out(role)


@router.post("/roles/{role_id}/deactivate", response_model=RoleOut)
def deactivate_role(
    role_id: uuid.UUID,
    ctx: RoleManage,
    db: DbSession,
    registry: RegistryDep,
    body: RoleDeactivate | None = None,
) -> RoleOut:
    service = RoleService(db, ctx, registry)
    role = service.deactivate(role_id, body or RoleDeactivate())
    db.commit()
    return service.out(role)


@router.get("/roles/{role_id}/members", response_model=list[RoleMemberOut])
def list_role_members(
    role_id: uuid.UUID,
    ctx: RoleView,
    _members: MemberView,
    db: DbSession,
    registry: RegistryDep,
) -> list[RoleMemberOut]:
    return RoleService(db, ctx, registry).members(role_id)


@router.get("/role-templates", response_model=list[RoleTemplateOut])
def list_role_templates(
    ctx: RoleView, db: DbSession, registry: RegistryDep
) -> list[RoleTemplateOut]:
    return RoleService(db, ctx, registry).templates()


@router.post("/roles/from-template", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
def create_role_from_template(
    body: RoleFromTemplate, ctx: RoleManage, db: DbSession, registry: RegistryDep
) -> RoleOut:
    service = RoleService(db, ctx, registry)
    role = service.create_from_template(body.template_code)
    db.commit()
    return service.out(role)


# Aucun DELETE /roles/{id} : un rôle n'est jamais supprimé, il est désactivé (ADR-0015).


@router.get("/permissions", response_model=list[PermissionOut])
def list_permissions(ctx: RoleView, db: DbSession, registry: RegistryDep) -> list[PermissionOut]:
    return RoleService(db, ctx, registry).available_permissions()
