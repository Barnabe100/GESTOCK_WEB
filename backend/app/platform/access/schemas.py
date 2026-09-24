import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, StringConstraints

from app.platform.access.models import MembershipStatus


class RoleAssignment(BaseModel):
    role_id: uuid.UUID
    # Nul : rôle valable sur tout le tenant ; sinon limité à ce site.
    site_id: uuid.UUID | None = None


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    status: MembershipStatus
    is_owner: bool
    all_sites: bool
    must_change_password: bool
    roles: list[RoleAssignment]
    site_ids: list[uuid.UUID]
    created_at: datetime


class MemberCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=150)
    # Mot de passe provisoire : requis seulement si l'email est inconnu de la plateforme.
    # Jamais stocké en clair ni renvoyé.
    password: str | None = Field(default=None, max_length=256)
    roles: list[RoleAssignment] = Field(default_factory=list)
    site_ids: list[uuid.UUID] = Field(default_factory=list)
    all_sites: bool = False


class MemberUpdate(BaseModel):
    roles: list[RoleAssignment] | None = None
    site_ids: list[uuid.UUID] | None = None
    all_sites: bool | None = None
    status: MembershipStatus | None = None


class RoleOut(BaseModel):
    id: uuid.UUID
    # Rôle de base : nom et description de son modèle (données TechNova, versionnées).
    name: str
    description: str | None
    template_code: str | None
    is_system: bool
    is_active: bool
    # Rôle protégé (Administrateur) : ni désactivable ni modifiable.
    protected: bool
    # Membres titulaires du rôle (toutes portées : tenant ou site).
    member_count: int
    permission_codes: list[str]


class RoleTemplateOut(BaseModel):
    code: str
    name: str
    description: str | None
    instantiated: bool


class RoleFromTemplate(BaseModel):
    template_code: str


RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
RoleDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class RoleCreate(BaseModel):
    name: RoleName
    description: RoleDescription | None = None
    permissions: list[str] = Field(default_factory=list, max_length=1000)


class RoleUpdate(BaseModel):
    name: RoleName | None = None
    description: RoleDescription | None = None
    permissions: list[str] | None = Field(default=None, max_length=1000)


class RoleDuplicate(BaseModel):
    """Nouveau rôle personnalisé reprenant les permissions d'un rôle (de base ou personnalisé)."""

    name: RoleName
    description: RoleDescription | None = None


class RoleDeactivate(BaseModel):
    # Obligatoire si le rôle est encore attribué (sinon 409 role_in_use).
    confirm: bool = False


class RoleMemberOut(BaseModel):
    membership_id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str
    status: MembershipStatus
    # Nul : rôle valable sur tout le tenant ; sinon limité à ce site.
    site_id: uuid.UUID | None


class PermissionOut(BaseModel):
    code: str
    module: str
    access: str
    # Ressource et action, tirées du code ``module.ressource.action`` (regroupement à l'écran).
    resource: str
    action: str
