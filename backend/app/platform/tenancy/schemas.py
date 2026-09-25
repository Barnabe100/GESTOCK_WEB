import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform.tenancy.models import SiteKind
from app.shared.text import (
    Optional50,
    Optional100,
    Optional150,
    Optional255,
    Optional1000,
    OptionalEmail,
    OptionalHttpsUrl,
    OptionalPhone,
    Required150,
)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    business_profile_code: str
    currency: str
    locale: str
    timezone: str
    # Entreprise (Phase 3.2). ``country_code`` nul : tenant antérieur, pays à renseigner.
    country_code: str | None
    trade_name: str | None
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    region: str | None
    website: str | None
    tax_id: str | None
    trade_register: str | None
    description: str | None
    logo_url: str | None


class TenantUpdate(BaseModel):
    """Champ omis : inchangé. Champ facultatif à ``null`` (ou vide) : effacé. Le nom, le fuseau
    horaire et le pays ne s'effacent jamais ; la devise est fixée à la création."""

    # Nom : espaces retirés ; vide ou blanc refusé (obligatoire), ``null`` ignoré.
    name: Required150 | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    country_code: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    trade_name: Optional150 = None
    email: OptionalEmail = None
    phone: OptionalPhone = None
    address: Optional255 = None
    city: Optional100 = None
    region: Optional100 = None
    website: OptionalHttpsUrl = None
    tax_id: Optional50 = None
    trade_register: Optional50 = None
    description: Optional1000 = None
    logo_url: OptionalHttpsUrl = None

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("fuseau horaire inconnu (IANA attendu)") from exc
        return value


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
    # Nom : espaces retirés ; vide ou blanc refusé (obligatoire), ``null`` ignoré.
    name: Required150 | None = None
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


class IdentityLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str  # phone, email, address, locality, tax_id, trade_register
    value: str


class DocumentIdentityOut(BaseModel):
    """En-tête documentaire (aperçu aujourd'hui, reçus et documents demain) : construit
    depuis le tenant, lignes absentes omises (jamais « N/A »)."""

    model_config = ConfigDict(from_attributes=True)

    name: str
    trade_name: str | None
    logo_url: str | None
    contact: list[IdentityLineOut]
    identifiers: list[IdentityLineOut]
    missing_recommended: list[str]
