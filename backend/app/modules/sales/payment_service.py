"""Paiements des ventes (Phase 2.7, ADR-0020).

La validation d'une vente est indépendante de son encaissement : une vente VALIDÉE (stock déjà
sorti) peut être non payée, partiellement payée ou payée ; le reste dû d'une vente avec client
constitue une créance (phase ultérieure).

- **Aucun état stocké** : montant payé = somme des paiements EFFECTUÉS ; reste = total − payé ;
  état (non payée / partiellement payée / payée) calculé à la lecture, par agrégation SQL.
- **Aucun surpaiement** : le solde est recalculé par le serveur sous le verrou de la vente
  (``SELECT … FOR UPDATE``) : deux encaissements simultanés s'exécutent l'un après l'autre,
  le second voit le premier et est refusé s'il dépasse le reste dû.
- **Immuable après encaissement** : ni montant ni moyen modifiables ; une erreur se corrige par
  annulation (motif, auteur, date) — le paiement reste dans l'historique — puis nouveau
  paiement. Annuler un paiement n'annule PAS la vente et ne touche jamais au stock.
- **Double soumission** : clé d'idempotence facultative fournie par le client ; la même clé
  renvoie le paiement déjà créé (réponse rejouée) au lieu d'un doublon.
- **Moyens configurés** (Lot 1, ADR-0037) : le paiement fige le moyen choisi (identifiant,
  libellé, type) ; le **type** seul gouverne le comportement.
- **Espèces** : montant reçu et monnaie calculés par le serveur, sur la seule partie espèces
  (aucun autre moyen ne rend de monnaie). Site **avec** caisse (ADR-0022, révisée) : le
  paiement et son mouvement sont créés dans la MÊME transaction, dans la session ouverte **par
  l'utilisateur** sur ce site (site + poste + utilisateur) ; son annulation crée la sortie
  inverse dans la même session, si elle est encore ouverte. Site **sans** caisse : aucune
  session, aucun mouvement.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.modules.sales.cash_port import cash_ledger
from app.modules.sales.models import (
    ConfiguredPaymentMethod,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Sale,
    SalePaymentStatus,
    SaleStatus,
)
from app.modules.sales.payment_methods import PaymentMethodService
from app.modules.sales.schemas import PaymentCreate, PaymentOut, PaymentSummary
from app.modules.stock.api import round_money
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.identity.models import User
from app.platform.sequences.service import next_number

SEQUENCE_KEY = "payment"
PREFIX = "PAY"
ZERO = Decimal("0.00")
NOT_FOUND = "payment_not_found"
# Montants engagés sur le solde : effectués, et en attente (encaissements asynchrones futurs)
# pour qu'un paiement en cours de confirmation ne puisse pas être doublé.
COMMITTED = (PaymentStatus.COMPLETED, PaymentStatus.PENDING)


def payment_status(total: Decimal, paid: Decimal) -> SalePaymentStatus:
    if paid <= 0 and total > 0:
        return SalePaymentStatus.UNPAID
    if paid < total:
        return SalePaymentStatus.PARTIALLY_PAID
    return SalePaymentStatus.PAID


def summarize(total: Decimal, paid: Decimal) -> PaymentSummary:
    return PaymentSummary(
        total=total,
        paid_amount=paid,
        remaining_amount=max(total - paid, ZERO),
        payment_status=payment_status(total, paid),
    )


def paid_amounts(
    db: Session,
    sale_ids: Iterable[uuid.UUID],
    statuses: Sequence[PaymentStatus] = (PaymentStatus.COMPLETED,),
) -> dict[uuid.UUID, Decimal]:
    """Somme des paiements par vente, en UNE requête d'agrégation (aucun N+1)."""
    ids = set(sale_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(Payment.sale_id, func.sum(Payment.amount))
        .where(Payment.sale_id.in_(ids), Payment.status.in_(statuses))
        .group_by(Payment.sale_id)
    ).all()
    return {row[0]: row[1] for row in rows}


def paid_subquery() -> Any:
    """Montant payé par vente (paiements effectués), pour jointures et filtres de listes."""
    return (
        select(Payment.sale_id, func.sum(Payment.amount).label("paid"))
        .where(Payment.status == PaymentStatus.COMPLETED)
        .group_by(Payment.sale_id)
        .subquery("sale_paid")
    )


class PaymentService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Lecture ------------------------------------------------------------------------------

    def _sale(self, sale_id: uuid.UUID, *, lock: bool = False, own_scope: bool = True) -> Sale:
        # Même contrôle d'accès que la vente : tenant (RLS), site accessible / sélectionné,
        # portée « ses propres ventes » sans ``sales.sale.view_all``.
        from app.modules.sales.service import SaleService

        return SaleService(self.db, self.ctx, self.now).get(sale_id, lock=lock, own_scope=own_scope)

    def history(self, sale_id: uuid.UUID, *, own_scope: bool = True) -> tuple[Sale, list[Payment]]:
        sale = self._sale(sale_id, own_scope=own_scope)
        payments = list(
            self.db.scalars(
                select(Payment)
                .where(Payment.sale_id == sale.id)
                .order_by(Payment.paid_at, Payment.number)
            )
        )
        return sale, payments

    def history_out(self, sale_id: uuid.UUID, *, own_scope: bool = True) -> list[PaymentOut]:
        """Historique complet des paiements d'une vente (annulés compris), pour l'API."""
        sale, payments = self.history(sale_id, own_scope=own_scope)
        return self.to_out(sale, payments)

    def summary(self, sale: Sale) -> PaymentSummary | None:
        """Solde d'une vente validée (``None`` pour un brouillon ou une vente annulée)."""
        if sale.status is not SaleStatus.VALIDATED:
            return None
        paid = paid_amounts(self.db, {sale.id}).get(sale.id, ZERO)
        return summarize(sale.total, paid)

    def get(
        self, sale_id: uuid.UUID, payment_id: uuid.UUID, *, lock: bool = False
    ) -> tuple[Sale, Payment]:
        sale = self._sale(sale_id, lock=lock)
        stmt = select(Payment).where(Payment.id == payment_id, Payment.sale_id == sale.id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        payment = self.db.scalars(stmt).one_or_none()
        if payment is None:
            raise NotFoundError("Paiement introuvable", code=NOT_FOUND)
        return sale, payment

    # --- Écritures ----------------------------------------------------------------------------

    def resolve_method(self, site_id: uuid.UUID, data: PaymentCreate) -> ConfiguredPaymentMethod:
        """Moyen utilisable sur le site, champs cohérents avec son type (référence, monnaie)."""
        method = PaymentMethodService(self.db, self.ctx, self.now).resolve(
            site_id, data.payment_method_id, data.method
        )
        if method.reference_required and not (data.reference or "").strip():
            raise BusinessRuleError(
                "La référence de la transaction est obligatoire pour ce moyen de paiement",
                code="payment_reference_required",
                extra={"label": method.label},
            )
        if method.kind is not PaymentMethod.CASH and data.amount_received is not None:
            raise BusinessRuleError(
                "Seules les espèces rendent la monnaie : indiquez le montant payé",
                code="change_not_allowed",
            )
        if (
            data.amount is not None
            and data.amount_received is not None
            and round_money(data.amount_received) < round_money(data.amount)
        ):
            raise BusinessRuleError(
                "Le montant reçu est inférieur au montant payé",
                code="cash_received_insufficient",
            )
        return method

    @staticmethod
    def applied_amount(data: PaymentCreate, remaining: Decimal) -> Decimal:
        """Montant imputé sur la vente : celui indiqué, sinon (espèces) min(reçu, reste dû)."""
        if data.amount is not None:
            return round_money(data.amount)
        assert data.amount_received is not None
        return min(round_money(data.amount_received), remaining)

    def plan(
        self, site_id: uuid.UUID, total: Decimal, payments: Sequence[PaymentCreate]
    ) -> list[tuple[PaymentCreate, Decimal]]:
        """Paiements immédiats d'une validation, dans l'ordre d'imputation (autres moyens
        d'abord, espèces ensuite : la monnaie ne porte que sur les espèces) avec leur montant
        imputé. Aucune écriture : sert au contrôle du crédit avant la sortie de stock."""
        resolved = [(p, self.resolve_method(site_id, p)) for p in payments]
        ordered = [r for r in resolved if r[1].kind is not PaymentMethod.CASH] + [
            r for r in resolved if r[1].kind is PaymentMethod.CASH
        ]
        remaining = total
        planned: list[tuple[PaymentCreate, Decimal]] = []
        for data, _ in ordered:
            amount = self.applied_amount(data, max(remaining, ZERO))
            if amount <= 0 or amount > remaining:
                raise BusinessRuleError(
                    "Le montant dépasse le reste à payer",
                    code="payment_exceeds_balance",
                    extra={"remaining": format(max(remaining, ZERO), "f")},
                )
            remaining -= amount
            planned.append((data.model_copy(update={"amount": amount}), amount))
        return planned

    def create(self, sale_id: uuid.UUID, data: PaymentCreate) -> tuple[Payment, bool]:
        """Encaissement (effectué immédiatement ; saisie manuelle). Renvoie ``(paiement,
        rejoué)``."""
        # Verrou de la vente : sérialise les paiements concurrents et l'annulation de la vente.
        sale = self._sale(sale_id, lock=True)
        if data.idempotency_key is not None:
            existing = self.db.scalars(
                select(Payment).where(Payment.idempotency_key == data.idempotency_key)
            ).one_or_none()
            if existing is not None:
                same_method = (
                    existing.payment_method_id == data.payment_method_id
                    if data.payment_method_id is not None
                    else existing.method is data.method
                )
                same_amount = (
                    existing.amount == round_money(data.amount)
                    if data.amount is not None
                    else existing.amount_received == round_money(data.amount_received or ZERO)
                )
                if existing.sale_id == sale.id and same_method and same_amount:
                    return existing, True  # double soumission : réponse rejouée
                raise ConflictError(
                    "Clé d'idempotence déjà utilisée pour un autre paiement",
                    code="idempotency_key_reused",
                )
        if sale.status is not SaleStatus.VALIDATED:
            raise ConflictError(
                "Seule une vente validée peut être payée",
                code="sale_not_payable",
                extra={"status": sale.status.value},
            )
        method = self.resolve_method(sale.site_id, data)
        committed = paid_amounts(self.db, {sale.id}, COMMITTED).get(sale.id, ZERO)
        remaining = max(sale.total - committed, ZERO)
        if remaining <= 0:
            raise BusinessRuleError(
                "Cette vente est déjà entièrement payée", code="sale_already_paid"
            )
        amount = self.applied_amount(data, remaining)
        if amount > remaining:
            raise BusinessRuleError(
                "Le montant dépasse le reste à payer",
                code="payment_exceeds_balance",
                extra={"remaining": format(remaining, "f")},
            )
        received: Decimal | None = None
        change: Decimal | None = None
        if method.kind is PaymentMethod.CASH and data.amount_received is not None:
            received = round_money(data.amount_received)
            change = received - amount
        payment = Payment(
            tenant_id=self.ctx.tenant_id,
            number=next_number(self.db, self.ctx.tenant_id, SEQUENCE_KEY, PREFIX),
            sale_id=sale.id,
            site_id=sale.site_id,
            amount=amount,
            method=method.kind,
            payment_method_id=method.id,
            method_label=method.label,
            provider=data.provider,
            reference=data.reference,
            amount_received=received,
            change_given=change,
            status=PaymentStatus.COMPLETED,
            paid_at=self.now,
            idempotency_key=data.idempotency_key,
            created_by=self.ctx.user.id,
        )
        self.db.add(payment)
        self.db.flush()
        cash: dict[str, Any] = {}
        if payment.method is PaymentMethod.CASH:
            if received is not None:
                cash = {"amount_received": format(received, "f"), "change": format(change, "f")}
            # Site avec caisse : mouvement dans la session de l'utilisateur (même transaction).
            # Site sans caisse (ou module Caisse inactif) : aucune session, aucun mouvement.
            ledger = cash_ledger(self.db, self.ctx, self.now, require_module=True)
            if ledger is not None and ledger.site_cash_enabled(sale.site_id):
                movement = ledger.record_sale_cash_in(
                    site_id=sale.site_id,
                    payment_id=payment.id,
                    payment_number=payment.number,
                    amount=payment.amount,
                    sale_id=sale.id,
                    sale_number=sale.number or "",
                    cash_register_id=data.cash_register_id,
                )
                cash |= {
                    "cash_session_id": str(movement.cash_session_id),
                    "cash_register_id": str(movement.cash_register_id),
                }
        after = summarize(sale.total, committed + amount)
        self._audit("created", sale, payment, {"status": PaymentStatus.COMPLETED.value, **cash})
        self._audit("completed", sale, payment, {**self._balance(after), **cash})
        return payment, False

    def cancel(self, sale_id: uuid.UUID, payment_id: uuid.UUID, reason: str) -> Payment:
        """Annulation d'un paiement (erreur de saisie…) : conservé dans l'historique, ne compte
        plus dans le montant payé. La vente reste VALIDÉE ; le stock n'est pas touché."""
        sale, payment = self.get(sale_id, payment_id, lock=True)
        if payment.status is PaymentStatus.CANCELLED:
            raise ConflictError("Paiement déjà annulé", code="payment_already_cancelled")
        previous = payment.status
        if payment.method is PaymentMethod.CASH and previous is PaymentStatus.COMPLETED:
            # Espèces rendues : sortie inverse dans la session d'origine (encore ouverte).
            ledger = cash_ledger(self.db, self.ctx, self.now, require_module=False)
            if ledger is not None:
                ledger.record_sale_cash_reversal(payment_id=payment.id, reason=reason)
        payment.status = PaymentStatus.CANCELLED
        payment.cancelled_at = self.now
        payment.cancelled_by = self.ctx.user.id
        payment.cancellation_reason = reason
        self.db.flush()
        paid = paid_amounts(self.db, {sale.id}).get(sale.id, ZERO)
        self._audit(
            "cancelled",
            sale,
            payment,
            {
                "previous_status": previous.value,
                "status": PaymentStatus.CANCELLED.value,
                "reason": reason,
                **self._balance(summarize(sale.total, paid)),
            },
        )
        return payment

    # --- Audit et sortie API ------------------------------------------------------------------

    @staticmethod
    def _balance(summary: PaymentSummary) -> dict[str, Any]:
        return {
            "sale_total": format(summary.total, "f"),
            "paid_amount": format(summary.paid_amount, "f"),
            "remaining_amount": format(summary.remaining_amount, "f"),
            "payment_status": summary.payment_status.value,
        }

    def _audit(self, action: str, sale: Sale, payment: Payment, data: dict[str, Any]) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"payment.{action}",
            entity_type="payment",
            entity_id=payment.id,
            site_id=payment.site_id,
            data={
                "number": payment.number,
                "sale_id": str(sale.id),
                "sale_number": sale.number,
                "amount": format(payment.amount, "f"),
                "method": payment.method.value,
                "payment_method_id": str(payment.payment_method_id),
                "method_label": payment.method_label,
                "paid_at": payment.paid_at.isoformat(),
                **data,
            },
        )

    def to_out(self, sale: Sale, payments: Sequence[Payment]) -> list[PaymentOut]:
        users = {u for p in payments for u in (p.created_by, p.cancelled_by) if u}
        names: dict[Any, str] = (
            {
                row[0]: row[1]
                for row in self.db.execute(
                    select(User.id, User.full_name).where(User.id.in_(users))
                )
            }
            if users
            else {}
        )
        return [
            PaymentOut(
                id=p.id,
                number=p.number,
                sale_id=p.sale_id,
                sale_number=sale.number,
                site_id=p.site_id,
                amount=p.amount,
                method=p.method,
                payment_method_id=p.payment_method_id,
                method_label=p.method_label,
                provider=p.provider,
                amount_received=p.amount_received,
                change_given=p.change_given,
                status=p.status,
                reference=p.reference,
                paid_at=p.paid_at,
                created_at=p.created_at,
                created_by_name=names.get(p.created_by) if p.created_by else None,
                cancelled_at=p.cancelled_at,
                cancelled_by_name=names.get(p.cancelled_by) if p.cancelled_by else None,
                cancellation_reason=p.cancellation_reason,
            )
            for p in payments
        ]
