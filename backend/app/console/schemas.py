import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform.licensing.models import MAX_ACTIVATIONS
from app.platform.licensing.schemas import LicenseSummary
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
    tenants: "PlatformDashboardTenants"


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


# --- Tenants et abonnements (Phase 3.2-G) ------------------------------------------------------


class TenantListItem(BaseModel):
    """Métadonnées plateforme d'une entreprise (aucune donnée métier). Abonnements : un par
    site (ADR-0033), résumés ici (plans, statuts effectifs, prochaine échéance)."""

    id: uuid.UUID
    name: str
    trade_name: str | None
    slug: str
    status: str
    business_profile_code: str
    business_profile_name: str
    created_at: datetime
    subscription_count: int
    plan_codes: list[str]
    # Statuts effectifs (échéance, délai de grâce) des abonnements, distincts du statut du
    # tenant.
    effective_statuses: list[str]
    next_period_end: datetime | None
    sites: int
    users: int

    @field_validator("plan_codes", "effective_statuses", mode="before")
    @classmethod
    def _sorted(cls, value: list[str] | None) -> list[str]:
        return sorted(v for v in (value or []) if v is not None)


class TenantUsage(BaseModel):
    used: int
    limit: int | None


class SiteRefOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str


class PlanChoice(BaseModel):
    code: str
    name: str


class SubscriptionActions(BaseModel):
    """Actions possibles sur cet abonnement dans l'état actuel (décidées par le serveur, qui
    revérifie tout)."""

    # Activation manuelle et prolongation transitoires (3.2-G) : jamais pour un abonnement dont
    # une licence décide de la période (ADR-0034).
    can_activate: bool
    can_extend: bool
    can_change_plan: bool
    activation_start: date
    activation_end: date
    extension_end: date
    available_plans: list[PlanChoice]


class TenantSubscriptionOut(BaseModel):
    id: uuid.UUID
    # Site de l'abonnement ; nul : pris à l'inscription, en attente du premier site.
    site: SiteRefOut | None
    plan_code: str
    plan_name: str
    billing_period: str
    status: str
    effective_status: str
    started_at: datetime
    current_period_start: datetime
    current_period_end: datetime
    cancelled_at: datetime | None
    grace_days: int
    # Prix et devise figés à la souscription (ou au dernier changement de plan).
    price_at_subscription: Money | None
    currency_at_subscription: str | None
    requested_activations: int
    # Limites du plan de CET abonnement et usage du site (utilisateurs ayant accès au site).
    usage: dict[str, TenantUsage]
    # Licence en vigueur du site, sinon la plus récente (nulle : aucune licence).
    license: LicenseSummary | None
    actions: SubscriptionActions


class TenantActions(BaseModel):
    can_suspend: bool
    can_reactivate: bool


class TenantDetailOut(TenantListItem):
    country_code: str | None
    country_name: str | None
    currency: str
    locale: str
    timezone: str
    subscriptions: list[TenantSubscriptionOut]
    actions: TenantActions


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("La raison est obligatoire")
        return stripped


class ActivationIn(ReasonIn):
    # Dates dans le fuseau du tenant ; absentes : proposition du serveur (aujourd'hui, une
    # période de facturation).
    period_start: date | None = None
    period_end: date | None = None


class ExtensionIn(ReasonIn):
    period_end: date


class PlanChangeIn(ReasonIn):
    plan_code: str = Field(min_length=1, max_length=50)


class PlatformDashboardTenants(BaseModel):
    """Agrégats calculés en base (aucune ligne de tenant chargée)."""

    tenants_total: int
    tenants_active: int
    tenants_suspended: int
    subscriptions_active: int
    subscriptions_trial: int
    subscriptions_pending_activation: int
    subscriptions_past_due: int
    subscriptions_expired: int
    subscriptions_renewal_due: int


class ConsolePaymentOut(BaseModel):
    """Paiement d'abonnement vu par TechNova : ni identité du déclarant (utilisateur de
    l'entreprise), ni donnée métier."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    subscription_id: uuid.UUID
    plan_code: str
    site_id: uuid.UUID | None
    site_name: str | None
    amount: Money
    currency: str
    period_start: date
    period_end: date
    payment_method: str
    declared_reference: str
    status: str
    created_at: datetime
    decided_at: datetime | None
    decided_by_email: str | None
    rejection_reason: str | None


# --- Licences (Phase 3.3-B2, ADR-0034) ----------------------------------------------------------


class ConsoleLicenseOut(BaseModel):
    """Licence d'un site vue par TechNova (contenu signé, état calculé, révocation)."""

    id: uuid.UUID
    license_number: str
    license_version: int
    supersedes_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    tenant_id: uuid.UUID
    tenant_name: str
    site_id: uuid.UUID
    site_name: str
    site_code: str
    subscription_id: uuid.UUID
    payment_id: uuid.UUID
    plan_code: str
    billing_period: str
    valid_from: date
    valid_until: date
    timezone: str
    max_activations: int
    # Postes actifs sur l'abonnement du site (3.3-B3).
    activations_used: int
    modules: list[str]
    features: list[str]
    limits: dict[str, int | None]
    status: str
    state: str
    issued_at: datetime
    issued_by_email: str | None
    key_id: str
    payload_sha256: str
    revoked_at: datetime | None
    revoked_by_email: str | None
    revocation_reason: str | None


class LicenseProposalOut(BaseModel):
    """Licence que produirait la génération depuis ce paiement (période calculée par le
    serveur ; postes proposés = postes demandés à la souscription). ``blocking`` : raison pour
    laquelle la génération est impossible (``null`` : possible)."""

    payment_id: uuid.UUID
    payment_status: str
    tenant_id: uuid.UUID
    tenant_name: str
    subscription_id: uuid.UUID
    site_id: uuid.UUID | None
    site_name: str | None
    plan_code: str
    billing_period: str
    timezone: str
    valid_from: date
    valid_until: date
    requested_activations: int
    max_activations: int
    payment_period_start: date
    payment_period_end: date
    blocking: str | None
    license_id: uuid.UUID | None


class ConsoleActivationOut(BaseModel):
    """Poste d'un site vu par TechNova : métadonnées de l'installation, jamais l'utilisateur de
    l'entreprise qui l'a activé."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str
    site_id: uuid.UUID
    site_name: str
    subscription_id: uuid.UUID
    license_id: uuid.UUID
    license_number: str
    installation_id: uuid.UUID
    label: str
    client_version: str | None
    status: str
    activated_at: datetime
    last_seen_at: datetime
    released_at: datetime | None
    release_source: str | None
    release_reason: str | None


class LicenseGenerateIn(ReasonIn):
    """Nombre de postes confirmé ou ajusté par TechNova (arbitrage Q3), figé dans la licence."""

    max_activations: int = Field(ge=1, le=MAX_ACTIVATIONS)


class LicenseReissueIn(ReasonIn):
    """Postes conservés sauf changement commercial explicite."""

    max_activations: int | None = Field(default=None, ge=1, le=MAX_ACTIVATIONS)


DashboardOut.model_rebuild()
