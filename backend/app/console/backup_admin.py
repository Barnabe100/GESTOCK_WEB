"""Sauvegardes et restaurations pilotées depuis la console TechNova (R11, ADR-0058).

La console ne SAUVEGARDE ni ne RESTAURE elle-même : elle enregistre des paramètres, des demandes
et des décisions, que l'agent des sauvegardes (``ops/backup/sm-backup-agent.sh``, conteneur de
la base) exécute. Son rôle SQL n'a aucun accès aux données des entreprises ; les droits de la
migration 0063 limitent ses écritures aux colonnes de paramétrage, aux demandes et aux colonnes
de décision, et un déclencheur n'admet que les transitions qui lui reviennent.

Règles :

- paramètres : une ligne par catégorie (physique, logique, vérification, test de restauration)
  et par fréquence (quotidienne, hebdomadaire, mensuelle, automatique = toutes les N heures,
  manuelle = rétention des sauvegardes à la demande, jamais planifiée) ; fuseau IANA contrôlé ;
  prochaine exécution recalculée par la base (``backup_policy_next_run``), celle que l'agent
  utilise : l'interface affiche exactement ce qui s'exécutera ; chaque modification est
  journalisée avant / après avec sa raison ;
- sauvegarde immédiate : toutes les entreprises ou une seule ; une seule exécution active par
  type et entreprise (``409 backup_already_queued``) ;
- restauration d'UNE entreprise : depuis une sauvegarde logique réussie de CETTE entreprise et
  de la MÊME révision de schéma, ou à un instant (PITR) dans la fenêtre restaurable ;
  vérification sur copie isolée par l'agent, puis approbation par un SECOND administrateur
  (contrainte en base) dans les 24 h ; l'agent fait une sauvegarde préalable avant d'appliquer ;
  retour arrière = nouvelle restauration depuis cette sauvegarde préalable, dans les 24 h, par le
  même circuit ;
- restauration GLOBALE : PITR vérifié et approuvé de même, APPLICATION MANUELLE documentée
  (jamais par l'agent ni par la console) ;
- double audit (plateforme + journal de l'entreprise) pour toute action portant sur une
  entreprise.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sqlalchemy import Select, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_platform_audit, record_tenant_action
from app.console.backup_models import (
    RESTORE_ACTIVE,
    BackupAgentState,
    BackupItem,
    BackupPolicy,
    BackupRun,
    RestoreOperation,
)
from app.core.errors import (
    AppError,
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.platform.audit.service import RequestMeta
from app.platform.tenancy.models import Tenant
from app.shared.pagination import PageParams, apply_sort, paginate

# Champs utilisés par chaque fréquence (les autres sont ignorés et remis à zéro).
FREQUENCY_FIELDS: dict[str, tuple[str, ...]] = {
    "daily": ("run_time",),
    "weekly": ("run_time", "weekday"),
    "monthly": ("run_time", "month_day"),
    "automatic": ("interval_hours", "run_time"),
    "manual": (),
}
COMMON_FIELDS = ("enabled", "start_date", "timezone", "retention_days")
# Âge maximal de la dernière réussite selon la fréquence planifiée la plus serrée (mêmes seuils
# que R11-0 : quotidien 26 h, hebdomadaire 8 jours, mensuel 35 jours).
_GRACE = {"daily": 26 * 3600, "weekly": 192 * 3600, "monthly": 35 * 86400}
AGENT_STALE_SECONDS = 180
DECISION_HOURS = 24


def _level(problem: str | None, critical: bool) -> str:
    if problem is None:
        return "ok"
    return "critical" if critical else "warning"


@dataclass(frozen=True)
class RunFilters:
    kind: str | None = None
    scope: str | None = None
    tenant_id: uuid.UUID | None = None
    status: str | None = None
    trigger: str | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True)
class ItemFilters:
    tenant_id: uuid.UUID | None = None
    status: str | None = None
    source: str | None = None
    trigger: str | None = None
    frequency: str | None = None
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True)
class RestoreFilters:
    tenant_id: uuid.UUID | None = None
    status: str | None = None
    scope: str | None = None
    date_from: date | None = None
    date_to: date | None = None


def _day_bounds(
    day_from: date | None, day_to: date | None
) -> tuple[datetime | None, datetime | None]:
    # Console TechNova : dates en UTC (plateforme, aucune entreprise ne fixe le fuseau).
    lo = datetime.combine(day_from, time.min, UTC) if day_from else None
    hi = datetime.combine(day_to + timedelta(days=1), time.min, UTC) if day_to else None
    return lo, hi


class BackupAdminService:
    def __init__(self, db: Session, now: datetime) -> None:
        self.db = db
        self.now = now

    # --- Lectures communes -----------------------------------------------------------------

    def schema_revision(self) -> str | None:
        return self.db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()

    def tenant_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = self.db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(ids))).all()
        return {r[0]: r[1] for r in rows}

    def _tenant(self, tenant_id: uuid.UUID) -> None:
        # Le rôle de la console ne lit que des colonnes de métadonnées des entreprises.
        if self.db.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) is None:
            raise NotFoundError("Entreprise introuvable", code="tenant_not_found")

    # --- Paramètres ------------------------------------------------------------------------

    def policies(self) -> list[BackupPolicy]:
        return list(
            self.db.scalars(
                select(BackupPolicy).order_by(BackupPolicy.category, BackupPolicy.frequency)
            )
        )

    def policy(self, category: str, frequency: str) -> BackupPolicy:
        found = self.db.scalars(
            select(BackupPolicy).where(
                BackupPolicy.category == category, BackupPolicy.frequency == frequency
            )
        ).one_or_none()
        if found is None:
            raise NotFoundError(
                "Paramètre de sauvegarde introuvable", code="backup_policy_not_found"
            )
        return found

    @staticmethod
    def policy_snapshot(p: BackupPolicy) -> dict[str, Any]:
        return {
            "enabled": p.enabled,
            "run_time": p.run_time.strftime("%H:%M") if p.run_time else None,
            "weekday": p.weekday,
            "month_day": p.month_day,
            "interval_hours": p.interval_hours,
            "start_date": p.start_date.isoformat(),
            "timezone": p.timezone,
            "retention_days": p.retention_days,
            "next_run_at": p.next_run_at.isoformat() if p.next_run_at else None,
        }

    def update_policy(
        self,
        category: str,
        frequency: str,
        values: dict[str, Any],
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> BackupPolicy:
        policy = self.policy(category, frequency)
        tz = values["timezone"]
        try:
            if tz not in available_timezones():
                raise ZoneInfoNotFoundError(tz)
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise BusinessRuleError("Fuseau horaire inconnu", code="invalid_timezone") from exc
        used = FREQUENCY_FIELDS[frequency]
        if frequency == "manual" and values["enabled"]:
            raise BusinessRuleError(
                "La fréquence manuelle n'est jamais planifiée (sauvegardes à la demande)",
                code="manual_policy_not_schedulable",
            )
        for field in used:
            if (field != "run_time" or frequency != "automatic") and values.get(field) is None:
                raise BusinessRuleError(
                    "Champ obligatoire pour cette fréquence",
                    code="policy_field_required",
                    extra={"field": field},
                )
        before = self.policy_snapshot(policy)
        policy.enabled = values["enabled"]
        policy.run_time = values.get("run_time") if "run_time" in used else None
        if policy.run_time is not None:
            policy.run_time = policy.run_time.replace(second=0, microsecond=0, tzinfo=None)
        policy.weekday = values.get("weekday") if "weekday" in used else None
        policy.month_day = values.get("month_day") if "month_day" in used else None
        policy.interval_hours = values.get("interval_hours") if "interval_hours" in used else None
        policy.start_date = values["start_date"]
        policy.timezone = tz
        policy.retention_days = values["retention_days"]
        policy.updated_at = self.now
        policy.updated_by = actor.user_id
        self.db.flush()
        # Prochaine exécution : calculée par la base, celle que l'agent utilisera.
        self.db.execute(
            text(
                "UPDATE backup_policies p SET next_run_at = backup_policy_next_run(p, :now) "
                "WHERE id = :id"
            ),
            {"now": self.now, "id": policy.id},
        )
        self.db.refresh(policy)
        record_platform_audit(
            self.db,
            actor=actor,
            action="backup.policy_updated",
            target_type="backup_policy",
            target_id=f"{category}.{frequency}",
            before=before,
            after=self.policy_snapshot(policy),
            reason=reason,
            meta=meta,
        )
        return policy

    # --- Sauvegardes immédiates --------------------------------------------------------------

    def request_backup(
        self,
        kind: str,
        scope: str,
        tenant_id: uuid.UUID | None,
        reason: str | None,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> BackupRun:
        if scope == "tenant":
            if kind != "logical":
                raise BusinessRuleError(
                    "Seule une sauvegarde logique concerne une entreprise seule",
                    code="tenant_backup_must_be_logical",
                )
            if tenant_id is None:
                raise BusinessRuleError("Entreprise obligatoire", code="tenant_required")
            self._tenant(tenant_id)
        else:
            tenant_id = None
        run = BackupRun(
            id=uuid.uuid4(),
            kind=kind,
            scope=scope,
            tenant_id=tenant_id,
            trigger="manual",
            requested_by=actor.user_id,
            requested_label=actor.label,
            not_before=self.now,
            requested_at=self.now,
        )
        self.db.add(run)
        try:
            with self.db.begin_nested():
                self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Une sauvegarde de ce type est déjà en attente ou en cours",
                code="backup_already_queued",
            ) from exc
        data = {"kind": kind, "scope": scope}
        if tenant_id is not None:
            record_tenant_action(
                self.db,
                actor=actor,
                action="backup.requested",
                tenant_id=tenant_id,
                target_type="backup_run",
                target_id=run.id,
                before={},
                after={"status": "queued"},
                reason=reason or "Sauvegarde à la demande",
                data=data,
                meta=meta,
            )
        else:
            record_platform_audit(
                self.db,
                actor=actor,
                action="backup.requested",
                target_type="backup_run",
                target_id=run.id,
                after={"status": "queued"},
                reason=reason,
                data=data,
                meta=meta,
            )
        return run

    def cancel_run(
        self, run_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> BackupRun:
        run = self.run(run_id, lock=True)
        if run.status != "queued":
            raise ConflictError(
                "Seule une exécution en attente peut être annulée", code="run_not_cancellable"
            )
        run.status = "cancelled"
        run.finished_at = self.now
        run.error = f"annulée : {reason}"[:2000]
        self.db.flush()
        record_platform_audit(
            self.db,
            actor=actor,
            action="backup.run_cancelled",
            target_type="backup_run",
            target_id=run.id,
            before={"status": "queued"},
            after={"status": "cancelled"},
            reason=reason,
            tenant_id=None,
            meta=meta,
        )
        return run

    def run(self, run_id: uuid.UUID, lock: bool = False) -> BackupRun:
        stmt = select(BackupRun).where(BackupRun.id == run_id)
        if lock:
            stmt = stmt.with_for_update()
        run = self.db.scalars(stmt).one_or_none()
        if run is None:
            raise NotFoundError("Exécution introuvable", code="backup_run_not_found")
        return run

    def list_runs(self, filters: RunFilters, params: PageParams) -> tuple[list[BackupRun], int]:
        stmt: Select[Any] = select(BackupRun)
        if filters.kind:
            stmt = stmt.where(BackupRun.kind == filters.kind)
        if filters.scope:
            stmt = stmt.where(BackupRun.scope == filters.scope)
        if filters.tenant_id:
            # Historique d'une entreprise : ses sauvegardes propres ET les sauvegardes globales
            # qui la contiennent.
            stmt = stmt.where(
                (BackupRun.tenant_id == filters.tenant_id)
                | BackupRun.id.in_(
                    select(BackupItem.run_id).where(BackupItem.tenant_id == filters.tenant_id)
                )
            )
        if filters.status:
            stmt = stmt.where(BackupRun.status == filters.status)
        if filters.trigger:
            stmt = stmt.where(BackupRun.trigger == filters.trigger)
        lo, hi = _day_bounds(filters.date_from, filters.date_to)
        if lo:
            stmt = stmt.where(BackupRun.requested_at >= lo)
        if hi:
            stmt = stmt.where(BackupRun.requested_at < hi)
        stmt = apply_sort(
            stmt,
            params.sort,
            {
                "requested_at": BackupRun.requested_at,
                "finished_at": BackupRun.finished_at,
                "kind": BackupRun.kind,
            },
            "-requested_at",
            BackupRun.id,
        )
        return paginate(self.db, stmt, params)

    def run_item_counts(self, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int, int]]:
        if not run_ids:
            return {}
        rows = self.db.execute(
            select(
                BackupItem.run_id,
                func.count(),
                func.count().filter(BackupItem.status.in_(("succeeded", "expired"))),
                func.count().filter(BackupItem.status == "failed"),
            )
            .where(BackupItem.run_id.in_(run_ids))
            .group_by(BackupItem.run_id)
        ).all()
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

    def policy_frequencies(self) -> dict[uuid.UUID, str]:
        return {p.id: p.frequency for p in self.policies()}

    def run_items(self, run_id: uuid.UUID) -> list[BackupItem]:
        return list(
            self.db.scalars(
                select(BackupItem)
                .where(BackupItem.run_id == run_id)
                .order_by(BackupItem.created_at)
            )
        )

    # --- Historique des sauvegardes logiques (sélection d'une restauration) ------------------

    def list_items(self, filters: ItemFilters, params: PageParams) -> tuple[list[BackupItem], int]:
        stmt: Select[Any] = select(BackupItem).join(BackupRun, BackupRun.id == BackupItem.run_id)
        if filters.tenant_id:
            stmt = stmt.where(BackupItem.tenant_id == filters.tenant_id)
        if filters.status:
            stmt = stmt.where(BackupItem.status == filters.status)
        if filters.source:
            stmt = stmt.where(BackupItem.source == filters.source)
        if filters.trigger:
            stmt = stmt.where(BackupRun.trigger == filters.trigger)
        if filters.frequency:
            stmt = stmt.where(
                BackupRun.policy_id.in_(
                    select(BackupPolicy.id).where(BackupPolicy.frequency == filters.frequency)
                )
            )
        lo, hi = _day_bounds(filters.date_from, filters.date_to)
        if lo:
            stmt = stmt.where(BackupItem.created_at >= lo)
        if hi:
            stmt = stmt.where(BackupItem.created_at < hi)
        stmt = apply_sort(
            stmt,
            params.sort,
            {"created_at": BackupItem.created_at, "size_bytes": BackupItem.size_bytes},
            "-created_at",
            BackupItem.id,
        )
        return paginate(self.db, stmt, params)

    def item(self, item_id: uuid.UUID) -> BackupItem:
        found = self.db.get(BackupItem, item_id)
        if found is None:
            raise NotFoundError("Sauvegarde introuvable", code="backup_item_not_found")
        return found

    def restorable(self, item: BackupItem, revision: str | None) -> tuple[bool, str | None]:
        if item.status != "succeeded":
            return False, "not_succeeded"
        if not item.verified:
            return False, "not_verified"
        if item.alembic_revision != revision:
            return False, "schema_revision_differs"
        return True, None

    # --- Restaurations -------------------------------------------------------------------------

    def restore(self, op_id: uuid.UUID, lock: bool = False) -> RestoreOperation:
        stmt = select(RestoreOperation).where(RestoreOperation.id == op_id)
        if lock:
            stmt = stmt.with_for_update()
        found = self.db.scalars(stmt).one_or_none()
        if found is None:
            raise NotFoundError("Restauration introuvable", code="restore_not_found")
        return found

    def list_restores(
        self, filters: RestoreFilters, params: PageParams
    ) -> tuple[list[RestoreOperation], int]:
        stmt: Select[Any] = select(RestoreOperation)
        if filters.tenant_id:
            stmt = stmt.where(RestoreOperation.tenant_id == filters.tenant_id)
        if filters.status:
            stmt = stmt.where(RestoreOperation.status == filters.status)
        if filters.scope:
            stmt = stmt.where(RestoreOperation.scope == filters.scope)
        lo, hi = _day_bounds(filters.date_from, filters.date_to)
        if lo:
            stmt = stmt.where(RestoreOperation.requested_at >= lo)
        if hi:
            stmt = stmt.where(RestoreOperation.requested_at < hi)
        stmt = apply_sort(
            stmt,
            params.sort,
            {"requested_at": RestoreOperation.requested_at},
            "-requested_at",
            RestoreOperation.id,
        )
        return paginate(self.db, stmt, params)

    def actions(self, op: RestoreOperation, user_id: uuid.UUID) -> dict[str, Any]:
        live = op.decision_expires_at is None or op.decision_expires_at > self.now
        blocked = None
        if op.status == "verified":
            if op.requested_by == user_id:
                blocked = "same_admin"
            elif not live:
                blocked = "decision_expired"
        else:
            blocked = "not_verified"
        rollback = (
            op.status == "applied"
            and op.scope == "tenant"
            and op.pre_restore_item_id is not None
            and op.rollback_deadline is not None
            and op.rollback_deadline > self.now
        )
        return {
            "approve": blocked is None,
            "reject": op.status == "verified",
            "cancel": op.status in ("requested", "verified", "verification_failed", "approved"),
            "rollback": rollback,
            "approve_blocked": blocked,
        }

    def _check_window(self, target: datetime, window: dict[str, Any]) -> None:
        if target.tzinfo is None:
            raise BusinessRuleError(
                "Instant sans fuseau : UTC explicite requis", code="target_time_naive"
            )
        if target >= self.now:
            raise BusinessRuleError(
                "L'instant demandé doit être passé", code="target_time_in_future"
            )
        lo, hi = window.get("restorable_from"), window.get("restorable_to")
        if not window.get("known") or lo is None or hi is None:
            raise ConflictError(
                "Fenêtre de restauration inconnue "
                "(sauvegarde physique vérifiée et archivage des WAL requis)",
                code="pitr_window_unknown",
            )
        if not lo <= target <= hi:
            raise BusinessRuleError(
                "Instant hors de la fenêtre restaurable",
                code="target_time_outside_window",
                extra={"restorable_from": lo.isoformat(), "restorable_to": hi.isoformat()},
            )

    def _active_conflict(self, tenant_id: uuid.UUID | None) -> None:
        stmt = (
            select(func.count())
            .select_from(RestoreOperation)
            .where(RestoreOperation.status.in_(RESTORE_ACTIVE))
        )
        stmt = (
            stmt.where(RestoreOperation.tenant_id == tenant_id)
            if tenant_id
            else stmt.where(RestoreOperation.tenant_id.is_(None))
        )
        if self.db.scalar(stmt):
            raise ConflictError(
                "Une restauration est déjà en cours pour cette portée",
                code="restore_already_active",
            )

    def request_restore(
        self,
        *,
        scope: str,
        tenant_id: uuid.UUID | None,
        mode: str,
        source_item_id: uuid.UUID | None,
        target_time: datetime | None,
        reason: str,
        window: dict[str, Any],
        actor: PlatformActor,
        meta: RequestMeta,
        rollback_of: uuid.UUID | None = None,
    ) -> RestoreOperation:
        if actor.user_id is None:
            raise ForbiddenError("Administrateur TechNova requis", code="platform_admin_required")
        if scope == "platform":
            if mode != "pitr":
                raise BusinessRuleError(
                    "Une restauration globale est une restauration à un instant (PITR)",
                    code="platform_restore_must_be_pitr",
                )
            tenant_id, source_item_id = None, None
        else:
            if tenant_id is None:
                raise BusinessRuleError("Entreprise obligatoire", code="tenant_required")
            self._tenant(tenant_id)
        if mode == "logical":
            if source_item_id is None:
                raise BusinessRuleError(
                    "Sauvegarde source obligatoire", code="source_item_required"
                )
            item = self.item(source_item_id)
            if item.tenant_id != tenant_id:
                raise BusinessRuleError(
                    "Sauvegarde d'une autre entreprise : restauration refusée",
                    code="source_item_other_tenant",
                )
            ok, why = self.restorable(item, self.schema_revision())
            if not ok:
                raise BusinessRuleError("Sauvegarde non restaurable", code=f"source_item_{why}")
            target_time = None
        else:
            if target_time is None:
                raise BusinessRuleError("Instant cible obligatoire", code="target_time_required")
            self._check_window(target_time, window)
            target_time = target_time.astimezone(UTC).replace(microsecond=0)
            source_item_id = None
        self._active_conflict(tenant_id)
        op = RestoreOperation(
            id=uuid.uuid4(),
            scope=scope,
            tenant_id=tenant_id,
            mode=mode,
            source_item_id=source_item_id,
            target_time=target_time,
            rollback_of=rollback_of,
            status="requested",
            reason=reason.strip(),
            requested_by=actor.user_id,
            requested_label=actor.label,
            requested_at=self.now,
        )
        self.db.add(op)
        try:
            with self.db.begin_nested():
                self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "Une restauration est déjà en cours pour cette portée",
                code="restore_already_active",
            ) from exc
        self._audit(op, "restore.requested", {}, {"status": "requested"}, reason, actor, meta)
        return op

    def _audit(
        self,
        op: RestoreOperation,
        action: str,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str,
        actor: PlatformActor,
        meta: RequestMeta,
    ) -> None:
        data = {
            "scope": op.scope,
            "mode": op.mode,
            "source_item_id": str(op.source_item_id) if op.source_item_id else None,
            "target_time": op.target_time.isoformat() if op.target_time else None,
            "rollback_of": str(op.rollback_of) if op.rollback_of else None,
        }
        if op.tenant_id is not None:
            record_tenant_action(
                self.db,
                actor=actor,
                action=action,
                tenant_id=op.tenant_id,
                target_type="restore_operation",
                target_id=op.id,
                before=before,
                after=after,
                reason=reason,
                data=data,
                meta=meta,
            )
        else:
            record_platform_audit(
                self.db,
                actor=actor,
                action=action,
                target_type="restore_operation",
                target_id=op.id,
                before=before,
                after=after,
                reason=reason,
                data=data,
                meta=meta,
            )

    def approve(
        self, op_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> RestoreOperation:
        op = self.restore(op_id, lock=True)
        if op.status != "verified":
            raise ConflictError(
                "Seule une restauration vérifiée sur copie isolée peut être approuvée",
                code="restore_not_verified",
            )
        if op.decision_expires_at is not None and op.decision_expires_at <= self.now:
            raise ConflictError(
                "Décision expirée : refaire la demande", code="restore_decision_expired"
            )
        if actor.user_id is None or actor.user_id == op.requested_by:
            raise ForbiddenError(
                "L'approbation exige un SECOND administrateur, distinct du demandeur",
                code="restore_same_admin",
            )
        op.status = "approved"
        op.approved_by = actor.user_id
        op.approved_label = actor.label
        op.approved_at = self.now
        op.approval_reason = reason.strip()
        self.db.flush()
        self._audit(
            op,
            "restore.approved",
            {"status": "verified"},
            {"status": "approved"},
            reason,
            actor,
            meta,
        )
        return op

    def reject(
        self, op_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> RestoreOperation:
        op = self.restore(op_id, lock=True)
        if op.status != "verified":
            raise ConflictError(
                "Seule une restauration vérifiée peut être refusée", code="restore_not_verified"
            )
        op.status = "rejected"
        op.finished_at = self.now
        op.error = f"refusée : {reason}"[:2000]
        self.db.flush()
        self._audit(
            op,
            "restore.rejected",
            {"status": "verified"},
            {"status": "rejected"},
            reason,
            actor,
            meta,
        )
        return op

    def cancel(
        self, op_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> RestoreOperation:
        op = self.restore(op_id, lock=True)
        if op.status not in ("requested", "verified", "verification_failed", "approved"):
            raise ConflictError(
                "Restauration en cours d'exécution ou terminée : annulation impossible",
                code="restore_not_cancellable",
            )
        before = op.status
        op.status = "cancelled"
        op.finished_at = self.now
        op.error = f"annulée : {reason}"[:2000]
        self.db.flush()
        self._audit(
            op,
            "restore.cancelled",
            {"status": before},
            {"status": "cancelled"},
            reason,
            actor,
            meta,
        )
        return op

    def rollback(
        self, op_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> RestoreOperation:
        op = self.restore(op_id, lock=True)
        if not self.actions(op, actor.user_id or uuid.UUID(int=0))["rollback"]:
            raise ConflictError(
                "Retour arrière impossible (restauration non appliquée ou délai de 24 h dépassé)",
                code="rollback_unavailable",
            )
        assert op.pre_restore_item_id is not None
        return self.request_restore(
            scope="tenant",
            tenant_id=op.tenant_id,
            mode="logical",
            source_item_id=op.pre_restore_item_id,
            target_time=None,
            reason=reason,
            window={},
            actor=actor,
            meta=meta,
            rollback_of=op.id,
        )

    # --- Supervision --------------------------------------------------------------------------

    def agent(self) -> dict[str, Any]:
        state = self.db.get(BackupAgentState, 1)
        if state is None:
            return {
                "last_seen_at": None,
                "age_seconds": None,
                "agent": None,
                "last_error": None,
                "level": "critical",
                "problem": "never_seen",
            }
        age = int((self.now - state.last_seen_at).total_seconds())
        problem = "stale" if age > AGENT_STALE_SECONDS else None
        return {
            "last_seen_at": state.last_seen_at,
            "age_seconds": max(age, 0),
            "agent": state.agent,
            "last_error": state.last_error,
            "level": _level(problem, True)
            if problem
            else ("warning" if state.last_error else "ok"),
            "problem": problem,
        }

    def schedules(self) -> list[dict[str, Any]]:
        out = []
        policies = self.policies()
        for category in ("physical", "logical", "verification", "restore_test"):
            enabled = [p for p in policies if p.category == category and p.enabled]
            nexts = [p.next_run_at for p in enabled if p.next_run_at]
            ages = [
                ((p.interval_hours or 24) * 3600 + 2 * 3600)
                if p.frequency == "automatic"
                else _GRACE[p.frequency]
                for p in enabled
                if p.frequency != "manual"
            ]
            max_age = min(ages) if ages else None
            last_ok = self.db.scalar(
                select(func.max(BackupRun.finished_at)).where(
                    BackupRun.kind == category,
                    BackupRun.status == "succeeded",
                    BackupRun.trigger.in_(("schedule", "manual", "retry")),
                )
            )
            last = self.db.scalars(
                select(BackupRun)
                .where(
                    BackupRun.kind == category,
                    BackupRun.trigger.in_(("schedule", "manual", "retry")),
                )
                .order_by(BackupRun.requested_at.desc())
                .limit(1)
            ).one_or_none()
            pending_retry = self.db.scalar(
                select(func.count())
                .select_from(BackupRun)
                .where(BackupRun.kind == category, BackupRun.status.in_(("queued", "running")))
            )
            problem, critical = None, False
            if not enabled:
                problem = "not_scheduled"
                critical = False
            elif last_ok is None:
                oldest = min(p.updated_at for p in enabled)
                if max_age is not None and (self.now - oldest).total_seconds() > max_age:
                    problem, critical = "never_succeeded", True
            elif max_age is not None and (self.now - last_ok).total_seconds() > max_age:
                problem, critical = "too_old", True
            if (
                problem is None
                and last is not None
                and last.status == "failed"
                and not pending_retry
            ):
                problem = "last_run_failed"
            out.append(
                {
                    "category": category,
                    "enabled_frequencies": [p.frequency for p in enabled],
                    "next_run_at": min(nexts) if nexts else None,
                    "last_success_at": last_ok,
                    "last_status": last.status if last else None,
                    "max_age_seconds": max_age,
                    "problem": problem,
                    "level": _level(problem, critical),
                }
            )
        return out

    def active_runs(self) -> list[BackupRun]:
        return list(
            self.db.scalars(
                select(BackupRun)
                .where(BackupRun.status.in_(("queued", "running")))
                .order_by(BackupRun.not_before)
            )
        )

    def active_restores(self) -> list[RestoreOperation]:
        return list(
            self.db.scalars(
                select(RestoreOperation)
                .where(RestoreOperation.status.in_(RESTORE_ACTIVE))
                .order_by(RestoreOperation.requested_at)
            )
        )


def window_from_status(status: dict[str, Any]) -> dict[str, Any]:
    """Fenêtre de restauration à un instant : de la fin de la plus ancienne sauvegarde physique
    vérifiée au dernier WAL archivé (statuts de R11-0)."""
    lo = (status.get("storage") or {}).get("restorable_from")
    hi = (status.get("archiving") or {}).get("last_archived_at")
    known = isinstance(lo, datetime) and isinstance(hi, datetime) and lo <= hi
    return {
        "known": known,
        "restorable_from": lo if known else None,
        "restorable_to": hi if known else None,
    }


def worst_level(*levels: str) -> str:
    rank = {"ok": 0, "warning": 1, "critical": 2}
    return max(levels, key=lambda lvl: rank[lvl]) if levels else "ok"


def ensure_reason(reason: str | None) -> str:
    if reason is None or len(reason.strip()) < 5:
        raise AppError("Raison obligatoire (5 caractères au moins)", code="reason_required")
    return reason.strip()
