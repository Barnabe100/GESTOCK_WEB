import uuid
from datetime import datetime

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, field_validator

from app.modules.customers.models import CustomerType
from app.shared.schemas import Money
from app.shared.text import (
    Optional50,
    Optional100,
    Optional150,
    Optional200,
    Optional255,
    Optional1000,
    OptionalPhone,
    Required150,
)


def _check_email(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError("adresse email invalide") from exc


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    customer_type: CustomerType
    name: str
    legal_name: str | None
    tax_id: str | None
    phone: str | None
    phone2: str | None
    email: str | None
    address: str | None
    city: str | None
    country: str | None
    notes: str | None
    credit_limit: Money | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class _CustomerFields(BaseModel):
    legal_name: Optional200 = None
    tax_id: Optional50 = None
    phone: OptionalPhone = None
    phone2: OptionalPhone = None
    email: Optional150 = None
    address: Optional255 = None
    city: Optional100 = None
    country: Optional100 = None
    notes: Optional1000 = None
    credit_limit: Money | None = None

    _email = field_validator("email")(_check_email)


class CustomerCreate(_CustomerFields):
    """Le code (CLI-000001) est attribué par le serveur ; le client est créé actif."""

    customer_type: CustomerType
    name: Required150


class CustomerUpdate(_CustomerFields):
    """Champs absents : inchangés ; champ optionnel vide : effacé. Le code est immuable."""

    customer_type: CustomerType | None = None
    name: Required150 | None = None
