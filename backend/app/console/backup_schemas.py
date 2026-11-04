"""Schémas de l'API des sauvegardes de la console (R11, ADR-0058)."""

import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["physical", "logical", "verification", "restore_test"]
Frequency = Literal["daily", "weekly", "monthly", "automatic", "manual"]
Level = Literal["ok", "warning", "critical"]


class PolicyOut(BaseModel):
    id: uuid.UUID
    category: Category
    frequency: Frequency
    enabled: bool
    run_time: time | None
    weekday: int | None
    month_day: int | None
    interval_hours: int | None
    start_date: date
    timezone: str
    retention_days: int
    next_run_at: datetime | None
    last_enqueued_at: datetime | None
    updated_at: datetime
    # Champs que cette fréquence utilise (l'interface n'affiche que ceux-là).
    fields: list[str]
    schedulable: bool


class PolicyUpdateIn(BaseModel):
    enabled: bool
    run_time: time | None = None
    weekday: int | None = Field(default=None, ge=1, le=7)
    month_day: int | None = Field(default=None, ge=1, le=31)
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    start_date: date
    timezone: str = Field(min_length=1, max_length=64)
    retention_days: int = Field(ge=1, le=3650)
    reason: str = Field(min_length=5, max_length=500)


class BackupRequestIn(BaseModel):
    kind: Category
    scope: Literal["platform", "tenant"] = "platform"
    tenant_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


class RunItemOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    tenant_name: str | None
    status: str
    source: str
    source_point: datetime | None
    table_count: int | None
    row_count: int | None
    size_bytes: int | None
    manifest_sha256: str | None
    alembic_revision: str | None
    verified: bool
    verified_at: datetime | None
    expires_at: datetime | None
    protected_until: datetime | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    # Contexte de l'exécution (filtres « type » de l'historique).
    run_id: uuid.UUID
    run_trigger: str
    run_frequency: str | None
    restorable: bool
    restorable_reason: str | None


class RunOut(BaseModel):
    id: uuid.UUID
    kind: Category
    scope: Literal["platform", "tenant"]
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    trigger: str
    frequency: str | None
    status: str
    attempt: int
    max_attempts: int
    retry_of: uuid.UUID | None
    scheduled_for: datetime | None
    not_before: datetime
    requested_label: str
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    progress_percent: int
    progress_message: str | None
    error: str | None
    restore_operation_id: uuid.UUID | None
    items_total: int
    items_succeeded: int
    items_failed: int
    can_cancel: bool


class RunDetailOut(RunOut):
    result: dict[str, Any]
    items: list[RunItemOut]


class RestoreCreateIn(BaseModel):
    scope: Literal["tenant", "platform"] = "tenant"
    tenant_id: uuid.UUID | None = None
    mode: Literal["logical", "pitr"]
    source_item_id: uuid.UUID | None = None
    target_time: datetime | None = None
    reason: str = Field(min_length=5, max_length=1000)


class RestoreActions(BaseModel):
    approve: bool
    reject: bool
    cancel: bool
    rollback: bool
    # Raison d'une approbation impossible pour CET administrateur (ex. demandeur).
    approve_blocked: str | None


class RestoreOut(BaseModel):
    id: uuid.UUID
    scope: Literal["tenant", "platform"]
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    mode: Literal["logical", "pitr"]
    source_item_id: uuid.UUID | None
    target_time: datetime | None
    rollback_of: uuid.UUID | None
    status: str
    reason: str
    requested_label: str
    requested_at: datetime
    verified_at: datetime | None
    approved_label: str | None
    approved_at: datetime | None
    approval_reason: str | None
    decision_expires_at: datetime | None
    applied_at: datetime | None
    rollback_deadline: datetime | None
    pre_restore_item_id: uuid.UUID | None
    materialized_item_id: uuid.UUID | None
    progress_percent: int
    progress_message: str | None
    finished_at: datetime | None
    error: str | None
    manual_application: bool
    actions: RestoreActions


class RestoreDetailOut(RestoreOut):
    verification: dict[str, Any]
    source_item: RunItemOut | None


class ScheduleSupervisionOut(BaseModel):
    category: Category
    enabled_frequencies: list[str]
    next_run_at: datetime | None
    last_success_at: datetime | None
    last_status: str | None
    max_age_seconds: int | None
    problem: str | None  # not_scheduled | never_succeeded | too_old | last_run_failed
    level: Level


class AgentOut(BaseModel):
    last_seen_at: datetime | None
    age_seconds: int | None
    agent: str | None
    last_error: str | None
    level: Level
    problem: str | None  # never_seen | stale


class RestorableWindowOut(BaseModel):
    known: bool
    restorable_from: datetime | None
    restorable_to: datetime | None


class BackupOverviewOut(BaseModel):
    checked_at: datetime
    level: Level
    schema_revision: str | None
    agent: AgentOut
    schedules: list[ScheduleSupervisionOut]
    active_runs: list[RunOut]
    active_restores: list[RestoreOut]
    window: RestorableWindowOut
