"""Console TechNova — état des sauvegardes (lecture seule) : archivage WAL lu dans
``pg_stat_archiver`` sans droit SQL supplémentaire, sauvegardes de base lues dans le répertoire
monté (``SM_BACKUP_DIR``) ; seuils du RPO et de fraîcheur ; administrateur requis ; aucune route
d'écriture."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.console.backups import backup_status, base_backups
from app.core.config import Settings
from tests.conftest import CONSOLE_HEADERS, CONSOLE_PREFIX, console_login

PATH = f"{CONSOLE_PREFIX}/operations/backups"


def _backup(root: Path, stamp: str, finished: datetime, verified: bool = True) -> None:
    folder = root / "base" / stamp
    folder.mkdir(parents=True)
    (folder / "backup_info.json").write_text(
        json.dumps(
            {
                "label": f"stockmanager-{stamp}",
                "finished_at": finished.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_seconds": 12,
                "verified": verified,
            }
        )
    )


def test_base_backups_are_read_newest_first_and_partial_ones_ignored(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    assert base_backups(None) is None
    assert base_backups(tmp_path) is None
    _backup(tmp_path, "20261101T011500Z", now - timedelta(hours=30))
    _backup(tmp_path, "20261102T011500Z", now - timedelta(hours=2), verified=False)
    (tmp_path / "base" / ".partial-20261102T020000Z").mkdir()
    found = base_backups(tmp_path)
    assert found is not None
    assert [b.label for b in found] == [
        "stockmanager-20261102T011500Z",
        "stockmanager-20261101T011500Z",
    ]
    assert [b.verified for b in found] == [False, True]


def test_status_flags_stale_or_unverified_backups(tmp_path: Path, owner_db: Any) -> None:
    now = datetime.now(UTC)
    _backup(tmp_path, "20261101T011500Z", now - timedelta(hours=30))
    _backup(tmp_path, "20261102T011500Z", now - timedelta(hours=2), verified=False)
    status = backup_status(owner_db, tmp_path, now)
    base = status["base_backups"]
    # La plus récente n'est pas vérifiée : la dernière VÉRIFIÉE a 30 h (> 26 h).
    assert (base["count"], base["unverified_count"], base["latest_age_hours"]) == (2, 1, 30)
    assert base["ok"] is False
    _backup(tmp_path, "20261102T050000Z", now - timedelta(hours=1))
    assert backup_status(owner_db, tmp_path, now)["base_backups"]["ok"] is True
    # Base de test sans archivage : jamais signalée comme conforme au RPO.
    assert status["archiving"]["ok"] is (status["archiving"]["archive_mode"] in ("on", "always"))
    assert status["rpo_seconds"] == 900


def test_console_route_requires_an_administrator_and_reads_only(
    console_app: Any, platform_admin: Any, settings: Settings, tmp_path: Path
) -> None:
    with TestClient(console_app) as anonymous:
        assert anonymous.get(PATH, headers=CONSOLE_HEADERS).status_code == 401
    now = datetime.now(UTC)
    _backup(tmp_path, "20261102T011500Z", now - timedelta(hours=1))
    console_app.state.settings = settings.model_copy(update={"backup_dir": tmp_path})
    platform_admin()
    with TestClient(console_app) as client:
        assert console_login(client).status_code == 200
        response = client.get(PATH, headers=CONSOLE_HEADERS)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["base_backups"]["configured"] is True
        assert body["base_backups"]["latest_label"] == "stockmanager-20261102T011500Z"
        assert set(body["archiving"]) >= {"archive_mode", "failed_count", "last_archived_at"}
    paths = console_app.openapi()["paths"]
    assert set(paths[f"{CONSOLE_PREFIX}/operations/backups"]) == {"get"}
