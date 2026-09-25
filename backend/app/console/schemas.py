import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform.registry import AccessKind, ModuleStatus
from app.shared.schemas import Money


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class PlatformAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str


# --- Plans --------------------------------------------------------------------------------------


class PlanOut(BaseModel):
    """Plan vu par TechNova : identité et statut (catalogue) + paramètres commerciaux (base)."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None
    # Actif dans le catalogue (plan retiré de plans.toml : inactif, jamais supprimé).
    is_active: bool
    sort_order: int
    listed: bool
    price_display_enabled: bool
    monthly_price: Money | None
    monthly_price_enabled: bool
    annual_price: Money | None
    annual_price_enabled: bool
    currency: str | None
    contact_required: bool
    commercial_description: str | None
    display_order: int
    trial_days: int
    # Souscriptible depuis l'inscription publique (règle unique, celle de l'inscription).
    self_service: bool
    updated_at: datetime


class PlanModuleOut(BaseModel):
    code: str
    status: ModuleStatus | None
    core: bool


class PlanPermissionOut(BaseModel):
    code: str
    module: str
    access: AccessKind
    feature: str | None


class PlanStructureOut(BaseModel):
    """Structure technique (catalogue versionné, lecture seule)."""

    modules: list[PlanModuleOut]
    features: list[str]
    # Toutes les limites déclarées par les modules ; nul = illimitée.
    limits: dict[str, int | None]
    grace_days: int
    permissions: list[PlanPermissionOut]


class PlanDetailOut(PlanOut):
    structure: PlanStructureOut


class PlanCommercialUpdate(BaseModel):
    """Paramètres commerciaux modifiables par TechNova ; tout autre champ (structure) est refusé.
    Seuls les champs envoyés sont modifiés ; ``reason`` est obligatoire."""

    model_config = ConfigDict(extra="forbid")

    listed: bool | None = None
    price_display_enabled: bool | None = None
    monthly_price: Money | None = None
    monthly_price_enabled: bool | None = None
    annual_price: Money | None = None
    annual_price_enabled: bool | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    contact_required: bool | None = None
    commercial_description: str | None = Field(default=None, max_length=2000)
    display_order: int | None = Field(default=None, ge=0, le=9999)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("La raison de la modification est obligatoire")
        return stripped


# --- Catalogue technique (lecture seule) --------------------------------------------------------


class CatalogPermissionOut(BaseModel):
    code: str
    access: AccessKind
    feature: str | None


class CatalogModuleOut(BaseModel):
    code: str
    status: ModuleStatus
    core: bool
    depends_on: list[str]
    features: list[str]
    limits: list[str]
    permissions: list[CatalogPermissionOut]


class CatalogSectorOut(BaseModel):
    code: str
    name: str
    is_active: bool


class CatalogProfileOut(BaseModel):
    code: str
    name: str
    sector_code: str | None
    ux_profile_code: str | None
    is_active: bool
    modules: list[str]


class CatalogUxProfileOut(BaseModel):
    code: str
    name: str
    is_active: bool


class CatalogRoleTemplateOut(BaseModel):
    code: str
    name: str
    description: str | None
    protected: bool
    permission_patterns: list[str]
    exclude_patterns: list[str]


class CatalogPolicyOut(BaseModel):
    status: str
    allowed_access: list[str]
    description: str | None


class CatalogOut(BaseModel):
    modules: list[CatalogModuleOut]
    sectors: list[CatalogSectorOut]
    profiles: list[CatalogProfileOut]
    ux_profiles: list[CatalogUxProfileOut]
    role_templates: list[CatalogRoleTemplateOut]
    policies: list[CatalogPolicyOut]
    # Devises du référentiel des pays actifs (choix de la devise d'un plan).
    currencies: list[str]
    active_countries: int


# --- Tableau de bord et journal -----------------------------------------------------------------


class DashboardOut(BaseModel):
    admin: PlatformAdminOut
    plans_total: int
    plans_active: int
    plans_listed: int
    plans_self_service: int
    plans_contact_required: int
    modules_available: int
    modules_planned: int
    permissions: int
    profiles_active: int
    active_countries: int


class PlatformAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None
    actor_label: str
    action: str
    target_type: str | None
    target_id: str | None
    tenant_id: uuid.UUID | None
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str | None
    data: dict[str, Any]
    ip_address: str | None
