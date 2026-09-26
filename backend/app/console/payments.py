"""Paiements d'abonnement vus et décidés par TechNova (Phase 3.3-A, ADR-0032).

- Lecture : paiement + métadonnées plateforme de l'entreprise (nom) et de l'abonnement (plan) ;
  le déclarant est un utilisateur de l'entreprise, invisible pour la console (seul son
  identifiant technique figure dans la ligne).
- Décision **définitive** sous verrou de ligne : ``PENDING`` → ``CONFIRMED`` ou ``REJECTED``
  (motif obligatoire) ; une décision déjà prise répond ``409 payment_already_decided`` (deux
  administrateurs simultanés : une seule décision réussit). Double audit (plateforme + miroir
  de l'entreprise) dans la même transaction.
- **Aucune activation** : confirmer un paiement ne modifie ni l'abonnement ni l'entreprise ;
  l'activation viendra de la licence (3.3-B).

Le rôle SQL de la console ne peut modifier que les colonnes de décision, d'une ligne
``PENDING`` seulement (RLS ``platform_decide``) ; il ne crée ni ne supprime aucun paiement.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_tenant_action
from app.core.errors import ConflictError, NotFoundError
from app.platform.audit.service import RequestMeta
from app.platform.identity.models import User
from app.platform.subscriptions.models import (
    Subscription,
    SubscriptionPayment,
    SubscriptionPaymentStatus,
)
from app.platform.tenancy.models import Tenant
from app.shared.pagination import PageParams, apply_sort, escape_like, paginate_rows

NOT_FOUND = "subscription_payment_not_found"


class PaymentDecisionService:
    def __init__(self, db: Session, now: datetime) -> None:
        self.db = db
        self.now = now

    def _base(self) -> Select[Any]:
        return (
            select(
                SubscriptionPayment,
                Tenant.name.label("tenant_name"),
                Subscription.plan_code,
                User.email.label("decided_by_email"),
            )
            .join(Tenant, Tenant.id == SubscriptionPayment.tenant_id)
            .join(Subscription, Subscription.id == SubscriptionPayment.subscription_id)
            # Décideur : administrateur TechNova (seuls comptes visibles de la console).
            .outerjoin(User, User.id == SubscriptionPayment.decided_by)
        )

    def search(
        self,
        params: PageParams,
        *,
        status: SubscriptionPaymentStatus | None,
        tenant_id: uuid.UUID | None,
        search: str | None,
    ) -> tuple[list[Any], int]:
        stmt = self._base()
        if status:
            stmt = stmt.where(SubscriptionPayment.status == status)
        if tenant_id:
            stmt = stmt.where(SubscriptionPayment.tenant_id == tenant_id)
        if search:
            pattern = f"%{escape_like(search.strip())}%"
            stmt = stmt.where(SubscriptionPayment.declared_reference.ilike(pattern, escape="\\"))
        stmt = apply_sort(
            stmt,
            params.sort,
            {
                "created_at": SubscriptionPayment.created_at,
                "amount": SubscriptionPayment.amount,
                "status": SubscriptionPayment.status,
                "decided_at": SubscriptionPayment.decided_at,
            },
            default="-created_at",
            tiebreaker=SubscriptionPayment.id,
        )
        return paginate_rows(self.db, stmt, params)

    def row(self, payment_id: uuid.UUID) -> Any:
        found = self.db.execute(
            self._base().where(SubscriptionPayment.id == payment_id)
        ).one_or_none()
        if found is None:
            raise NotFoundError("Paiement introuvable", code=NOT_FOUND)
        return found

    def _lock_pending(self, payment_id: uuid.UUID) -> SubscriptionPayment:
        """Verrou de la ligne. La politique RLS de décision n'expose au verrou que les lignes
        ``PENDING`` : une ligne déjà décidée (ou décidée par une transaction concurrente dont
        on attendait le verrou) n'est pas renvoyée → ``409``, jamais une seconde décision."""
        payment = self.db.scalars(
            select(SubscriptionPayment)
            .where(SubscriptionPayment.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if payment is not None and payment.status is SubscriptionPaymentStatus.PENDING:
            return payment
        exists = self.db.scalar(
            select(SubscriptionPayment.status).where(SubscriptionPayment.id == payment_id)
        )
        if exists is None:
            raise NotFoundError("Paiement introuvable", code=NOT_FOUND)
        raise ConflictError(
            "Ce paiement a déjà été décidé",
            code="payment_already_decided",
            extra={"status": SubscriptionPaymentStatus(exists).value},
        )

    def _decide(
        self,
        payment_id: uuid.UUID,
        *,
        target: SubscriptionPaymentStatus,
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> None:
        payment = self._lock_pending(payment_id)
        payment.status = target
        payment.decided_by = actor.user_id
        payment.decided_at = self.now
        if target is SubscriptionPaymentStatus.REJECTED:
            payment.rejection_reason = reason
        self.db.flush()
        after: dict[str, Any] = {"status": target.value, "decided_at": self.now.isoformat()}
        if target is SubscriptionPaymentStatus.REJECTED:
            after["rejection_reason"] = reason
        record_tenant_action(
            self.db,
            actor=actor,
            action=f"subscription_payment.{target.value.lower()}",
            tenant_id=payment.tenant_id,
            target_type="subscription_payment",
            target_id=payment.id,
            before={"status": SubscriptionPaymentStatus.PENDING.value},
            after=after,
            reason=reason,
            meta=meta,
            data={
                "amount": payment.amount,
                "currency": payment.currency,
                "declared_reference": payment.declared_reference,
                # Payment CONFIRMED ≠ activation (3.3-B).
                "activation": False,
            },
        )

    def confirm(
        self, payment_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> None:
        self._decide(
            payment_id,
            target=SubscriptionPaymentStatus.CONFIRMED,
            reason=reason,
            actor=actor,
            meta=meta,
        )

    def reject(
        self, payment_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> None:
        self._decide(
            payment_id,
            target=SubscriptionPaymentStatus.REJECTED,
            reason=reason,
            actor=actor,
            meta=meta,
        )
