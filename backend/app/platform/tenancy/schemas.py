import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.platform.tenancy.models import SiteKind


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    business_profile_code: str
    currency: str
    locale: str
    timezone: str


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    kind: SiteKind
    address: str | None
    phone: str | None
    is_active: bool
    created_at: datetime


class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    kind: SiteKind = SiteKind.STORE
    address: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    code: str | None = Field(default=None, min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    kind: SiteKind | None = None
    address: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    is_active: bool | None = None


class ModuleOut(BaseModel):
    code: str
    status: str
    core: bool
    depends_on: list[str]
    in_profile: bool
    in_plan: bool
    enabled: bool
    effective: bool


class ModuleToggle(BaseModel):
    enabled: bool
