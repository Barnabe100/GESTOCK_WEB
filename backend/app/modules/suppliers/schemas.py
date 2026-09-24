import uuid
from datetime import datetime

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, field_validator

from app.shared.text import (
    Optional30,
    Optional100,
    Optional150,
    Optional255,
    Optional500,
    Required150,
)


def _check_email(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError("adresse email invalide") from exc


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    contact_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    city: str | None
    country: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupplierCreate(BaseModel):
    name: Required150
    contact_name: Optional150 = None
    phone: Optional30 = None
    email: Optional150 = None
    address: Optional255 = None
    city: Optional100 = None
    country: Optional100 = None
    notes: Optional500 = None

    _email = field_validator("email")(_check_email)


class SupplierUpdate(BaseModel):
    """Champs absents : inchangés ; champ optionnel vide : effacé (SUP-03)."""

    name: Required150 | None = None
    contact_name: Optional150 = None
    phone: Optional30 = None
    email: Optional150 = None
    address: Optional255 = None
    city: Optional100 = None
    country: Optional100 = None
    notes: Optional500 = None

    _email = field_validator("email")(_check_email)
