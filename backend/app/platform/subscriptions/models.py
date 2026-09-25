from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, UniqueConstraint
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
