import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform.subscriptions.models import (
    SubscriptionPaymentMethod,
    SubscriptionPaymentStatus,
)
from app.shared.schemas import Money, PositiveMoney


class SubscriptionPaymentCreate(BaseModel):
    """Déclaration d'un paiement d'abonnement. Aucun champ de décision (statut, décideur,
    date, motif) ni de devise : ils sont fixés par le serveur ou par TechNova."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: uuid.UUID
    amount: PositiveMoney
    period_start: date
    period_end: date
    payment_method: SubscriptionPaymentMethod
    declared_reference: str = Field(min_length=1, max_length=100)
    idempotency_key: uuid.UUID

    @field_validator("declared_reference")
    @classmethod
    def _reference_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("La référence est obligatoire")
        return stripped


class SubscriptionPaymentOut(BaseModel):
    """Vue de l'entreprise : jamais l'identité de l'agent TechNova qui a décidé."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subscription_id: uuid.UUID
    amount: Money
    currency: str
    period_start: date
    period_end: date
    payment_method: SubscriptionPaymentMethod
    declared_reference: str
    declared_by: uuid.UUID
    status: SubscriptionPaymentStatus
    created_at: datetime
    decided_at: datetime | None
    rejection_reason: str | None
