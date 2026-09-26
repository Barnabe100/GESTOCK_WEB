"""Payloads **autorisés** à la signature : structure stricte, aucune clé inconnue.

Le service ne décide rien (plan, prix, quota, période : décidés par la console TechNova à
partir d'un paiement confirmé) ; il refuse seulement ce qui n'est pas une licence bien formée.
"""

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,99}$")]
LimitCode = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=100)]


def _uuid(value: str) -> str:
    if str(uuid.UUID(value)) != value:
        raise ValueError("identifiant non canonique")
    return value


class LicensePayload(BaseModel):
    """Contenu signé d'une licence v1 (voir ``docs/architecture/LICENSING.md``)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    license_id: str
    license_number: Annotated[str, StringConstraints(pattern=r"^LIC-\d{4}-\d{5,}$")]
    license_version: int = Field(ge=1, le=10_000)
    supersedes_id: str | None
    tenant_id: str
    site_id: str
    subscription_id: str
    payment_id: str
    plan: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9_]{1,50}$")]
    billing_period: Annotated[str, StringConstraints(pattern=r"^(monthly|annual)$")]
    issued_at: str
    timezone: ShortText
    valid_from: str
    valid_until: str
    max_activations: int = Field(ge=1, le=10_000)
    modules: list[Code] = Field(max_length=200)
    features: list[Code] = Field(max_length=500)
    limits: dict[LimitCode, int | None] = Field(max_length=100)
    compatibility: dict[LimitCode, list[ShortText]] = Field(max_length=10)

    @field_validator("license_id", "tenant_id", "site_id", "subscription_id", "payment_id")
    @classmethod
    def _identifier(cls, value: str) -> str:
        return _uuid(value)

    @field_validator("supersedes_id")
    @classmethod
    def _optional_identifier(cls, value: str | None) -> str | None:
        return _uuid(value) if value is not None else None

    @field_validator("issued_at")
    @classmethod
    def _utc_instant(cls, value: str) -> str:
        if not value.endswith("Z"):
            raise ValueError("instant UTC attendu (suffixe Z)")
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        return value

    @field_validator("valid_from", "valid_until")
    @classmethod
    def _day(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("date AAAA-MM-JJ attendue")
        return value

    @field_validator("modules", "features")
    @classmethod
    def _sorted_unique(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("liste triée et sans doublon attendue")
        return value

    @field_validator("limits")
    @classmethod
    def _limits(cls, value: dict[str, int | None]) -> dict[str, int | None]:
        if any(v is not None and v < 0 for v in value.values()):
            raise ValueError("limite négative")
        return value

    @model_validator(mode="after")
    def _period(self) -> "LicensePayload":
        if self.valid_until < self.valid_from:
            raise ValueError("valid_until précède valid_from")
        return self


class SignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: LicensePayload
