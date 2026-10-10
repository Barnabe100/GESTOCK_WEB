"""API publique du module Ventes pour les autres modules (créances, futurs caisse et POS), qui
n'importent jamais ses modèles directement (règle d'architecture 10). ``register_cash_ledger`` :
implémentation de la caisse des paiements espèces (module Caisse). ``register_sale_origin`` et
``sale_financial_states`` : ventes issues d'un document d'un autre module (R2-D, ADR-0049)."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.sales.cash_port import CashLedger, register_cash_ledger
from app.modules.sales.credit import balances_query, customer_exposure, open_receivables_query
from app.modules.sales.models import (
    ORIGIN_RESTAURANT_ORDER,
    CreditStatus,
    PaymentStatus,
    Sale,
    SaleChannel,
    SalePaymentStatus,
    SaleStatus,
)
from app.modules.sales.origin_port import register_sale_origin
from app.modules.sales.payment_service import ZERO, PaymentService, paid_amounts, payment_status
from app.modules.sales.schemas import (
    CheckoutOut,
    CreditOverride,
    ExpiredLotOverride,
    PaymentCreate,
    PaymentOut,
    SaleCheckout,
)
from app.modules.sales.service import OriginLine, SaleService


@dataclass(frozen=True)
class SaleKeyRef:
    """Vente déjà enregistrée pour une clé d'idempotence (rejeu d'un règlement)."""

    id: uuid.UUID
    site_id: uuid.UUID
    origin_type: str | None
    origin_id: uuid.UUID | None


@dataclass(frozen=True)
class SaleFinancialState:
    """État financier d'une vente, lu sur la vente (jamais copié ailleurs) : numéro, statut,
    total, montant payé, reste dû, état d'encaissement (P-11) ; aucun détail des paiements."""

    id: uuid.UUID
    number: str | None
    status: SaleStatus
    total: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
    payment_status: SalePaymentStatus | None


def sale_by_idempotency_key(db: Session, key: uuid.UUID) -> SaleKeyRef | None:
    row = db.execute(
        select(Sale.id, Sale.site_id, Sale.origin_type, Sale.origin_id).where(
            Sale.idempotency_key == key
        )
    ).one_or_none()
    return None if row is None else SaleKeyRef(*row)


def sale_financial_states(
    db: Session, sale_ids: set[uuid.UUID]
) -> dict[uuid.UUID, SaleFinancialState]:
    """États financiers de ventes du tenant (RLS), en une agrégation des paiements."""
    if not sale_ids:
        return {}
    rows = db.execute(
        select(Sale.id, Sale.number, Sale.status, Sale.total).where(Sale.id.in_(sale_ids))
    ).all()
    paid = paid_amounts(db, {row.id for row in rows if row.status is SaleStatus.VALIDATED})
    result: dict[uuid.UUID, SaleFinancialState] = {}
    for row in rows:
        validated = row.status is SaleStatus.VALIDATED
        amount = paid.get(row.id, ZERO) if validated else ZERO
        result[row.id] = SaleFinancialState(
            id=row.id,
            number=row.number,
            status=row.status,
            total=row.total,
            paid_amount=amount,
            remaining_amount=max(row.total - amount, ZERO) if validated else ZERO,
            payment_status=payment_status(row.total, amount) if validated else None,
        )
    return result


__all__ = [
    "ORIGIN_RESTAURANT_ORDER",
    "CheckoutOut",
    "CreditOverride",
    "ExpiredLotOverride",
    "OriginLine",
    "PaymentCreate",
    "SaleFinancialState",
    "SaleKeyRef",
    "SaleStatus",
    "register_sale_origin",
    "sale_by_idempotency_key",
    "sale_financial_states",
    "CreditStatus",
    "SaleChannel",
    "SaleCheckout",
    "SaleService",
    "CashLedger",
    "register_cash_ledger",
    "PaymentOut",
    "PaymentService",
    "PaymentStatus",
    "SalePaymentStatus",
    "balances_query",
    "customer_exposure",
    "open_receivables_query",
    "payment_status",
]
