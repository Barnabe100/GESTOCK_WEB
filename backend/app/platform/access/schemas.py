import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    template_code: str | None
    is_system: bool
    permission_codes: list[str]


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = None


class PermissionOut(BaseModel):
    code: str
    module: str
    access: str
