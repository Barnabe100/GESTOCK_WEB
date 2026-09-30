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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
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
    """Abonnement d'un **site** (Phase 3.3-B, ADR-0033) : 1 site = 1 abonnement = 1 licence.
    Une entreprise à plusieurs sites a plusieurs abonnements, indépendants (plan, période,
    statut, limites). ``site_id`` n'est nul que pour l'abonnement pris à l'inscription, avant
    la création du premier site (au plus un par entreprise), auquel il est alors rattaché.
    L'expiration ne supprime jamais de données."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        # Un abonnement par site ; jamais le site d'un autre tenant.
        UniqueConstraint("tenant_id", "site_id"),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        # Au plus un abonnement en attente de site (inscription publique) par entreprise.
        Index(
            "uq_subscriptions_unattached",
            "tenant_id",
            unique=True,
            postgresql_where=text("site_id IS NULL"),
        ),
        # Cible des clés étrangères composites (paiements d'abonnement du même tenant ;
        # licences du même tenant et du même site).
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "site_id", "id"),
        CheckConstraint(
            "(price_at_subscription IS NULL) = (currency_at_subscription IS NULL)",
            name="price_snapshot_complete",
        ),
        CheckConstraint("requested_activations >= 1", name="requested_activations_positive"),
        # Tarif par poste figé avec le prix de base (3.3-B4).
        CheckConstraint(
            "(included_activations_at_subscription IS NULL) = (price_at_subscription IS NULL)",
            name="included_activations_snapshot_complete",
        ),
        CheckConstraint(
            "activation_price_at_subscription IS NULL OR price_at_subscription IS NOT NULL",
            name="activation_price_snapshot_complete",
        ),
    )

    site_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

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
    # Tarif par poste figé en même temps (3.3-B4) : postes compris dans le prix de base et prix
    # de chaque poste supplémentaire (nul : sur devis).
    included_activations_at_subscription: Mapped[int | None] = mapped_column(Integer)
    activation_price_at_subscription: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    # Nombre de postes demandé par le client à la souscription (défaut : 1) ; TechNova le
    # confirme ou l'ajuste à la génération de la licence, qui le fige (3.3-B).
    requested_activations: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )


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
        # Cible de la clé étrangère des licences (paiement de CET abonnement).
        UniqueConstraint("tenant_id", "subscription_id", "id"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "requested_activations IS NULL OR requested_activations BETWEEN 1 AND 10000",
            name="requested_activations_range",
        ),
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
    # Nombre de postes demandé EXPLICITEMENT pour la période payée (3.3-B4) ; nul : quota de la
    # licence en vigueur reconduit. TechNova le confirme ou l'ajuste à la génération.
    requested_activations: Mapped[int | None] = mapped_column(Integer)
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
