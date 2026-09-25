"""Tables propres à la console TechNova (ADR-0031), hors de tout tenant.

Aucun droit pour le rôle applicatif des tenants : seul le rôle SQL de la console
(``stockmanager_platform``) y accède, avec des droits minimaux (voir la migration 0017).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin
from app.shared.clock import utcnow


class PlatformSession(IdMixin, Base):
    """Session d'un administrateur TechNova. Jeton opaque (cookie HttpOnly) stocké haché ;
    expiration absolue et d'inactivité ; révocable (déconnexion, retrait du statut)."""

    __tablename__ = "platform_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))


class PlatformAuditLog(IdMixin, Base):
    """Journal d'audit de la plateforme (actions TechNova), distinct du journal des tenants.

    Append-only : le rôle de la console n'a que SELECT et INSERT, et un déclencheur refuse toute
    modification ou suppression (y compris par le propriétaire)."""

    __tablename__ = "platform_audit_logs"
    __table_args__ = (
        Index("ix_platform_audit_logs_occurred", "occurred_at"),
        Index("ix_platform_audit_logs_target", "target_type", "target_id"),
    )

    # Valeur fournie par l'application (aucun RETURNING nécessaire).
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    # Administrateur TechNova auteur (nul pour la CLI ou une tentative de connexion inconnue).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    # Libellé figé de l'auteur (e-mail, « cli ») : lisible même si le compte change.
    actor_label: Mapped[str] = mapped_column(String(254), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(64))
    # Tenant concerné (opérations sur un tenant, Phase 3.2-G : une entrée miroir est alors
    # aussi écrite dans le journal de ce tenant). Nul pour une action de portée plateforme.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    after: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
