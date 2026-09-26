import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum
from app.platform.subscriptions.models import BillingPeriod

# Numérotation globale des licences (LIC-AAAA-NNNNN) : séquence PostgreSQL (migration 0021).
LICENSE_NUMBER_SEQUENCE = "license_number_seq"
MAX_ACTIVATIONS = 10_000


class LicenseStatus(StrEnum):
    """Statut **stocké** (ADR-0034). ``REVOKED`` est définitif (déclencheur ``licenses_final``) :
    une licence révoquée n'est jamais restaurée ; une réémission crée une nouvelle licence.
    L'état affiché (``NOT_YET_VALID`` / ``ACTIVE`` / ``EXPIRED``) est calculé à la lecture."""

    ISSUED = "ISSUED"
    REVOKED = "REVOKED"


class LicenseState(StrEnum):
    NOT_YET_VALID = "NOT_YET_VALID"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class License(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Licence d'un site (1 site = 1 abonnement = 1 licence en vigueur, ADR-0033/0034) :
    générée par TechNova à partir d'un paiement **confirmé**, signée par le Signing Service
    (Ed25519). Elle fige la période (jours du fuseau de l'entreprise), le plan, les modules,
    les fonctionnalités, les limites et le nombre de postes (``max_activations``).

    ``payload`` et ``signature`` : document ``.lic`` v1 tel que signé ; les colonnes
    ``plan_code``… en sont la copie interrogeable. Aucune colonne n'est modifiable après
    l'émission, sauf la révocation (ISSUED → REVOKED)."""

    __tablename__ = "licenses"
    __table_args__ = (
        # Abonnement du même tenant ET de ce site ; paiement de cet abonnement.
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "subscription_id"],
            ["subscriptions.tenant_id", "subscriptions.site_id", "subscriptions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id", "payment_id"],
            [
                "subscription_payments.tenant_id",
                "subscription_payments.subscription_id",
                "subscription_payments.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            ["licenses.tenant_id", "licenses.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("license_number"),
        # Une seule licence non révoquée par paiement.
        Index(
            "uq_licenses_issued_payment",
            "tenant_id",
            "payment_id",
            unique=True,
            postgresql_where=text("status = 'ISSUED'"),
        ),
        # Une licence n'est remplacée (réémission) qu'une fois.
        Index(
            "uq_licenses_supersedes",
            "tenant_id",
            "supersedes_id",
            unique=True,
            postgresql_where=text("supersedes_id IS NOT NULL"),
        ),
        Index("ix_licenses_subscription_period", "tenant_id", "subscription_id", "starts_at"),
        Index("ix_licenses_status_created", "status", "created_at"),
        CheckConstraint("valid_until >= valid_from", name="period_ordered"),
        CheckConstraint("ends_at > starts_at", name="instants_ordered"),
        CheckConstraint(
            f"max_activations BETWEEN 1 AND {MAX_ACTIVATIONS}", name="max_activations_range"
        ),
        CheckConstraint("license_version >= 1", name="version_positive"),
        CheckConstraint(
            "(supersedes_id IS NULL) = (license_version = 1)", name="supersedes_consistent"
        ),
        CheckConstraint(
            "(status = 'REVOKED') = (revoked_at IS NOT NULL AND revocation_reason IS NOT NULL)",
            name="revocation_consistent",
        ),
        CheckConstraint(
            "revocation_reason IS NULL OR length(btrim(revocation_reason)) > 0",
            name="revocation_reason_present",
        ),
        CheckConstraint("license_number ~ '^LIC-[0-9]{4}-[0-9]{5,}$'", name="number_format"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    subscription_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    license_number: Mapped[str] = mapped_column(String(20), nullable=False)
    license_version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    plan_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("plans.code", ondelete="RESTRICT"), nullable=False
    )
    billing_period: Mapped[BillingPeriod] = mapped_column(
        str_enum(BillingPeriod, "billing_period"), nullable=False
    )
    # Période contractuelle, bornes incluses, jours du fuseau de l'entreprise (ADR-0028) …
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    # … et ses instants UTC : [minuit local de valid_from, minuit local du lendemain de
    # valid_until[ (licence en vigueur : starts_at <= maintenant < ends_at).
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_activations: Mapped[int] = mapped_column(Integer, nullable=False)
    modules: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    features: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Administrateur TechNova (nul : CLI).
    issued_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[LicenseStatus] = mapped_column(
        str_enum(LicenseStatus, "license_status"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text)

    def state(self, now: datetime) -> LicenseState:
        if self.status is LicenseStatus.REVOKED:
            return LicenseState.REVOKED
        if now < self.starts_at:
            return LicenseState.NOT_YET_VALID
        if now >= self.ends_at:
            return LicenseState.EXPIRED
        return LicenseState.ACTIVE
