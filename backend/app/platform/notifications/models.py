import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantFiltered
from app.platform.models_base import IdMixin, TenantScopedMixin, str_enum

# Échéance d'un abonnement de site (dernier jour couvert par la licence ou la période).
SUBSCRIPTION_EXPIRY = "subscription.expiry"


class NotificationStatus(StrEnum):
    """``SENT`` : visible des membres ; ``SKIPPED`` : étape passée alors que le job n'a pas
    tourné (jamais affichée, jamais rattrapée en rafale) — conservée pour la traçabilité."""

    SENT = "SENT"
    SKIPPED = "SKIPPED"


class Notification(IdMixin, TenantScopedMixin, Base):
    """Notification applicative d'une entreprise, rattachée à un site quand elle le concerne.
    Écrite par le job de la plateforme (``stockmanager notifications run``), jamais modifiée ni
    supprimée : la table est aussi l'historique. Unicité logique d'un rappel d'échéance :
    ``(abonnement du site, type, étape, date d'échéance)`` — un nouveau renouvellement change
    l'échéance, donc la série de rappels."""

    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["subscriptions.tenant_id", "subscriptions.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "subscription_id", "kind", "step", "reference_date"),
        UniqueConstraint("tenant_id", "id"),
        Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
    )

    site_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # Jours avant (positif) ou après (négatif) l'échéance : 30, 15, …, 0 (J0), -1 (J+1), -7.
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    # Échéance : dernier jour couvert, dans le fuseau de l'entreprise.
    reference_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        str_enum(NotificationStatus, "notification_status"), nullable=False
    )
    # Contexte affiché (plan, licence, statut effectif…) ; jamais de donnée métier.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationRead(TenantFiltered, Base):
    """Lecture d'une notification par un membre (état lu / non lu propre à chaque membre)."""

    __tablename__ = "notification_reads"
    __table_args__ = (
        PrimaryKeyConstraint("notification_id", "user_id"),
        ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["notifications.tenant_id", "notifications.id"],
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
