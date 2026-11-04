"""Routes « Sauvegardes » de la console TechNova : Sauvegarder, Restaurer, Paramètres (R11,
ADR-0058). Toutes exigent un administrateur de la plateforme ; aucune n'accède au contenu d'une
sauvegarde ni aux données d'une entreprise : l'agent exécute, la console décide."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.console.auth import ConsoleDb, ConsoleSettings, NowDep, PlatformAdmin
from app.console.backup_admin import (
    FREQUENCY_FIELDS,
    BackupAdminService,
    ItemFilters,
    RestoreFilters,
    RunFilters,
    window_from_status,
    worst_level,
)
from app.console.backup_models import BackupItem, BackupPolicy, BackupRun, RestoreOperation
from app.console.backup_schemas import (
    AgentOut,
    BackupOverviewOut,
    BackupRequestIn,
    Category,
    Frequency,
    PolicyOut,
    PolicyUpdateIn,
    ReasonIn,
    RestorableWindowOut,
    RestoreActions,
    RestoreCreateIn,
    RestoreDetailOut,
    RestoreOut,
    RunDetailOut,
    RunItemOut,
    RunOut,
    ScheduleSupervisionOut,
)
from app.console.backups import backup_status, backup_thresholds, status_directory
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(prefix="/operations/backups", tags=["console-backups"])


def _window(db: ConsoleDb, settings: ConsoleSettings, now: NowDep) -> dict[str, object]:
    status_ = backup_status(
        db,
        status_directory(settings.backup_status_dir, settings.backup_dir),
        now,
        backup_thresholds(settings),
    )
    return window_from_status(status_)


def _policy_out(p: BackupPolicy) -> PolicyOut:
    return PolicyOut(
        id=p.id,
        category=p.category,
        frequency=p.frequency,
        enabled=p.enabled,
        run_time=p.run_time,
        weekday=p.weekday,
        month_day=p.month_day,
        interval_hours=p.interval_hours,
        start_date=p.start_date,
        timezone=p.timezone,
        retention_days=p.retention_days,
        next_run_at=p.next_run_at,
        last_enqueued_at=p.last_enqueued_at,
        updated_at=p.updated_at,
        fields=list(FREQUENCY_FIELDS[p.frequency]),
        schedulable=p.frequency != "manual",
    )


def _runs_out(service: BackupAdminService, runs: list[BackupRun]) -> list[RunOut]:
    names = service.tenant_names({r.tenant_id for r in runs if r.tenant_id})
    counts = service.run_item_counts([r.id for r in runs])
    freqs = service.policy_frequencies()
    out = []
    for r in runs:
        total, ok, failed = counts.get(r.id, (0, 0, 0))
        out.append(
            RunOut(
                id=r.id,
                kind=r.kind,
                scope=r.scope,
                tenant_id=r.tenant_id,
                tenant_name=names.get(r.tenant_id) if r.tenant_id else None,
                trigger=r.trigger,
                frequency=freqs.get(r.policy_id) if r.policy_id else None,
                status=r.status,
                attempt=r.attempt,
                max_attempts=r.max_attempts,
                retry_of=r.retry_of,
                scheduled_for=r.scheduled_for,
                not_before=r.not_before,
                requested_label=r.requested_label,
                requested_at=r.requested_at,
                started_at=r.started_at,
                finished_at=r.finished_at,
                heartbeat_at=r.heartbeat_at,
                progress_percent=r.progress_percent,
                progress_message=r.progress_message,
                error=r.error,
                restore_operation_id=r.restore_operation_id,
                items_total=total,
                items_succeeded=ok,
                items_failed=failed,
                can_cancel=r.status == "queued",
            )
        )
    return out


def _items_out(service: BackupAdminService, items: list[BackupItem]) -> list[RunItemOut]:
    names = service.tenant_names({i.tenant_id for i in items})
    runs = {r.id: r for r in (service.run(rid) for rid in {i.run_id for i in items})}
    freqs = service.policy_frequencies()
    revision = service.schema_revision()
    out = []
    for i in items:
        run = runs[i.run_id]
        ok, why = service.restorable(i, revision)
        out.append(
            RunItemOut(
                id=i.id,
                tenant_id=i.tenant_id,
                tenant_name=names.get(i.tenant_id),
                status=i.status,
                source=i.source,
                source_point=i.source_point,
                table_count=i.table_count,
                row_count=i.row_count,
                size_bytes=i.size_bytes,
                manifest_sha256=i.manifest_sha256,
                alembic_revision=i.alembic_revision,
                verified=i.verified,
                verified_at=i.verified_at,
                expires_at=i.expires_at,
                protected_until=i.protected_until,
                error=i.error,
                created_at=i.created_at,
                finished_at=i.finished_at,
                run_id=i.run_id,
                run_trigger=run.trigger,
                run_frequency=freqs.get(run.policy_id) if run.policy_id else None,
                restorable=ok,
                restorable_reason=why,
            )
        )
    return out


def _restore_out(
    service: BackupAdminService, op: RestoreOperation, user_id: uuid.UUID
) -> RestoreOut:
    names = service.tenant_names({op.tenant_id} if op.tenant_id else set())
    return RestoreOut(
        id=op.id,
        scope=op.scope,
        tenant_id=op.tenant_id,
        tenant_name=names.get(op.tenant_id) if op.tenant_id else None,
        mode=op.mode,
        source_item_id=op.source_item_id,
        target_time=op.target_time,
        rollback_of=op.rollback_of,
        status=op.status,
        reason=op.reason,
        requested_label=op.requested_label,
        requested_at=op.requested_at,
        verified_at=op.verified_at,
        approved_label=op.approved_label,
        approved_at=op.approved_at,
        approval_reason=op.approval_reason,
        decision_expires_at=op.decision_expires_at,
        applied_at=op.applied_at,
        rollback_deadline=op.rollback_deadline,
        pre_restore_item_id=op.pre_restore_item_id,
        materialized_item_id=op.materialized_item_id,
        progress_percent=op.progress_percent,
        progress_message=op.progress_message,
        finished_at=op.finished_at,
        error=op.error,
        manual_application=op.scope == "platform",
        actions=RestoreActions(**service.actions(op, user_id)),
    )


def _restore_detail(
    service: BackupAdminService, op: RestoreOperation, user_id: uuid.UUID
) -> RestoreDetailOut:
    base = _restore_out(service, op, user_id)
    item = service.item(op.source_item_id) if op.source_item_id else None
    return RestoreDetailOut(
        **base.model_dump(),
        verification=op.verification or {},
        source_item=_items_out(service, [item])[0] if item else None,
    )


# --- Vue d'ensemble -------------------------------------------------------------------------------


@router.get("/overview", response_model=BackupOverviewOut)
def overview(
    ctx: PlatformAdmin, db: ConsoleDb, settings: ConsoleSettings, now: NowDep
) -> BackupOverviewOut:
    """Agent, planification (prochaine exécution, dernière réussite, retard), exécutions et
    restaurations en cours, fenêtre de restauration à un instant."""
    service = BackupAdminService(db, now)
    agent = service.agent()
    schedules = service.schedules()
    window = _window(db, settings, now)
    return BackupOverviewOut(
        checked_at=now,
        level=worst_level(agent["level"], *(s["level"] for s in schedules)),
        schema_revision=service.schema_revision(),
        agent=AgentOut(**agent),
        schedules=[ScheduleSupervisionOut(**s) for s in schedules],
        active_runs=_runs_out(service, service.active_runs()),
        active_restores=[
            _restore_out(service, op, ctx.user.id) for op in service.active_restores()
        ],
        window=RestorableWindowOut(**window),
    )


# --- Paramètres -----------------------------------------------------------------------------------


@router.get("/policies", response_model=list[PolicyOut])
def list_policies(ctx: PlatformAdmin, db: ConsoleDb, now: NowDep) -> list[PolicyOut]:
    return [_policy_out(p) for p in BackupAdminService(db, now).policies()]


@router.put("/policies/{category}/{frequency}", response_model=PolicyOut)
def update_policy(
    category: Category,
    frequency: Frequency,
    body: PolicyUpdateIn,
    ctx: PlatformAdmin,
    db: ConsoleDb,
    now: NowDep,
) -> PolicyOut:
    """Enregistre un paramètre ; la prochaine exécution est recalculée par la base et l'agent
    l'applique dès son prochain passage (chaque minute)."""
    service = BackupAdminService(db, now)
    values = body.model_dump(exclude={"reason"})
    policy = service.update_policy(category, frequency, values, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _policy_out(policy)


# --- Sauvegarder ----------------------------------------------------------------------------------


@router.post("/runs", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def request_backup(body: BackupRequestIn, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep) -> RunOut:
    """Sauvegarde immédiate (toutes les entreprises ou une seule) ; suivie par
    ``GET /runs/{id}``."""
    service = BackupAdminService(db, now)
    run = service.request_backup(
        body.kind, body.scope, body.tenant_id, body.reason, ctx.actor, ctx.meta
    )
    db.commit()
    return _runs_out(service, [run])[0]


@router.get("/runs", response_model=Page[RunOut])
def list_runs(
    ctx: PlatformAdmin,
    db: ConsoleDb,
    now: NowDep,
    params: Annotated[PageParams, Depends(page_params)],
    kind: Category | None = None,
    scope: str | None = Query(default=None, pattern="^(platform|tenant)$"),
    tenant_id: uuid.UUID | None = None,
    status_: Annotated[
        str | None, Query(alias="status", pattern="^(queued|running|succeeded|failed|cancelled)$")
    ] = None,
    trigger: str | None = Query(
        default=None, pattern="^(schedule|manual|retry|pre_restore|restore_source)$"
    ),
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[RunOut]:
    service = BackupAdminService(db, now)
    rows, total = service.list_runs(
        RunFilters(kind, scope, tenant_id, status_, trigger, date_from, date_to), params
    )
    return Page(
        items=_runs_out(service, rows), total=total, limit=params.limit, offset=params.offset
    )


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(run_id: uuid.UUID, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep) -> RunDetailOut:
    service = BackupAdminService(db, now)
    run = service.run(run_id)
    base = _runs_out(service, [run])[0]
    return RunDetailOut(
        **base.model_dump(),
        result=run.result or {},
        items=_items_out(service, service.run_items(run.id)),
    )


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(
    run_id: uuid.UUID, body: ReasonIn, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep
) -> RunOut:
    service = BackupAdminService(db, now)
    run = service.cancel_run(run_id, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _runs_out(service, [run])[0]


# --- Restaurer ------------------------------------------------------------------------------------


@router.get("/items", response_model=Page[RunItemOut])
def list_items(
    ctx: PlatformAdmin,
    db: ConsoleDb,
    now: NowDep,
    params: Annotated[PageParams, Depends(page_params)],
    tenant_id: uuid.UUID | None = None,
    status_: Annotated[
        str | None, Query(alias="status", pattern="^(running|succeeded|failed|expired)$")
    ] = None,
    source: str | None = Query(default=None, pattern="^(live|pitr)$"),
    trigger: str | None = Query(
        default=None, pattern="^(schedule|manual|retry|pre_restore|restore_source)$"
    ),
    frequency: Frequency | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[RunItemOut]:
    """Historique des sauvegardes LOGIQUES (par entreprise ou toutes) : sélection d'une
    restauration (entreprise, date, période, type, statut)."""
    service = BackupAdminService(db, now)
    rows, total = service.list_items(
        ItemFilters(tenant_id, status_, source, trigger, frequency, date_from, date_to), params
    )
    return Page(
        items=_items_out(service, rows), total=total, limit=params.limit, offset=params.offset
    )


@router.get("/restores", response_model=Page[RestoreOut])
def list_restores(
    ctx: PlatformAdmin,
    db: ConsoleDb,
    now: NowDep,
    params: Annotated[PageParams, Depends(page_params)],
    tenant_id: uuid.UUID | None = None,
    status_: Annotated[str | None, Query(alias="status", max_length=30)] = None,
    scope: str | None = Query(default=None, pattern="^(platform|tenant)$"),
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[RestoreOut]:
    service = BackupAdminService(db, now)
    rows, total = service.list_restores(
        RestoreFilters(tenant_id, status_, scope, date_from, date_to), params
    )
    return Page(
        items=[_restore_out(service, op, ctx.user.id) for op in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post("/restores", response_model=RestoreDetailOut, status_code=status.HTTP_201_CREATED)
def request_restore(
    body: RestoreCreateIn, ctx: PlatformAdmin, db: ConsoleDb, settings: ConsoleSettings, now: NowDep
) -> RestoreDetailOut:
    """Demande de restauration : vérifiée par l'agent sur une copie isolée, puis soumise à
    l'approbation d'un second administrateur. Jamais appliquée directement."""
    service = BackupAdminService(db, now)
    window = _window(db, settings, now) if body.mode == "pitr" else {}
    op = service.request_restore(
        scope=body.scope,
        tenant_id=body.tenant_id,
        mode=body.mode,
        source_item_id=body.source_item_id,
        target_time=body.target_time,
        reason=body.reason,
        window=window,
        actor=ctx.actor,
        meta=ctx.meta,
    )
    db.commit()
    return _restore_detail(service, op, ctx.user.id)


@router.get("/restores/{op_id}", response_model=RestoreDetailOut)
def get_restore(
    op_id: uuid.UUID, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep
) -> RestoreDetailOut:
    service = BackupAdminService(db, now)
    return _restore_detail(service, service.restore(op_id), ctx.user.id)


@router.post("/restores/{op_id}/approve", response_model=RestoreDetailOut)
def approve_restore(
    op_id: uuid.UUID, body: ReasonIn, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep
) -> RestoreDetailOut:
    service = BackupAdminService(db, now)
    op = service.approve(op_id, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _restore_detail(service, op, ctx.user.id)


@router.post("/restores/{op_id}/reject", response_model=RestoreDetailOut)
def reject_restore(
    op_id: uuid.UUID, body: ReasonIn, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep
) -> RestoreDetailOut:
    service = BackupAdminService(db, now)
    op = service.reject(op_id, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _restore_detail(service, op, ctx.user.id)


@router.post("/restores/{op_id}/cancel", response_model=RestoreDetailOut)
def cancel_restore(
    op_id: uuid.UUID, body: ReasonIn, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep
) -> RestoreDetailOut:
    service = BackupAdminService(db, now)
    op = service.cancel(op_id, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _restore_detail(service, op, ctx.user.id)


@router.post(
    "/restores/{op_id}/rollback",
    response_model=RestoreDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def rollback_restore(
    op_id: uuid.UUID, body: ReasonIn, ctx: PlatformAdmin, db: ConsoleDb, now: NowDep
) -> RestoreDetailOut:
    """Retour arrière (24 h) : nouvelle restauration depuis la sauvegarde préalable, soumise à la
    même vérification et à la même double approbation."""
    service = BackupAdminService(db, now)
    op = service.rollback(op_id, body.reason, ctx.actor, ctx.meta)
    db.commit()
    return _restore_detail(service, op, ctx.user.id)
