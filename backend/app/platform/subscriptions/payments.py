"""Paiements d'abonnement déclarés par l'entreprise (Phase 3.3-A, ADR-0032).

Chaîne : PLAN → SUBSCRIPTION → **PAYMENT** → LICENCE → ACTIVATION. L'entreprise **déclare** un
paiement (``PENDING``) ; seul TechNova le confirme ou le rejette (console). Un paiement
confirmé n'active rien : l'activation viendra de la licence (3.3-B).

- Idempotence (même règle que les paiements des ventes, ADR-0020) : une même clé renvoie la
  déclaration existante (``200``) si la demande est identique, sinon ``409
  idempotency_key_reused`` ; unicité ``(tenant_id, idempotency_key)`` en base.
- La devise est fixée par le serveur (devise figée de l'abonnement, sinon celle de
  l'entreprise) ; aucun champ de décision n'est accepté du client (``extra="forbid"``).
- L'abonnement est verrouillé pendant la déclaration (déclarations concurrentes sérialisées) ;
  la RLS et la clé étrangère composite garantissent qu'il appartient au tenant.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.platform.audit.service import audit_action
from app.platform.context import RequestContext
from app.platform.licensing.renewal import renewal_quote
from app.platform.subscriptions.models import (
    Subscription,
    SubscriptionPayment,
    SubscriptionPaymentStatus,
)
from app.platform.subscriptions.schemas import SubscriptionPaymentCreate
from app.shared.clock import utcnow
from app.shared.pagination import PageParams, apply_sort, paginate


def _same_declaration(existing: SubscriptionPayment, data: SubscriptionPaymentCreate) -> bool:
    """Même demande (double soumission) : mêmes champs **saisis** par le client ; la période et
    le montant calculés par le serveur n'en font pas partie."""
    return (
        existing.subscription_id == data.subscription_id
        and existing.payment_method is data.payment_method
        and existing.declared_reference == data.declared_reference
        and existing.requested_activations == data.requested_activations
        and (data.amount is None or existing.amount == data.amount)
    )


class SubscriptionPaymentService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def search(
        self, params: PageParams, status: SubscriptionPaymentStatus | None
    ) -> tuple[list[SubscriptionPayment], int]:
        stmt = select(SubscriptionPayment)
        if status:
            stmt = stmt.where(SubscriptionPayment.status == status)
        stmt = apply_sort(
            stmt,
            params.sort,
            {
                "created_at": SubscriptionPayment.created_at,
                "amount": SubscriptionPayment.amount,
                "status": SubscriptionPayment.status,
                "period_start": SubscriptionPayment.period_start,
            },
            default="-created_at",
            tiebreaker=SubscriptionPayment.id,
        )
        return paginate(self.db, stmt, params)

    def get(self, payment_id: uuid.UUID) -> SubscriptionPayment:
        payment = self.db.get(SubscriptionPayment, payment_id)
        if payment is None:
            raise NotFoundError("Paiement introuvable", code="subscription_payment_not_found")
        return payment

    def declare(self, data: SubscriptionPaymentCreate) -> tuple[SubscriptionPayment, bool]:
        """Déclaration ``PENDING``. Renvoie ``(paiement, rejoué)``."""
        # Verrou de l'abonnement (RLS : invisible s'il appartient à un autre tenant).
        subscription = self.db.scalars(
            select(Subscription).where(Subscription.id == data.subscription_id).with_for_update()
        ).one_or_none()
        if subscription is None:
            raise NotFoundError("Abonnement introuvable", code="subscription_not_found")
        existing = self.db.scalars(
            select(SubscriptionPayment).where(
                SubscriptionPayment.idempotency_key == data.idempotency_key
            )
        ).one_or_none()
        if existing is not None:
            if _same_declaration(existing, data):
                return existing, True  # double soumission : réponse rejouée
            raise ConflictError(
                "Clé d'idempotence déjà utilisée pour une autre déclaration",
                code="idempotency_key_reused",
            )
        quote = renewal_quote(
            self.db,
            subscription,
            timezone=self.ctx.tenant.timezone,
            now=utcnow(),
            requested_activations=data.requested_activations,
        )
        # Montant : calculé par le serveur (tarif figé de l'abonnement) ; saisi par le client
        # seulement pour une offre sur devis (aucun tarif figé).
        if quote.amount is not None:
            if data.amount is not None:
                raise BusinessRuleError(
                    "Le montant est calculé par le serveur", code="amount_computed_by_server"
                )
            amount = quote.amount
        elif data.amount is None:
            raise BusinessRuleError(
                "Aucun tarif pour cette offre : indiquez le montant convenu avec TechNova",
                code="amount_required",
            )
        else:
            amount = data.amount
        payment = SubscriptionPayment(
            tenant_id=self.ctx.tenant_id,
            subscription_id=subscription.id,
            amount=amount,
            currency=subscription.currency_at_subscription or self.ctx.tenant.currency,
            # Période calculée par le serveur (informative : celle de la licence sera recalculée
            # par le serveur à sa génération) ; jamais fournie par le client.
            period_start=quote.valid_from,
            period_end=quote.valid_until,
            payment_method=data.payment_method,
            declared_reference=data.declared_reference,
            idempotency_key=data.idempotency_key,
            # Changement de quota : seulement s'il est demandé explicitement (R1).
            requested_activations=quote.activations if quote.activations_explicit else None,
            declared_by=self.ctx.user.id,
            status=SubscriptionPaymentStatus.PENDING,
        )
        self.db.add(payment)
        self.db.flush()
        details: dict[str, Any] = {
            "amount": format(payment.amount, "f"),
            "currency": payment.currency,
            "period_start": payment.period_start.isoformat(),
            "period_end": payment.period_end.isoformat(),
            "payment_method": payment.payment_method.value,
            "declared_reference": payment.declared_reference,
            "requested_activations": payment.requested_activations,
            "kind": quote.kind,
        }
        audit_action(
            self.db,
            self.ctx,
            "subscription_payment.declared",
            entity_type="subscription_payment",
            entity_id=payment.id,
            data=details,
        )
        return payment, False
