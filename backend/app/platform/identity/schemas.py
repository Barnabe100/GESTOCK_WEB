import uuid

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    tenant_id: uuid.UUID | None = None


class RefreshRequest(BaseModel):
    tenant_id: uuid.UUID | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    locale: str
    must_change_password: bool


class MembershipSummary(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    is_owner: bool


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    tenant_id: uuid.UUID | None
    user: UserOut
    memberships: list[MembershipSummary]


class MeResponse(BaseModel):
    user: UserOut
    tenant_id: uuid.UUID | None
    memberships: list[MembershipSummary]
