import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum


class BillingPeriod(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class SubscriptionStatus(StrEnum):
    # Souscription commerciale enregistrée (inscription publique sans essai) : aucun paiement
    # confirmé, aucune licence activée. Accès administratif seulement (politique d'accès) ;
    # passe à ACTIVE par l'activation d'une licence après paiement confirmé par TechNova
    # (PLAN → SUBSCRIPTION → PAYMENT → LICENCE → ACTIVATION, ADR-0025).
    PENDING_ACTIVATION = "pending_activation"
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"  # période échue, dans le délai de grâce
    EXPIRED = "expired"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class Subscription(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Abonnement courant du tenant. L'expiration ne supprime jamais de données."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        # Cible des clés étrangères composites (paiements d'abonnement du même tenant).
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint(
            "(price_at_subscription IS NULL) = (currency_at_subscription IS NULL)",
            name="price_snapshot_complete",
        ),
    )

    plan_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("plans.code", ondelete="RESTRICT"), nullable=False
    )
    billing_period: Mapped[BillingPeriod] = mapped_column(
        str_enum(BillingPeriod, "billing_period"), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        str_enum(SubscriptionStatus, "subscription_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Prix de la période souscrite, figé à la souscription : une modification ultérieure du
    # tarif du plan ne change pas rétroactivement l'abonnement (nul : aucun prix affiché).
    price_at_subscription: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency_at_subscription: Mapped[str | None] = mapped_column(String(3))


MONEY = Numeric(18, 2)


class SubscriptionPaymentStatus(StrEnum):
    """Paiement d'abonnement (ADR-0025, ADR-0032) : déclaré par l'entreprise, décidé par
    TechNova. Décision **définitive** : jamais de retour à ``PENDING`` ni de changement."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class SubscriptionPaymentMethod(StrEnum):
    """Moyen de règlement déclaré (code technique, indépendant de la langue)."""

    BANK_TRANSFER = "BANK_TRANSFER"
    MOBILE_MONEY = "MOBILE_MONEY"
    CASH = "CASH"
    CHECK = "CHECK"
    CARD = "CARD"
    OTHER = "OTHER"


class SubscriptionPayment(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Paiement de l'abonnement **à TechNova** (Phase 3.3-A) — distinct des paiements des
    ventes (table ``payments``, module ``sales``). Déclaré par l'entreprise (``PENDING``),
    confirmé ou rejeté par TechNova dans la console. Un paiement confirmé **n'active rien** :
    l'activation viendra de la licence (3.3-B). Jamais supprimé."""

    __tablename__ = "subscription_payments"
    __table_args__ = (
        # L'abonnement appartient au même tenant (garanti par la base).
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["subscriptions.tenant_id", "subscriptions.id"],
            ondelete="RESTRICT",
        ),
        # Une seule déclaration par clé d'idempotence et par tenant.
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="iso_currency"),
        CheckConstraint("period_end > period_start", name="period_ordered"),
        CheckConstraint("length(btrim(declared_reference)) > 0", name="reference_present"),
        CheckConstraint(
            "(status = 'PENDING') = (decided_by IS NULL AND decided_at IS NULL)",
            name="decision_consistent",
        ),
        CheckConstraint(
            "(status = 'REJECTED') = (rejection_reason IS NOT NULL)", name="rejection_has_reason"
        ),
        CheckConstraint(
            "rejection_reason IS NULL OR length(btrim(rejection_reason)) > 0",
            name="rejection_reason_present",
        ),
        Index("ix_subscription_payments_tenant_created", "tenant_id", "created_at"),
        Index("ix_subscription_payments_status_created", "status", "created_at"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Fixée par le serveur : devise figée de l'abonnement, sinon celle de l'entreprise.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Période couverte déclarée (jours du fuseau de l'entreprise, ADR-0028).
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[SubscriptionPaymentMethod] = mapped_column(
        str_enum(SubscriptionPaymentMethod, "subscription_payment_method"), nullable=False
    )
    # Référence de l'opération (n° de virement, de transaction Mobile Money…).
    declared_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    declared_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SubscriptionPaymentStatus] = mapped_column(
        str_enum(SubscriptionPaymentStatus, "subscription_payment_status"), nullable=False
    )
    # Décision TechNova (administrateur de la plateforme).
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
