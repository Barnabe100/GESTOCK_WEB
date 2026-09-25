import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.modules.sales.models import (
    PaymentMethod,
    PaymentStatus,
    SalePaymentStatus,
    SaleStatus,
)
from app.shared.schemas import Money, PositiveMoney, PositiveQuantity, Quantity
from app.shared.text import Optional50, Optional100, Optional500


class SaleLineInput(BaseModel):
    """Ni prix ni montant : le prix vient du catalogue, les montants sont calculés."""

    article_id: uuid.UUID
    quantity: PositiveQuantity


class SaleInput(BaseModel):
    sale_date: date | None = None  # défaut : aujourd'hui (fuseau du tenant)
    customer_id: uuid.UUID | None = None  # vente comptant anonyme si absent
    notes: Optional500 = None
    lines: list[SaleLineInput] = Field(min_length=1, max_length=500)


class SaleCreate(SaleInput):
    # Facultatif si un site est sélectionné (X-Site-Id).
    site_id: uuid.UUID | None = None


class SaleCancel(BaseModel):
    """Motif obligatoire, 5 à 500 caractères."""

    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def _min_length(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("le motif d'annulation doit contenir au moins 5 caractères")
        return value


class SaleLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    quantity: Quantity
    unit_price: Money
    line_total: Money


class SaleOut(BaseModel):
    id: uuid.UUID
    number: str
    site_id: uuid.UUID
    site_name: str
    customer_id: uuid.UUID | None
    customer_code: str | None
    customer_name: str | None
    status: SaleStatus
    sale_date: date
    notes: str | None
    subtotal: Money
    total: Money
    line_count: int
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None
    validated_at: datetime | None
    validated_by_name: str | None
    cancelled_at: datetime | None
    cancelled_by_name: str | None
    cancellation_reason: str | None
    # Encaissement (vente validée seulement ; calculé à partir des paiements effectués).
    paid_amount: Money | None = None
    remaining_amount: Money | None = None
    payment_status: SalePaymentStatus | None = None
    lines: list[SaleLineOut] = Field(default_factory=list)


# --- Paiements (Phase 2.7) ---------------------------------------------------------------------


class PaymentCreate(BaseModel):
    """Montant > 0 (2 décimales) ; le serveur recalcule le solde et refuse tout surpaiement.
    ``idempotency_key`` : identifiant généré par le client pour une saisie ; une seconde
    soumission avec la même clé renvoie le paiement déjà créé (aucun doublon)."""

    amount: PositiveMoney
    method: PaymentMethod
    provider: Optional50 = None
    reference: Optional100 = None
    idempotency_key: uuid.UUID | None = None
    # Espèces : caisse du site de la vente (facultatif ; choisie par le serveur si une seule
    # session est ouverte sur le site, ou celle ouverte par l'utilisateur).
    cash_register_id: uuid.UUID | None = None


class SaleValidate(BaseModel):
    """Corps facultatif de la validation : encaissements immédiats (paiement comptant), créés
    dans la même transaction que la validation. Le reste dû devient une créance soumise à la
    limite de crédit du client (ADR-0021)."""

    payments: list[PaymentCreate] = Field(default_factory=list, max_length=10)


class PaymentOut(BaseModel):
    id: uuid.UUID
    number: str
    sale_id: uuid.UUID
    sale_number: str
    site_id: uuid.UUID
    amount: Money
    method: PaymentMethod
    provider: str | None
    status: PaymentStatus
    reference: str | None
    paid_at: datetime
    created_at: datetime
    created_by_name: str | None
    cancelled_at: datetime | None
    cancelled_by_name: str | None
    cancellation_reason: str | None


class PaymentSummary(BaseModel):
    """Solde d'une vente validée : total, payé (paiements effectués), reste, état."""

    total: Money
    paid_amount: Money
    remaining_amount: Money
    payment_status: SalePaymentStatus


class SalePaymentsOut(BaseModel):
    sale_id: uuid.UUID
    sale_status: SaleStatus
    summary: PaymentSummary | None
    items: list[PaymentOut]
