import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform.subscriptions.models import (
    SubscriptionPaymentMethod,
    SubscriptionPaymentStatus,
)
from app.shared.schemas import Money, PositiveMoney


class SubscriptionPaymentCreate(BaseModel):
    """Déclaration d'un paiement d'abonnement (d'UN site). Aucun champ de décision (statut,
    décideur, date, motif), ni devise, ni **période** : fixés par le serveur ou par TechNova
    (3.3-B4, R3). ``amount`` : seulement pour une offre sans tarif (sur devis) ;
    ``requested_activations`` : changement explicite du nombre de postes (R1)."""

    model_config = ConfigDict(extra="forbid")

    subscription_id: uuid.UUID
    amount: PositiveMoney | None = None
    requested_activations: int | None = Field(default=None, ge=1, le=10_000)
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
    requested_activations: int | None
    declared_by: uuid.UUID
    status: SubscriptionPaymentStatus
    created_at: datetime
    decided_at: datetime | None
    rejection_reason: str | None
