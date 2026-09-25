import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer, field_validator

from app.modules.cash_register.models import (
    CashMovementCategory,
    CashMovementType,
    CashSessionStatus,
)
from app.shared.schemas import Money, PositiveMoney, SignedMoney
from app.shared.text import Optional100, Optional500, Required100

# Écart de caisse : négatif (manquant) ou positif (excédent).
Variance = Annotated[
    Decimal,
    Field(max_digits=18, decimal_places=2),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]

# --- Caisses ---------------------------------------------------------------------------------


class CashRegisterCreate(BaseModel):
    """Code attribué par le serveur (CAI-001). Site facultatif si un site est sélectionné."""

    site_id: uuid.UUID | None = None
    name: Required100
    description: Optional500 = None


class CashRegisterUpdate(BaseModel):
    """Le site d'une caisse ne change jamais (son historique lui est rattaché)."""

    name: Required100 | None = None
    description: Optional500 = None


class CurrentSessionOut(BaseModel):
    id: uuid.UUID
    number: str
    opened_at: datetime
    opened_by_name: str | None
    opening_float: Money
    theoretical_balance: Money


class CashRegisterOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    site_id: uuid.UUID
    site_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    current_session: CurrentSessionOut | None


# --- Sessions --------------------------------------------------------------------------------


class CashSessionOpen(BaseModel):
    cash_register_id: uuid.UUID
    opening_float: Money


class CashSessionClose(BaseModel):
    """Montant réellement compté ; l'écart est calculé par le serveur (jamais envoyé)."""

    counted_balance: Money
    note: Optional500 = None


class CashSessionOut(BaseModel):
    id: uuid.UUID
    number: str
    cash_register_id: uuid.UUID
    cash_register_code: str
    cash_register_name: str
    site_id: uuid.UUID
    site_name: str
    status: CashSessionStatus
    opening_float: Money
    opened_at: datetime
    opened_by_name: str | None
    closed_at: datetime | None
    closed_by_name: str | None
    # Entrées hors fond initial (ventes, entrées manuelles) et sorties.
    cash_in_total: Money
    cash_out_total: Money
    # Session ouverte : calculé à partir des mouvements ; fermée : figé à la clôture.
    theoretical_balance: Money
    counted_balance: Money | None
    variance: Variance | None
    closing_note: str | None
    movement_count: int


# --- Mouvements ------------------------------------------------------------------------------


class CashMovementCreate(BaseModel):
    """Entrée ou sortie manuelle : montant > 0 (le type fixe le sens), nature et motif
    obligatoires. ``idempotency_key`` : une seconde soumission ne crée pas de doublon."""

    movement_type: CashMovementType
    amount: PositiveMoney
    category: CashMovementCategory
    reason: str = Field(max_length=255)
    reference: Optional100 = None
    idempotency_key: uuid.UUID | None = None

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("le motif doit contenir au moins 3 caractères")
        return value


class CashMovementOut(BaseModel):
    id: uuid.UUID
    cash_session_id: uuid.UUID
    cash_session_number: str
    cash_register_id: uuid.UUID
    cash_register_code: str
    cash_register_name: str
    site_id: uuid.UUID
    site_name: str
    movement_type: CashMovementType
    amount: Money
    # + entrée, − sortie.
    signed_amount: SignedMoney
    category: CashMovementCategory | None
    reason: str | None
    reference: str | None
    source_type: str | None
    source_id: uuid.UUID | None
    source_number: str | None
    payment_id: uuid.UUID | None
    occurred_at: datetime
    created_by_name: str | None
    # Solde de la session après ce mouvement (tous les mouvements de la session, dans l'ordre).
    balance_after: SignedMoney
