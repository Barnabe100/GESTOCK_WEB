from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.platform.subscriptions.models import BillingPeriod
from app.shared.schemas import Money
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

# Toute clé inconnue est refusée : aucun champ d'état, de paiement ou d'activation ne peut
# être fourni par le client (``extra="forbid"``).
_STRICT = ConfigDict(extra="forbid")


class SignupAccount(BaseModel):
    model_config = _STRICT

    full_name: Required150
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SignupCompany(BaseModel):
    """Obligatoires : raison sociale et pays ; devise : celle du pays par défaut. Les autres
    informations sont recommandées ou facultatives et ne bloquent jamais l'inscription."""

    model_config = _STRICT

    name: Required150
    country_code: str = Field(pattern=r"^[A-Za-z]{2}$")
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
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


class SignupRequest(BaseModel):
    model_config = _STRICT

    account: SignupAccount
    company: SignupCompany
    business_profile: str = Field(min_length=1, max_length=50)
    plan_code: str = Field(min_length=1, max_length=50)
    billing_period: BillingPeriod


class PublicPlanPeriod(BaseModel):
    billing_period: BillingPeriod
    # Nul si TechNova n'affiche pas les prix de ce plan.
    price: Money | None


class PublicPlan(BaseModel):
    code: str
    name: str
    description: str | None
    # Souscription sur contact commercial uniquement (« Contacter TechNova »).
    contact_required: bool
    # Souscription possible depuis l'inscription (publié, sans contact, période ouverte).
    self_service: bool
    trial_days: int
    price_displayed: bool
    currency: str | None
    periods: list[PublicPlanPeriod]
    # Limites de l'offre (nul : illimité) et modules disponibles inclus.
    limits: dict[str, int | None]
    modules: list[str]


class PublicPlansOut(BaseModel):
    contact_email: str | None
    plans: list[PublicPlan]


class PublicSector(BaseModel):
    code: str
    name: str
    icon: str | None


class PublicProfile(BaseModel):
    code: str
    name: str
    description: str | None
    sector: str | None


class PublicProfilesOut(BaseModel):
    sectors: list[PublicSector]
    profiles: list[PublicProfile]
