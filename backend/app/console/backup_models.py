"""Sauvegardes pilotées par la console TechNova (R11, ADR-0058) — tables de la PLATEFORME.

Aucune donnée d'entreprise ici : des politiques (planification), des exécutions (file d'attente
et historique), des éléments (une sauvegarde logique d'UNE entreprise : métadonnées seulement, le
contenu reste sur le disque des sauvegardes) et des opérations de restauration (machine à états,
double approbation). Droits SQL (migration 0063) :

- rôle de la console : lecture, demandes, approbations — jamais d'exécution ;
- rôle de l'agent de sauvegarde (``stockmanager_backup``) : prise en charge et compte rendu ;
- rôle applicatif des entreprises : AUCUN accès.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin

POLICY_CATEGORIES = ("physical", "logical", "verification", "restore_test")
FREQUENCIES = ("daily", "weekly", "monthly", "automatic", "manual")
RUN_KINDS = POLICY_CATEGORIES
RUN_TRIGGERS = ("schedule", "manual", "retry", "pre_restore", "restore_source")
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
ITEM_STATUSES = ("running", "succeeded", "failed", "expired")
RESTORE_STATUSES = (
    "requested",
    "verifying",
    "verified",
    "verification_failed",
    "approved",
    "applying",
    "applied",
    "apply_failed",
    "rejected",
    "cancelled",
    "expired",
)
RESTORE_ACTIVE = ("requested", "verifying", "verified", "approved", "applying")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class BackupPolicy(IdMixin, Base):
    """Une politique = une catégorie × une fréquence (au plus une ligne par couple).

    Catégories : ``physical`` (cluster complet + WAL : PITR de la plateforme), ``logical`` (une
    copie par entreprise : restauration individuelle), tâches de contrôle ``verification`` et
    ``restore_test``. ``next_run_at`` est calculé par la base (``backup_policy_next_run``), seule
    source de vérité du planificateur."""

    __tablename__ = "backup_policies"
    __table_args__ = (
        UniqueConstraint("category", "frequency", name="uq_backup_policies_category_frequency"),
        CheckConstraint(_in("category", POLICY_CATEGORIES), name="category_valid"),
        CheckConstraint(_in("frequency", FREQUENCIES), name="frequency_valid"),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 1 AND 7", name="weekday_valid"),
        CheckConstraint("month_day IS NULL OR month_day BETWEEN 1 AND 31", name="month_day_valid"),
        CheckConstraint(
            "interval_hours IS NULL OR interval_hours BETWEEN 1 AND 168", name="interval_valid"
        ),
        CheckConstraint("retention_days BETWEEN 1 AND 3650", name="retention_valid"),
        CheckConstraint(
            "frequency NOT IN ('daily', 'weekly', 'monthly') OR run_time IS NOT NULL",
            name="time_required",
        ),
        CheckConstraint("frequency <> 'weekly' OR weekday IS NOT NULL", name="weekday_required"),
        CheckConstraint(
            "frequency <> 'monthly' OR month_day IS NOT NULL", name="month_day_required"
        ),
        CheckConstraint(
            "frequency <> 'automatic' OR interval_hours IS NOT NULL", name="interval_required"
        ),
        CheckConstraint("frequency <> 'manual' OR NOT enabled", name="manual_not_scheduled"),
    )

    category: Mapped[str] = mapped_column(String(20), nullable=False)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    run_time: Mapped[time | None] = mapped_column(Time)
    weekday: Mapped[int | None] = mapped_column(SmallInteger)  # ISO : 1 = lundi … 7 = dimanche
    month_day: Mapped[int | None] = mapped_column(SmallInteger)  # 29-31 : dernier jour si absent
    interval_hours: Mapped[int | None] = mapped_column(SmallInteger)
    start_date: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Sans clé étrangère : les paramètres de la plateforme survivent à tout compte (trace
    # complète, avant / après, dans platform_audit_logs).
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class BackupRun(IdMixin, Base):
    """Une exécution (file d'attente + historique). Une seule exécution active (en attente ou en
    cours) par type, portée et entreprise : index unique partiel ``uq_backup_runs_active``."""

    __tablename__ = "backup_runs"
    __table_args__ = (
        UniqueConstraint("policy_id", "scheduled_for", name="uq_backup_runs_occurrence"),
        CheckConstraint(_in("kind", RUN_KINDS), name="kind_valid"),
        CheckConstraint("scope IN ('platform', 'tenant')", name="scope_valid"),
        CheckConstraint(_in("trigger", RUN_TRIGGERS), name="trigger_valid"),
        CheckConstraint(_in("status", RUN_STATUSES), name="status_valid"),
        CheckConstraint("(scope = 'tenant') = (tenant_id IS NOT NULL)", name="scope_tenant"),
        CheckConstraint("kind = 'logical' OR scope = 'platform'", name="tenant_scope_logical"),
        CheckConstraint("progress_percent BETWEEN 0 AND 100", name="progress_valid"),
        CheckConstraint("attempt BETWEEN 1 AND max_attempts", name="attempt_valid"),
        Index("ix_backup_runs_queue", "status", "not_before"),
        Index("ix_backup_runs_tenant", "tenant_id", "requested_at"),
        Index("ix_backup_runs_requested", "requested_at"),
        Index(
            "uq_backup_runs_active",
            "kind",
            "scope",
            text("coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_policies.id", ondelete="RESTRICT")
    )
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    max_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    retry_of: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_runs.id", ondelete="RESTRICT")
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    requested_label: Mapped[str] = mapped_column(String(254), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    progress_message: Mapped[str | None] = mapped_column(String(300))
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    restore_operation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("restore_operations.id", ondelete="RESTRICT", use_alter=True)
    )


class BackupItem(IdMixin, Base):
    """Sauvegarde logique d'UNE entreprise (métadonnées ; contenu sur le disque des sauvegardes,
    ``logical/<entreprise>/<répertoire>``). ``protected_until`` : jamais supprimée avant (fenêtre
    de retour arrière d'une restauration, sauvegarde préalable)."""

    __tablename__ = "backup_items"
    __table_args__ = (
        UniqueConstraint("run_id", "tenant_id", name="uq_backup_items_run_tenant"),
        CheckConstraint(_in("status", ITEM_STATUSES), name="status_valid"),
        CheckConstraint("source IN ('live', 'pitr')", name="source_valid"),
        Index("ix_backup_items_tenant", "tenant_id", "created_at"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("backup_runs.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    source: Mapped[str] = mapped_column(String(10), nullable=False, server_default="live")
    source_point: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_ref: Mapped[str | None] = mapped_column(String(300))
    table_count: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    alembic_revision: Mapped[str | None] = mapped_column(String(32))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    protected_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RestoreOperation(IdMixin, Base):
    """Restauration : demandée → vérifiée sur une copie temporaire isolée → approuvée par un
    SECOND administrateur (≠ demandeur, contrainte en base) → appliquée (sauvegarde préalable,
    une transaction) → retour arrière possible pendant 24 h. Transitions contrôlées par un
    déclencheur ; une seule opération active par entreprise."""

    __tablename__ = "restore_operations"
    __table_args__ = (
        CheckConstraint("scope IN ('tenant', 'platform')", name="scope_valid"),
        CheckConstraint("mode IN ('logical', 'pitr')", name="mode_valid"),
        CheckConstraint(_in("status", RESTORE_STATUSES), name="status_valid"),
        CheckConstraint("(scope = 'tenant') = (tenant_id IS NOT NULL)", name="scope_tenant"),
        CheckConstraint("scope = 'tenant' OR mode = 'pitr'", name="platform_is_pitr"),
        CheckConstraint(
            "(mode = 'logical') = (source_item_id IS NOT NULL)", name="logical_has_item"
        ),
        CheckConstraint("(mode = 'pitr') = (target_time IS NOT NULL)", name="pitr_has_time"),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by", name="two_distinct_admins"
        ),
        CheckConstraint("length(btrim(reason)) >= 5", name="reason_required"),
        Index("ix_restore_operations_tenant", "tenant_id", "requested_at"),
        Index(
            "uq_restore_operations_active",
            text("coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text(
                "status IN ('requested', 'verifying', 'verified', 'approved', 'applying')"
            ),
        ),
    )

    scope: Mapped[str] = mapped_column(String(10), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_items.id", ondelete="RESTRICT")
    )
    target_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_of: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("restore_operations.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="requested")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    requested_label: Mapped[str] = mapped_column(String(254), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_label: Mapped[str | None] = mapped_column(String(254))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reason: Mapped[str | None] = mapped_column(Text)
    decision_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pre_restore_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_items.id", ondelete="RESTRICT")
    )
    materialized_item_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_items.id", ondelete="RESTRICT")
    )
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    progress_message: Mapped[str | None] = mapped_column(String(300))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class BackupAgentState(Base):
    """Dernier passage de l'agent de sauvegarde (une ligne) : supervision « agent muet »."""

    __tablename__ = "backup_agent_state"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    agent: Mapped[str] = mapped_column(String(100), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
