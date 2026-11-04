"""Console TechNova — état des sauvegardes (R11-0, ADR-0057), lecture seule.

Archivage lu en direct (``pg_stat_archiver``, aucun droit SQL supplémentaire) et classé :
inactif ≠ retard ≠ échec ; opérations planifiées, stockage, copie externe et supervision lus
dans les SEULS statuts ``status/*.json`` (noms fixes, taille bornée, liens symboliques refusés,
champs en liste blanche : aucun chemin, aucun contenu de sauvegarde) ; seuils configurables ;
administrateur requis ; aucune route d'écriture."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.console.backups import (
    CRITICAL,
    OK,
    WARNING,
    Thresholds,
    backup_status,
    backup_thresholds,
    classify_archiving,
    offsite_status,
    operation_status,
    read_status,
    status_directory,
    storage_status,
    supervision_status,
)
from app.core.config import Settings
from tests.conftest import CONSOLE_HEADERS, CONSOLE_PREFIX, console_login

PATH = f"{CONSOLE_PREFIX}/operations/backups"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
LIMITS = Thresholds()
ARCHIVE_ON = {
    "archive_mode": "on",
    "archive_command": "/opt/stockmanager/backup/sm-wal-archive.sh %p %f",
    "archive_timeout": "300",
    "wal_segment_size": str(16 * 1024 * 1024),
    "data_checksums": "off",
    "wal_level": "replica",
}


def _epoch(delta: timedelta) -> int:
    return int((NOW - delta).timestamp())


def _write(folder: Path, name: str, data: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def _op(folder: Path, op: str, status: str, ago: timedelta, **extra: Any) -> None:
    data = {
        "operation": op,
        "status": status,
        "started_at_epoch": _epoch(ago + timedelta(minutes=5)),
        "finished_at_epoch": None if status == "running" else _epoch(ago),
        "message": f"{op} {status}",
        **extra,
    }
    _write(folder, op, data)
    if status == "success":
        _write(folder, f"{op}.success", data)


def _archiving(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "last_wal": "000000010000000000000010",
        "last_at": NOW - timedelta(minutes=2),
        "failed_wal": None,
        "failed_at": None,
        "current_wal": "000000010000000000000011",
        "archived_count": 16,
        "failed_count": 0,
        "now": NOW,
        "limits": LIMITS,
    }
    settings = kw.pop("settings", ARCHIVE_ON)
    return classify_archiving(settings, **(args | kw))


# --- Archivage : inactif ≠ retard ≠ échec ------------------------------------------------------


def test_archiving_up_to_date_and_idle_database_is_never_an_alert() -> None:
    assert _archiving()["state"] == "up_to_date"
    # Base INACTIVE : dernier archivage il y a 3 jours, mais aucun segment terminé en attente.
    idle = _archiving(last_at=NOW - timedelta(days=3))
    assert (idle["state"], idle["level"], idle["pending_segments"]) == ("idle", OK, 0)
    # Segment courant = dernier archivé (juste après une bascule) : à jour.
    same = _archiving(current_wal="000000010000000000000010", last_at=NOW - timedelta(days=1))
    assert (same["state"], same["level"]) == ("idle", OK)


def test_archiving_lag_is_critical_only_beyond_the_threshold() -> None:
    recent = _archiving(current_wal="000000010000000000000014")
    assert (recent["state"], recent["level"], recent["pending_segments"]) == ("archiving", OK, 3)
    late = _archiving(current_wal="000000010000000000000014", last_at=NOW - timedelta(minutes=16))
    assert (late["state"], late["level"]) == ("lagging", CRITICAL)
    # Passage d'un identifiant de journal (…0FF → …100) : 1 segment en attente seulement.
    edge = _archiving(last_wal="0000000100000000000000FE", current_wal="000000010000000100000000")
    assert edge["pending_segments"] == 1


def test_unresolved_archive_failure_is_critical_and_a_resolved_one_is_not() -> None:
    failing = _archiving(
        failed_wal="000000010000000000000011", failed_at=NOW - timedelta(seconds=30)
    )
    assert (failing["state"], failing["level"], failing["failure_unresolved"]) == (
        "failing",
        CRITICAL,
        True,
    )
    resolved = _archiving(
        failed_wal="00000001000000000000000F", failed_at=NOW - timedelta(minutes=10)
    )
    assert (resolved["level"], resolved["failure_unresolved"]) == (OK, False)


def test_archiving_configuration_errors() -> None:
    off = _archiving(settings=ARCHIVE_ON | {"archive_mode": "off"})
    assert (off["state"], off["level"]) == ("not_configured", CRITICAL)
    empty = _archiving(settings=ARCHIVE_ON | {"archive_command": ""})
    assert empty["state"] == "not_configured"
    slow = _archiving(settings=ARCHIVE_ON | {"archive_timeout": "3600"})
    assert (slow["level"], slow["messages"]) == (CRITICAL, ["archive_timeout_exceeds_rpo"])
    never = _archiving(last_wal=None, last_at=None)
    assert (never["state"], never["level"]) == ("never", WARNING)
    # Changement de timeline : retard non calculable, signalé seulement s'il est ancien.
    other = _archiving(current_wal="000000020000000000000011", last_at=NOW - timedelta(hours=1))
    assert (other["pending_segments"], other["level"]) == (None, WARNING)


# --- Opérations planifiées ---------------------------------------------------------------------


def test_operations_never_run_recent_old_failed_and_stuck(tmp_path: Path) -> None:
    assert operation_status(tmp_path, "base_backup", NOW, LIMITS)["problem"] == "never_succeeded"
    _op(tmp_path, "base_backup", "success", timedelta(days=2), label="20261031T011500Z")
    ok = operation_status(tmp_path, "base_backup", NOW, LIMITS)
    assert (ok["level"], ok["details"]["label"]) == (OK, "20261031T011500Z")
    _op(tmp_path, "base_backup", "success", timedelta(days=9))
    assert operation_status(tmp_path, "base_backup", NOW, LIMITS)["problem"] == "too_old"
    _op(tmp_path, "verify", "success", timedelta(hours=25))
    assert operation_status(tmp_path, "verify", NOW, LIMITS)["level"] == OK
    _op(tmp_path, "verify", "success", timedelta(hours=27))
    assert operation_status(tmp_path, "verify", NOW, LIMITS)["level"] == CRITICAL
    _op(tmp_path, "restore_test", "success", timedelta(days=34))
    assert operation_status(tmp_path, "restore_test", NOW, LIMITS)["level"] == OK
    _op(tmp_path, "restore_test", "success", timedelta(days=36))
    assert operation_status(tmp_path, "restore_test", NOW, LIMITS)["level"] == CRITICAL
    # Échec après une réussite récente : toujours critique (sauf la rétention : avertissement).
    _op(tmp_path, "verify", "success", timedelta(hours=2))
    _op(tmp_path, "verify", "failed", timedelta(hours=1), errors=["segment manquant"])
    failed = operation_status(tmp_path, "verify", NOW, LIMITS)
    assert (failed["level"], failed["problem"], failed["errors"]) == (
        CRITICAL,
        "last_run_failed",
        ["segment manquant"],
    )
    _op(tmp_path, "prune", "success", timedelta(days=1))
    _op(tmp_path, "prune", "failed", timedelta(hours=1))
    assert operation_status(tmp_path, "prune", NOW, LIMITS)["level"] == WARNING
    # « En cours » depuis plus de 24 h : bloquée.
    _op(tmp_path, "base_backup", "success", timedelta(days=1))
    _op(tmp_path, "base_backup", "running", timedelta(hours=30))
    assert operation_status(tmp_path, "base_backup", NOW, LIMITS)["problem"] == "stuck"
    _op(tmp_path, "base_backup", "running", timedelta(hours=1))
    assert operation_status(tmp_path, "base_backup", NOW, LIMITS)["level"] == OK


def test_only_whitelisted_status_fields_are_exposed(tmp_path: Path) -> None:
    checks = [{"name": "rls_tenant_isolation", "ok": True, "detail": "tenant 1234: secret"}]
    _op(
        tmp_path,
        "restore_test",
        "success",
        timedelta(days=1),
        rto_seconds=42,
        run_dir="/backups/restore-test/x",
        checks=checks,
        database_url="postgresql://user:password@host/db",
    )
    status = operation_status(tmp_path, "restore_test", NOW, LIMITS)
    assert status["details"]["rto_seconds"] == 42
    assert "run_dir" not in status["details"] and "database_url" not in status["details"]
    assert status["checks"] == [{"name": "rls_tenant_isolation", "ok": True}]
    dumped = json.dumps(status, default=str)
    assert "secret" not in dumped and "password" not in dumped


def test_status_files_are_read_defensively(tmp_path: Path) -> None:
    assert read_status(None, "verify") is None
    (tmp_path / "verify.json").write_text("{pas du json", encoding="utf-8")
    assert read_status(tmp_path, "verify") is None
    (tmp_path / "prune.json").write_text("[1, 2]", encoding="utf-8")
    assert read_status(tmp_path, "prune") is None
    (tmp_path / "big.json").write_text(json.dumps({"x": "a" * 300_000}), encoding="utf-8")
    assert read_status(tmp_path, "big") is None
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"status": "success"}), encoding="utf-8")
    os.symlink(outside, tmp_path / "offsite.json")
    assert read_status(tmp_path, "offsite") is None


# --- Stockage, copie externe, supervision ------------------------------------------------------


def test_storage_levels(tmp_path: Path) -> None:
    assert storage_status(tmp_path, LIMITS)["messages"] == ["inventory_missing"]
    base = {"base_backups": 3, "base_backups_verified": 3, "disk_free_percent": 40}
    _write(tmp_path, "inventory", base)
    assert storage_status(tmp_path, LIMITS)["level"] == OK
    _write(tmp_path, "inventory", base | {"disk_free_percent": 10})
    assert storage_status(tmp_path, LIMITS)["messages"] == ["disk_low"]
    _write(tmp_path, "inventory", base | {"disk_free_percent": 3})
    assert storage_status(tmp_path, LIMITS)["level"] == CRITICAL
    _write(tmp_path, "inventory", base | {"same_filesystem_as_data": True, "restore_test_dirs": 1})
    storage = storage_status(tmp_path, LIMITS)
    assert storage["level"] == WARNING
    assert storage["messages"] == ["same_filesystem_as_data", "restore_test_leftovers"]


def test_offsite_is_reported_not_configured_until_it_really_is(tmp_path: Path) -> None:
    absent = offsite_status(tmp_path, NOW, LIMITS)
    assert (absent["configured"], absent["level"], absent["problem"]) == (
        False,
        WARNING,
        "not_configured",
    )
    _write(tmp_path, "offsite", {"status": "not_configured", "configured": False})
    assert offsite_status(tmp_path, NOW, LIMITS)["configured"] is False
    _op(tmp_path, "offsite", "success", timedelta(hours=3), configured=True, snapshot_id="ab12")
    ok = offsite_status(tmp_path, NOW, LIMITS)
    assert (ok["configured"], ok["level"], ok["details"]["snapshot_id"]) == (True, OK, "ab12")
    _op(tmp_path, "offsite", "failed", timedelta(hours=1), configured=True)
    assert offsite_status(tmp_path, NOW, LIMITS)["level"] == CRITICAL


def test_supervision_stale_or_reported(tmp_path: Path) -> None:
    assert supervision_status(tmp_path, NOW, LIMITS)["level"] == CRITICAL
    _write(tmp_path, "health", {"checked_at_epoch": _epoch(timedelta(minutes=3)), "level": "OK"})
    assert supervision_status(tmp_path, NOW, LIMITS)["level"] == OK
    _write(
        tmp_path,
        "health",
        {"checked_at_epoch": _epoch(timedelta(minutes=3)), "level": "AVERTISSEMENT"},
    )
    assert supervision_status(tmp_path, NOW, LIMITS)["level"] == WARNING
    _write(tmp_path, "health", {"checked_at_epoch": _epoch(timedelta(minutes=20)), "level": "OK"})
    assert supervision_status(tmp_path, NOW, LIMITS)["stale"] is True


def test_status_directory_and_thresholds_come_from_settings(settings: Settings) -> None:
    assert status_directory(None, None) is None
    assert status_directory(None, Path("/srv/b")) == Path("/srv/b/status")
    assert status_directory(Path("/x/status"), Path("/srv/b")) == Path("/x/status")
    custom = backup_thresholds(settings.model_copy(update={"backup_base_max_age_hours": 48}))
    assert (custom.base_max_age_hours, custom.verify_max_age_hours) == (48, 26)


@pytest.fixture
def healthy(tmp_path: Path) -> Path:
    now = datetime.now(UTC)
    ago = now - timedelta(hours=1)
    for op in ("base_backup", "verify", "restore_test", "prune"):
        data = {
            "status": "success",
            "started_at_epoch": int(ago.timestamp()) - 60,
            "finished_at_epoch": int(ago.timestamp()),
        }
        _write(tmp_path, op, data)
        _write(tmp_path, f"{op}.success", data)
    _write(tmp_path, "inventory", {"base_backups": 2, "disk_free_percent": 50})
    _write(tmp_path, "health", {"checked_at_epoch": int(now.timestamp()), "level": "OK"})
    return tmp_path


def test_overall_status(owner_db: Any, healthy: Path, tmp_path_factory: Any) -> None:
    status = backup_status(owner_db, healthy, datetime.now(UTC))
    assert status["status_configured"] is True
    assert {op: s["level"] for op, s in status["operations"].items()} == dict.fromkeys(
        ("base_backup", "verify", "restore_test", "prune"), OK
    )
    # Copie externe non configurée : au mieux un avertissement ; base de test sans archivage :
    # jamais présentée comme conforme.
    assert status["offsite"]["level"] == WARNING
    expected = WARNING if status["archiving"]["ok"] else CRITICAL
    assert status["level"] == expected
    missing = backup_status(owner_db, tmp_path_factory.mktemp("vide") / "absent", datetime.now(UTC))
    assert (missing["status_configured"], missing["level"]) == (False, CRITICAL)


def test_console_route_requires_an_administrator_and_reads_only(
    console_app: Any, platform_admin: Any, settings: Settings, healthy: Path
) -> None:
    with TestClient(console_app) as anonymous:
        assert anonymous.get(PATH, headers=CONSOLE_HEADERS).status_code == 401
    console_app.state.settings = settings.model_copy(update={"backup_status_dir": healthy})
    platform_admin()
    with TestClient(console_app) as client:
        assert console_login(client).status_code == 200
        response = client.get(PATH, headers=CONSOLE_HEADERS)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status_configured"] is True
        assert body["operations"]["restore_test"]["level"] == "ok"
        assert body["offsite"]["configured"] is False
        assert set(body["archiving"]) >= {"state", "pending_segments", "failure_unresolved"}
        assert body["thresholds"]["base_max_age_hours"] == 192
    paths = console_app.openapi()["paths"]
    assert set(paths[f"{CONSOLE_PREFIX}/operations/backups"]) == {"get"}
