import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.sales.models import (
    CreditStatus,
    PaymentIntegration,
    PaymentMethod,
    PaymentStatus,
    SaleChannel,
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
    # Nul pour un brouillon : ``VENT-{SITE}-{ANNÉE}-{SÉQUENCE}`` attribué à la validation.
    number: str | None
    site_id: uuid.UUID
    site_name: str
    customer_id: uuid.UUID | None
    customer_code: str | None
    customer_name: str | None
    status: SaleStatus
    channel: SaleChannel
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
    # Crédit (Lot 1) : reste dû à la validation ; situation calculée à partir des paiements.
    is_credit: bool = False
    credit_status: CreditStatus | None = None
    credit_override_at: datetime | None = None
    credit_override_by_name: str | None = None
    credit_override_reason: str | None = None
    credit_override_amount: Money | None = None
    lines: list[SaleLineOut] = Field(default_factory=list)


class SellerOut(BaseModel):
    """Vendeur / opérateur proposé au filtre de l'historique (Lot 2)."""

    id: uuid.UUID
    name: str


class SaleEventOut(BaseModel):
    """Évènement de la chronologie d'une vente (Lot 2) : entrée réelle du journal d'audit."""

    id: uuid.UUID
    occurred_at: datetime
    action: str
    user_name: str | None
    data: dict[str, Any]


# --- Paiements (Phase 2.7) ---------------------------------------------------------------------


class PaymentCreate(BaseModel):
    """Paiement d'une vente validée ; le serveur recalcule le solde et refuse tout surpaiement.

    - ``payment_method_id`` : moyen configuré (Lot 1). Compatibilité : ``method`` seul (type)
      désigne l'unique moyen disponible de ce type sur le site.
    - ``amount`` : montant imputé sur la vente. Espèces : ``amount_received`` (montant remis par
      le client) peut le remplacer — montant imputé = min(reçu, reste dû) — ou le compléter
      (reçu ≥ montant) ; la monnaie (reçu − imputé) est calculée par le serveur. Aucun autre
      moyen ne rend de monnaie.
    - ``idempotency_key`` : identifiant généré par le client pour une saisie ; une seconde
      soumission avec la même clé renvoie le paiement déjà créé (aucun doublon)."""

    amount: PositiveMoney | None = None
    amount_received: PositiveMoney | None = None
    payment_method_id: uuid.UUID | None = None
    method: PaymentMethod | None = None
    provider: Optional50 = None
    reference: Optional100 = None
    idempotency_key: uuid.UUID | None = None
    # Espèces sur un site avec caisse : poste de la session de l'utilisateur (facultatif s'il
    # n'a qu'une session ouverte sur le site).
    cash_register_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _complete(self) -> "PaymentCreate":
        if self.payment_method_id is None and self.method is None:
            raise ValueError("moyen de paiement obligatoire (payment_method_id)")
        if self.amount is None and self.amount_received is None:
            raise ValueError("montant obligatoire (amount ou amount_received)")
        return self


class CreditOverride(BaseModel):
    """Dépassement exceptionnel de la limite de crédit : justification obligatoire (5 à 500
    caractères) ; l'autorisateur est l'utilisateur authentifié, qui doit détenir
    ``sales.sale.credit_override`` sur le site de la vente."""

    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def _min_length(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("la justification doit contenir au moins 5 caractères")
        return value


class SaleValidate(BaseModel):
    """Corps facultatif de la validation : encaissements immédiats (paiement comptant), créés
    dans la même transaction que la validation. Le reste dû devient une créance soumise à la
    limite de crédit du client (ADR-0021)."""

    payments: list[PaymentCreate] = Field(default_factory=list, max_length=10)
    credit_override: CreditOverride | None = None


class PaymentOut(BaseModel):
    id: uuid.UUID
    number: str
    sale_id: uuid.UUID
    sale_number: str | None
    site_id: uuid.UUID
    amount: Money
    method: PaymentMethod
    payment_method_id: uuid.UUID | None
    method_label: str
    provider: str | None
    amount_received: Money | None
    change_given: Money | None
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


class SaleCheckout(SaleCreate):
    """Encaissement en une étape (POS) : création, validation et paiements immédiats dans UNE
    transaction (``SaleService.checkout``). ``idempotency_key`` : générée par le client pour
    un panier ; une seconde soumission renvoie la vente déjà enregistrée."""

    payments: list[PaymentCreate] = Field(default_factory=list, max_length=10)
    credit_override: CreditOverride | None = None
    idempotency_key: uuid.UUID


class CheckoutOut(BaseModel):
    sale: SaleOut
    payments: list[PaymentOut]
    # Vrai si la clé avait déjà été traitée (réponse rejouée, aucune nouvelle écriture).
    replayed: bool


# --- Moyens de paiement configurables (Lot 1) ---------------------------------------------------


class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    kind: PaymentMethod
    reference_required: bool = False
    integration_mode: PaymentIntegration = PaymentIntegration.MANUAL
    sort_order: int = Field(default=0, ge=0, le=9999)


class PaymentMethodUpdate(BaseModel):
    """Le type n'est jamais modifiable (comportement des paiements déjà enregistrés)."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=60)
    reference_required: bool | None = None
    integration_mode: PaymentIntegration | None = None
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=9999)


class PaymentMethodSiteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class PaymentMethodOut(BaseModel):
    id: uuid.UUID
    label: str
    kind: PaymentMethod
    integration_mode: PaymentIntegration
    reference_required: bool
    is_active: bool
    sort_order: int
    # Sites où le moyen est désactivé (disponible partout ailleurs s'il est actif).
    disabled_site_ids: list[uuid.UUID]
    # Avec ``site_id`` en filtre : utilisable sur ce site.
    available: bool | None = None
