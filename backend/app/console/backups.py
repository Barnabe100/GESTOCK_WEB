"""État des sauvegardes dans la console TechNova (lecture seule, docs/exploitation).

- Archivage continu des WAL : vue ``pg_stat_archiver`` et réglages ``archive_mode`` /
  ``archive_timeout`` (lisibles sans privilège : aucun droit SQL ajouté au rôle de la console).
- Sauvegardes de base : répertoire ``SM_BACKUP_DIR`` monté en LECTURE SEULE dans le conteneur de
  la console (facultatif) — ``backup_info.json`` de chaque sauvegarde (écrit par
  ``ops/backup/sm-base-backup.sh`` après ``pg_verifybackup``).

Seuils (mêmes que ``ops/monitoring/sm-healthcheck.sh``) : WAL archivé depuis 15 min au plus
(RPO), sauvegarde vérifiée de moins de 26 h. Aucune action : sauvegarder et restaurer restent
des opérations d'exploitation tracées (procédure documentée), jamais un bouton de la console.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

RPO_SECONDS = 15 * 60
BASE_MAX_AGE_HOURS = 26


@dataclass(frozen=True)
class BaseBackup:
    label: str
    finished_at: datetime | None
    duration_seconds: int | None
    verified: bool


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def base_backups(root: Path | None) -> list[BaseBackup] | None:
    """Sauvegardes de base connues (les plus récentes d'abord) ; ``None`` sans répertoire."""
    if root is None or not (root / "base").is_dir():
        return None
    out: list[BaseBackup] = []
    for entry in sorted((root / "base").iterdir(), reverse=True):
        if not entry.is_dir() or not entry.name.startswith("20"):
            continue  # sauvegarde en cours (« .partial-… ») ou fichier étranger
        try:
            info = json.loads((entry / "backup_info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        duration = info.get("duration_seconds")
        out.append(
            BaseBackup(
                label=str(info.get("label") or entry.name),
                finished_at=_parse(info.get("finished_at")),
                duration_seconds=duration if isinstance(duration, int) else None,
                verified=info.get("verified") is True,
            )
        )
    return out


def backup_status(db: Session, root: Path | None, now: datetime) -> dict[str, Any]:
    rows = db.execute(
        text(
            "SELECT name, setting FROM pg_settings WHERE name IN "
            "('archive_mode', 'archive_timeout', 'wal_level')"
        )
    ).tuples()
    settings = {name: setting for name, setting in rows}
    archiver = db.execute(
        text(
            "SELECT archived_count, last_archived_wal, last_archived_time, failed_count, "
            "last_failed_wal, last_failed_time FROM pg_stat_archiver"
        )
    ).one()
    last_archived = archiver.last_archived_time
    archive_age = int((now - last_archived).total_seconds()) if last_archived else None
    archiving_ok = (
        settings.get("archive_mode") in ("on", "always")
        and archive_age is not None
        and archive_age <= RPO_SECONDS
    )
    backups = base_backups(root)
    latest = next((b for b in backups or [] if b.verified and b.finished_at), None)
    latest_age = (
        int((now - latest.finished_at).total_seconds() // 3600)
        if latest and latest.finished_at
        else None
    )
    return {
        "checked_at": now.astimezone(UTC),
        "rpo_seconds": RPO_SECONDS,
        "archiving": {
            "archive_mode": settings.get("archive_mode"),
            "archive_timeout_seconds": int(settings.get("archive_timeout") or 0),
            "wal_level": settings.get("wal_level"),
            "archived_count": int(archiver.archived_count),
            "last_archived_wal": archiver.last_archived_wal,
            "last_archived_at": last_archived,
            "last_archived_age_seconds": archive_age,
            "failed_count": int(archiver.failed_count),
            "last_failed_wal": archiver.last_failed_wal,
            "last_failed_at": archiver.last_failed_time,
            "ok": archiving_ok,
        },
        "base_backups": {
            "configured": backups is not None,
            "count": len(backups or []),
            "latest_label": latest.label if latest else None,
            "latest_finished_at": latest.finished_at if latest else None,
            "latest_age_hours": latest_age,
            "unverified_count": sum(1 for b in backups or [] if not b.verified),
            "ok": latest_age is not None and latest_age <= BASE_MAX_AGE_HOURS,
            "max_age_hours": BASE_MAX_AGE_HOURS,
        },
    }
