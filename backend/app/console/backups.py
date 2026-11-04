"""État des sauvegardes dans la console TechNova (lecture seule, R11-0, ADR-0057).

Sources, sans aucun accès au CONTENU des sauvegardes :

- archivage continu des WAL, lu en direct : ``pg_stat_archiver``, ``pg_settings`` et le segment
  courant (``pg_walfile_name(pg_current_wal_lsn())``) — lisibles sans privilège : aucun droit
  SQL ajouté au rôle de la console ;
- statuts écrits par les scripts d'exploitation (``ops/backup``) dans le SEUL répertoire
  ``status/`` monté en lecture seule (``SM_BACKUP_STATUS_DIR``) : noms de fichiers fixes, taille
  bornée, champs retenus par liste blanche (jamais un chemin de fichier de données, jamais une
  donnée d'entreprise).

Mêmes règles que ``ops/backup/sm-backup-health.sh`` : une base INACTIVE (aucun segment terminé
en attente) n'est jamais en alerte ; un RETARD = des segments terminés non archivés au-delà du
seuil ; un échec d'archivage non suivi d'une réussite est CRITIQUE ; chaque opération planifiée
a un âge maximal de dernière réussite. Aucune action : sauvegarder et restaurer restent des
opérations d'exploitation tracées, jamais un bouton de la console.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

OK, WARNING, CRITICAL = "ok", "warning", "critical"
_RANK = {OK: 0, WARNING: 1, CRITICAL: 2}
_SEGMENT = re.compile(r"^[0-9A-F]{24}")
MAX_STATUS_BYTES = 256 * 1024
OPERATIONS = ("base_backup", "verify", "restore_test", "prune")


@dataclass(frozen=True)
class Thresholds:
    wal_max_lag_seconds: int = 900
    base_max_age_hours: int = 192
    verify_max_age_hours: int = 26
    restore_test_max_age_days: int = 35
    offsite_max_age_hours: int = 48
    disk_warn_free_percent: int = 15
    disk_critical_free_percent: int = 5
    run_max_hours: int = 24
    health_max_age_seconds: int = 900

    def max_age(self, operation: str) -> int:
        return {
            "base_backup": self.base_max_age_hours * 3600,
            "verify": self.verify_max_age_hours * 3600,
            "restore_test": self.restore_test_max_age_days * 86400,
            "prune": self.base_max_age_hours * 3600 + 86400,
            "offsite": self.offsite_max_age_hours * 3600,
        }[operation]


def worst(*levels: str) -> str:
    return max(levels, key=lambda level: _RANK[level]) if levels else OK


def _epoch(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return datetime.fromtimestamp(value, UTC)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any, limit: int = 300) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def read_status(directory: Path | None, name: str) -> dict[str, Any] | None:
    """Fichier ``status/<name>.json`` (nom fixe), ``None`` s'il est absent ou illisible."""
    if directory is None:
        return None
    path = directory / f"{name}.json"
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_STATUS_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _segment(name: str | None, per_id: int) -> tuple[int, int] | None:
    if not name or not _SEGMENT.match(name):
        return None
    return int(name[:8], 16), int(name[8:16], 16) * per_id + int(name[16:24], 16)


def archiving_status(db: Session, now: datetime, limits: Thresholds) -> dict[str, Any]:
    rows = db.execute(
        text(
            "SELECT name, setting FROM pg_settings WHERE name IN ('archive_mode', "
            "'archive_timeout', 'archive_command', 'wal_level', 'data_checksums', "
            "'wal_segment_size')"
        )
    ).tuples()
    settings = {name: setting for name, setting in rows}
    archiver = db.execute(
        text(
            "SELECT archived_count, last_archived_wal, last_archived_time, failed_count, "
            "last_failed_wal, last_failed_time, CASE WHEN pg_is_in_recovery() THEN NULL "
            "ELSE pg_walfile_name(pg_current_wal_lsn()) END AS current_wal FROM pg_stat_archiver"
        )
    ).one()
    return classify_archiving(
        settings,
        last_wal=archiver.last_archived_wal,
        last_at=archiver.last_archived_time,
        failed_wal=archiver.last_failed_wal,
        failed_at=archiver.last_failed_time,
        current_wal=archiver.current_wal,
        archived_count=int(archiver.archived_count),
        failed_count=int(archiver.failed_count),
        now=now,
        limits=limits,
    )


def classify_archiving(
    settings: dict[str, str],
    *,
    last_wal: str | None,
    last_at: datetime | None,
    failed_wal: str | None,
    failed_at: datetime | None,
    current_wal: str | None,
    archived_count: int,
    failed_count: int,
    now: datetime,
    limits: Thresholds,
) -> dict[str, Any]:
    """État de l'archivage : inactif ≠ retard ≠ échec (mêmes règles que sm-backup-health.sh)."""
    mode = settings.get("archive_mode")
    timeout = int(settings.get("archive_timeout") or 0)
    per_id = 0x100000000 // int(settings.get("wal_segment_size") or 16 * 1024 * 1024)
    age = int((now - last_at).total_seconds()) if last_at else None
    unresolved = failed_at is not None and (last_at is None or failed_at > last_at)
    current = _segment(current_wal, per_id)
    archived = _segment(last_wal, per_id)
    pending = (
        max(0, current[1] - archived[1] - 1)
        if current and archived and current[0] == archived[0]
        else None
    )
    messages: list[str] = []
    if mode not in ("on", "always") or not settings.get("archive_command"):
        state, level = "not_configured", CRITICAL
    elif unresolved:
        state, level = "failing", CRITICAL
    elif age is None:
        state, level = "never", WARNING
    elif pending is None:
        state = "archiving"
        level = WARNING if age > limits.wal_max_lag_seconds else OK
    elif pending > 0:
        state = "lagging" if age > limits.wal_max_lag_seconds else "archiving"
        level = CRITICAL if state == "lagging" else OK
    else:
        state = "idle" if age > limits.wal_max_lag_seconds else "up_to_date"
        level = OK
    if state != "not_configured" and not 0 < timeout <= limits.wal_max_lag_seconds:
        level = CRITICAL
        messages.append("archive_timeout_exceeds_rpo")
    return {
        "archive_mode": mode,
        "archive_timeout_seconds": timeout,
        "wal_level": settings.get("wal_level"),
        "data_checksums": settings.get("data_checksums") == "on",
        "archived_count": archived_count,
        "last_archived_wal": last_wal,
        "last_archived_at": last_at,
        "last_archived_age_seconds": age,
        "failed_count": failed_count,
        "last_failed_wal": failed_wal,
        "last_failed_at": failed_at,
        "failure_unresolved": unresolved,
        "pending_segments": pending,
        "state": state,
        "level": level,
        "messages": messages,
        "ok": level == OK,
    }


# Champs retenus par opération (liste blanche : nombres, booléens, libellés courts).
_DETAILS: dict[str, tuple[str, ...]] = {
    "base_backup": ("label", "size_bytes", "verified", "data_checksums", "timeline"),
    "verify": (
        "backups_checked",
        "backups_ok",
        "latest_verified_backup",
        "oldest_verified_backup",
        "wal_segments_checked",
        "wal_segments_missing",
        "timelines",
        "full",
        "restorable_from_epoch",
        "restorable_to_epoch",
    ),
    "restore_test": (
        "backup",
        "rto_seconds",
        "replay_seconds",
        "archive_lag_seconds_at_start",
        "wal_bytes_not_recovered",
        "alembic_revision",
        "tenants",
        "sites",
        "checks_failed",
        "cleanup",
    ),
    "prune": ("mode", "retention_days", "backups_kept", "oldest_kept", "wal_deleted"),
    "offsite": ("snapshot_id", "files", "bytes_added", "checked_subset"),
}


def _details(operation: str, data: dict[str, Any]) -> dict[str, str | int | bool | None]:
    out: dict[str, str | int | bool | None] = {}
    for key in _DETAILS[operation]:
        value = data.get(key)
        if isinstance(value, bool | int) or value is None:
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:120]
    return out


def operation_status(
    directory: Path | None, operation: str, now: datetime, limits: Thresholds
) -> dict[str, Any]:
    last = read_status(directory, operation)
    success = read_status(directory, f"{operation}.success")
    max_age = limits.max_age(operation)
    failure_level = WARNING if operation == "prune" else CRITICAL
    last_status = _str((last or {}).get("status"), 20)
    started = _epoch((last or {}).get("started_at_epoch"))
    success_at = _epoch((success or {}).get("finished_at_epoch"))
    success_age = int((now - success_at).total_seconds()) if success_at else None
    level, problem = OK, None
    if success_age is None:
        level, problem = failure_level, "never_succeeded"
    elif success_age > max_age:
        level, problem = failure_level, "too_old"
    if last_status in ("failed", "interrupted"):
        level, problem = failure_level, "last_run_failed"
    elif (
        last_status == "running"
        and started
        and (now - started).total_seconds() > limits.run_max_hours * 3600
    ):
        level, problem = CRITICAL, "stuck"
    errors = (last or {}).get("errors")
    source = (last if last_status != "running" else success) or {}
    raw_checks = source.get("checks") if operation == "restore_test" else None
    checks = [
        {"name": c["name"][:60], "ok": c["ok"]}
        for c in (raw_checks if isinstance(raw_checks, list) else [])
        if isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("ok"), bool)
    ]
    return {
        "operation": operation,
        "last_status": last_status,
        "last_started_at": started,
        "last_finished_at": _epoch((last or {}).get("finished_at_epoch")),
        "last_message": _str((last or {}).get("message")),
        "last_success_at": success_at,
        "last_success_age_seconds": success_age,
        "max_age_seconds": max_age,
        "problem": problem,
        "level": level,
        "details": _details(operation, success or {}),
        "errors": [e[:300] for e in errors[:5] if isinstance(e, str)]
        if isinstance(errors, list)
        else [],
        "checks": checks,
    }


def storage_status(directory: Path | None, limits: Thresholds) -> dict[str, Any]:
    inv = read_status(directory, "inventory") or {}
    free = _int(inv.get("disk_free_percent"))
    level, messages = OK, []
    if not inv:
        level, messages = WARNING, ["inventory_missing"]
    elif free is not None and free < limits.disk_critical_free_percent:
        level, messages = CRITICAL, ["disk_critical"]
    elif free is not None and free < limits.disk_warn_free_percent:
        level, messages = WARNING, ["disk_low"]
    if inv.get("same_filesystem_as_data") is True:
        level, messages = worst(level, WARNING), [*messages, "same_filesystem_as_data"]
    if (_int(inv.get("restore_test_dirs")) or 0) > 0:
        level, messages = worst(level, WARNING), [*messages, "restore_test_leftovers"]
    return {
        "configured": bool(inv),
        "generated_at": _epoch(inv.get("generated_at_epoch")),
        "base_backups": _int(inv.get("base_backups")) or 0,
        "base_backups_verified": _int(inv.get("base_backups_verified")) or 0,
        "base_backups_bytes": _int(inv.get("base_backups_bytes")) or 0,
        "latest_verified_backup": _str(inv.get("latest_verified_backup"), 30),
        "restorable_from": _epoch(inv.get("restorable_from_epoch")),
        "wal_files": _int(inv.get("wal_files")) or 0,
        "wal_bytes": _int(inv.get("wal_bytes")) or 0,
        "retention_days": _int(inv.get("retention_days")),
        "disk_free_bytes": _int(inv.get("disk_free_bytes")),
        "disk_free_percent": free,
        "same_filesystem_as_data": inv.get("same_filesystem_as_data") is True,
        "restore_test_dirs": _int(inv.get("restore_test_dirs")) or 0,
        "level": level,
        "messages": messages,
    }


def offsite_status(directory: Path | None, now: datetime, limits: Thresholds) -> dict[str, Any]:
    data = read_status(directory, "offsite") or {}
    configured = data.get("configured") is True
    if not configured:
        return {
            "configured": False,
            "repository": None,
            "last_status": _str(data.get("status"), 20),
            "last_success_at": None,
            "last_success_age_seconds": None,
            "level": WARNING,
            "problem": "not_configured",
            "details": {},
        }
    op = operation_status(directory, "offsite", now, limits)
    return {
        "configured": True,
        "repository": _str(data.get("repository"), 200),
        "last_status": op["last_status"],
        "last_success_at": op["last_success_at"],
        "last_success_age_seconds": op["last_success_age_seconds"],
        "level": op["level"],
        "problem": op["problem"],
        "details": op["details"],
    }


def supervision_status(directory: Path | None, now: datetime, limits: Thresholds) -> dict[str, Any]:
    data = read_status(directory, "health") or {}
    checked = _epoch(data.get("checked_at_epoch"))
    age = int((now - checked).total_seconds()) if checked else None
    reported = {"OK": OK, "AVERTISSEMENT": WARNING, "CRITIQUE": CRITICAL}.get(
        str(data.get("level")), CRITICAL
    )
    stale = age is None or age > limits.health_max_age_seconds
    return {
        "last_check_at": checked,
        "age_seconds": age,
        "reported_level": reported if checked else None,
        "stale": stale,
        "level": CRITICAL if stale else reported,
    }


def backup_thresholds(settings: Any) -> Thresholds:
    """Seuils de la configuration (``SM_BACKUP_*``)."""
    return Thresholds(
        wal_max_lag_seconds=settings.backup_wal_max_lag_seconds,
        base_max_age_hours=settings.backup_base_max_age_hours,
        verify_max_age_hours=settings.backup_verify_max_age_hours,
        restore_test_max_age_days=settings.backup_restore_test_max_age_days,
        offsite_max_age_hours=settings.backup_offsite_max_age_hours,
        disk_warn_free_percent=settings.backup_disk_warn_free_percent,
        disk_critical_free_percent=settings.backup_disk_critical_free_percent,
        run_max_hours=settings.backup_run_max_hours,
        health_max_age_seconds=settings.backup_health_max_age_seconds,
    )


def status_directory(status_dir: Path | None, backup_dir: Path | None) -> Path | None:
    """``SM_BACKUP_STATUS_DIR`` ; à défaut ``SM_BACKUP_DIR/status`` (compatibilité)."""
    if status_dir is not None:
        return status_dir
    return backup_dir / "status" if backup_dir is not None else None


def backup_status(
    db: Session, directory: Path | None, now: datetime, limits: Thresholds | None = None
) -> dict[str, Any]:
    limits = limits or Thresholds()
    archiving = archiving_status(db, now, limits)
    configured = directory is not None and directory.is_dir()
    folder = directory if configured else None
    operations = {op: operation_status(folder, op, now, limits) for op in OPERATIONS}
    storage = storage_status(folder, limits)
    offsite = offsite_status(folder, now, limits)
    supervision = supervision_status(folder, now, limits)
    overall = worst(
        archiving["level"],
        *(o["level"] for o in operations.values()),
        storage["level"],
        offsite["level"],
        supervision["level"],
        OK if configured else CRITICAL,
    )
    return {
        "checked_at": now.astimezone(UTC),
        "level": overall,
        "status_configured": configured,
        "rpo_seconds": limits.wal_max_lag_seconds,
        "thresholds": {
            "wal_max_lag_seconds": limits.wal_max_lag_seconds,
            "base_max_age_hours": limits.base_max_age_hours,
            "verify_max_age_hours": limits.verify_max_age_hours,
            "restore_test_max_age_days": limits.restore_test_max_age_days,
            "offsite_max_age_hours": limits.offsite_max_age_hours,
            "disk_warn_free_percent": limits.disk_warn_free_percent,
            "disk_critical_free_percent": limits.disk_critical_free_percent,
        },
        "archiving": archiving,
        "operations": operations,
        "storage": storage,
        "offsite": offsite,
        "supervision": supervision,
    }
