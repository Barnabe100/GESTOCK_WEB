"""Console TechNova — Sauvegarder, Restaurer, Paramètres (R11, ADR-0058).

La console enregistre des paramètres, des demandes et des décisions ; l'agent (rôle SQL
``stockmanager_backup``, simulé ici par ``SET ROLE``) exécute. Couvre : planificateur en base
(prochaine exécution, mise en file idempotente, échéances manquées, aucune exécution
concurrente, nouvelles tentatives, reprise), sauvegardes immédiates, historique et filtres,
restauration à double approbation (deux administrateurs distincts, contrainte en base,
transitions réservées au bon rôle), retour arrière, PITR dans la fenêtre seulement, verrou d'une
entreprise pendant l'application (503, autres entreprises non concernées), périmètre et RLS
des rôles de sauvegarde / restauration, audit."""

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.restaurant_orders.contact_purge import CONTACT_RETENTION_DAYS
from app.platform.member_notifications.purge import RETENTION_DAYS
from tests.conftest import (
    CONSOLE_HEADERS,
    CONSOLE_PREFIX,
    PLATFORM_ADMIN_PASSWORD,
    console_login,
)

B = f"{CONSOLE_PREFIX}/operations/backups"
ROOT = Path(__file__).resolve().parents[2]
SECOND_ADMIN = "second@technova.example"


@pytest.fixture(autouse=True)
def keep_policies(owner_engine: Engine, migrated: None) -> Iterator[None]:
    """Paramètres de planification remis à l'identique après chaque test."""
    with owner_engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM backup_policies")).mappings().all()
    yield
    cols = [c for c in rows[0] if c != "id"]
    assignments = ", ".join(f"{c} = :{c}" for c in cols)
    with owner_engine.begin() as conn:
        for row in rows:
            conn.execute(
                text(f"UPDATE backup_policies SET {assignments} WHERE id = :id"), dict(row)
            )


@pytest.fixture
def admin(console: TestClient, platform_admin: Any) -> TestClient:
    platform_admin()
    assert console_login(console).status_code == 200
    return console


@pytest.fixture
def second(console_app: Any, platform_admin: Any) -> Iterator[TestClient]:
    platform_admin(email=SECOND_ADMIN, name="Second TechNova")
    with TestClient(console_app) as client:
        assert console_login(client, SECOND_ADMIN, PLATFORM_ADMIN_PASSWORD).status_code == 200
        yield client


def _req(client: TestClient, method: str, path: str, **kw: Any) -> Any:
    return client.request(method, f"{B}{path}", headers=CONSOLE_HEADERS, **kw)


def _agent(owner_engine: Engine, sql: str, params: dict[str, Any] | None = None) -> Any:
    """Exécute comme l'agent (rôle de réplication, droits de la migration 0063)."""
    with owner_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE stockmanager_backup"))
        result = conn.execute(text(sql), params or {})
        return result.all() if result.returns_rows else None


def _revision(owner_db: Session) -> str:
    return str(owner_db.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def _item(
    owner_engine: Engine, tenant_id: uuid.UUID, *, revision: str, status: str = "succeeded"
) -> uuid.UUID:
    with owner_engine.begin() as conn:
        run = conn.execute(
            text(
                "INSERT INTO backup_runs (id, kind, scope, trigger, status, requested_label, "
                "finished_at) "
                "VALUES (gen_random_uuid(), 'logical', 'platform', 'manual', 'succeeded', 'test', "
                "now()) RETURNING id"
            )
        ).scalar_one()
        return uuid.UUID(
            str(
                conn.execute(
                    text(
                        "INSERT INTO backup_items (id, run_id, tenant_id, status, storage_ref, "
                        "table_count, row_count, "
                        "size_bytes, manifest_sha256, alembic_revision, verified, verified_at, "
                        "finished_at, expires_at) "
                        "VALUES (gen_random_uuid(), :run, :t, :st, :ref, 64, 100, 1000, :sha, "
                        ":rev, true, now(), now(), "
                        "now() + interval '30 days') RETURNING id"
                    ),
                    {
                        "run": run,
                        "t": tenant_id,
                        "st": status,
                        "ref": f"logical/{tenant_id}/20261104T120000Z-0abc1234",
                        "sha": "0" * 64,
                        "rev": revision,
                    },
                ).scalar_one()
            )
        )


def _verify(owner_engine: Engine, op_id: str, *, expires: str = "24 hours") -> None:
    """Ce que fait l'agent après une vérification réussie sur copie isolée."""
    with owner_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE stockmanager_backup"))
        conn.execute(
            text("UPDATE restore_operations SET status = 'verifying' WHERE id = :id"), {"id": op_id}
        )
        conn.execute(
            text(
                "UPDATE restore_operations SET status = 'verified', verified_at = now(), "
                'verification = \'{"copy": {"checks": []}}\', decision_expires_at = now() + '
                "CAST(:e AS interval) "
                "WHERE id = :id"
            ),
            {"id": op_id, "e": expires},
        )


def _platform_audit(owner_db: Session, action: str) -> list[Any]:
    return list(
        owner_db.execute(
            text("SELECT * FROM platform_audit_logs WHERE action = :a ORDER BY occurred_at"),
            {"a": action},
        ).mappings()
    )


# --- Paramètres et planificateur ---------------------------------------------------------------


def test_policies_cover_every_category_and_frequency_with_server_computed_next_run(
    admin: TestClient,
) -> None:
    response = _req(admin, "GET", "/policies")
    assert response.status_code == 200, response.text
    rows = {(p["category"], p["frequency"]): p for p in response.json()}
    for category in ("physical", "logical"):
        for frequency in ("daily", "weekly", "monthly", "automatic", "manual"):
            assert (category, frequency) in rows
    assert rows[("physical", "weekly")]["enabled"] is True
    assert rows[("physical", "weekly")]["weekday"] == 7
    assert rows[("physical", "weekly")]["next_run_at"] is not None
    assert rows[("logical", "daily")]["enabled"] is True
    assert rows[("physical", "manual")]["schedulable"] is False
    assert rows[("physical", "manual")]["next_run_at"] is None
    assert rows[("physical", "weekly")]["fields"] == ["run_time", "weekday"]
    nxt = datetime.fromisoformat(rows[("physical", "weekly")]["next_run_at"])
    assert nxt.weekday() == 6 and (nxt.hour, nxt.minute) == (1, 15)


def test_policy_update_drives_the_next_run_in_its_timezone_and_is_audited(
    admin: TestClient, owner_db: Session
) -> None:
    body = {
        "enabled": True,
        "run_time": "22:30",
        "weekday": 3,
        "start_date": "2026-01-01",
        "timezone": "Europe/Paris",
        "retention_days": 45,
        "reason": "Sauvegarde du mercredi soir",
    }
    response = _req(admin, "PUT", "/policies/physical/weekly", json=body)
    assert response.status_code == 200, response.text
    out = response.json()
    nxt = datetime.fromisoformat(out["next_run_at"])
    local = nxt.astimezone(__import__("zoneinfo").ZoneInfo("Europe/Paris"))
    assert local.isoweekday() == 3 and (local.hour, local.minute) == (22, 30)
    assert nxt > datetime.now(UTC)
    assert out["retention_days"] == 45 and out["month_day"] is None
    # La valeur lue par l'agent est EXACTEMENT celle affichée.
    stored = owner_db.execute(
        text(
            "SELECT next_run_at FROM backup_policies WHERE category = 'physical' AND frequency = "
            "'weekly'"
        )
    ).scalar_one()
    assert stored == nxt
    [entry] = _platform_audit(owner_db, "backup.policy_updated")
    assert entry["reason"] == "Sauvegarde du mercredi soir"
    assert entry["before"]["weekday"] == 7 and entry["after"]["weekday"] == 3
    assert entry["after"]["timezone"] == "Europe/Paris"


@pytest.mark.parametrize(
    ("frequency", "patch", "code"),
    [
        ("weekly", {"timezone": "Mars/Olympus"}, "invalid_timezone"),
        ("weekly", {"weekday": None}, "policy_field_required"),
        ("monthly", {"month_day": None}, "policy_field_required"),
        ("automatic", {"interval_hours": None}, "policy_field_required"),
        ("manual", {"enabled": True}, "manual_policy_not_schedulable"),
    ],
)
def test_policy_validation_is_server_side(
    admin: TestClient, frequency: str, patch: dict[str, Any], code: str
) -> None:
    body = {
        "enabled": True,
        "run_time": "03:00",
        "weekday": 1,
        "month_day": 15,
        "interval_hours": 6,
        "start_date": "2026-01-01",
        "timezone": "UTC",
        "retention_days": 30,
        "reason": "Contrôle des règles",
    } | patch
    response = _req(admin, "PUT", f"/policies/logical/{frequency}", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code


def test_policy_reason_is_mandatory(admin: TestClient) -> None:
    body = {
        "enabled": False,
        "start_date": "2026-01-01",
        "timezone": "UTC",
        "retention_days": 30,
        "reason": "x",
    }
    assert _req(admin, "PUT", "/policies/logical/daily", json=body).status_code == 422


def test_monthly_31_falls_back_to_the_last_day_and_automatic_follows_its_anchor(
    owner_db: Session,
) -> None:
    # Colonnes de backup_policies, dans l'ordre : id, category, frequency, enabled, run_time,
    # weekday, month_day, interval_hours, start_date, timezone, retention_days, next_run_at,
    # last_enqueued_at, updated_at, updated_by.
    monthly, automatic = owner_db.execute(
        text(
            "SELECT backup_policy_next_run(ROW(gen_random_uuid(), 'physical', 'monthly', true, "
            "'01:15'::time, NULL, 31, "
            "NULL, '2026-01-01'::date, 'UTC', 30, NULL, NULL, now(), NULL)::backup_policies, "
            "'2027-02-01 00:00+00'), "
            "backup_policy_next_run(ROW(gen_random_uuid(), 'physical', 'automatic', true, "
            "'08:00'::time, NULL, NULL, 6, "
            "'2026-11-01'::date, 'Europe/Paris', 30, NULL, NULL, now(), NULL)::backup_policies, "
            "'2026-11-04 10:30+00')"
        )
    ).one()
    assert monthly == datetime(2027, 2, 28, 1, 15, tzinfo=UTC)
    # Ancre 08:00 Paris (07:00 UTC) toutes les 6 h → 13:00 UTC après 10:30 UTC.
    assert automatic == datetime(2026, 11, 4, 13, 0, tzinfo=UTC)


def test_due_policies_are_enqueued_once_and_missed_occurrences_run_once(
    owner_engine: Engine,
) -> None:
    with owner_engine.begin() as conn:
        # Échéance « manquée » depuis 3 semaines (agent arrêté) : une seule exécution.
        conn.execute(
            text(
                "UPDATE backup_policies SET next_run_at = now() - interval '21 days' "
                "WHERE category = 'physical' AND frequency = 'weekly'"
            )
        )
    first = _agent(owner_engine, "SELECT backup_enqueue_due(now())")[0][0]
    again = _agent(owner_engine, "SELECT backup_enqueue_due(now())")[0][0]
    assert (first, again) == (1, 0)
    with owner_engine.connect() as conn:
        runs = conn.execute(text("SELECT kind, trigger, status FROM backup_runs")).all()
        nxt = conn.execute(
            text(
                "SELECT next_run_at FROM backup_policies WHERE category = 'physical' AND "
                "frequency = 'weekly'"
            )
        ).scalar_one()
    assert runs == [("physical", "schedule", "queued")]
    assert nxt > datetime.now(UTC)


def test_disabled_policies_are_never_enqueued(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE backup_policies SET enabled = false, next_run_at = now() - interval '1 "
                "hour'"
            )
        )
    assert _agent(owner_engine, "SELECT backup_enqueue_due(now())")[0][0] == 0


def test_no_concurrent_run_of_the_same_kind_and_scope(admin: TestClient, provision: Any) -> None:
    first = _req(admin, "POST", "/runs", json={"kind": "physical"})
    assert first.status_code == 201, first.text
    dup = _req(admin, "POST", "/runs", json={"kind": "physical"})
    assert dup.status_code == 409 and dup.json()["code"] == "backup_already_queued"
    # Une autre portée n'est pas bloquée.
    tenant = provision("alpha").tenant_id
    other = _req(
        admin,
        "POST",
        "/runs",
        json={"kind": "logical", "scope": "tenant", "tenant_id": str(tenant)},
    )
    assert other.status_code == 201, other.text


def test_failures_are_retried_with_backoff_then_stop(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO backup_runs (id, kind, scope, trigger, requested_label) "
                "VALUES (gen_random_uuid(), 'verification', 'platform', 'manual', 'test')"
            )
        )
    for attempt in (1, 2, 3):
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE backup_runs SET not_before = now() WHERE status = 'queued'"))
        [claimed] = _agent(
            owner_engine, "SELECT id, attempt FROM backup_claim('test', ARRAY['verification'])"
        )
        assert claimed[1] == attempt
        [retry] = _agent(
            owner_engine,
            "SELECT backup_finish(:id, false, '{}'::jsonb, 'disque plein')",
            {"id": claimed[0]},
        )
        if attempt < 3:
            assert retry[0] is not None
            with owner_engine.connect() as conn:
                delay = conn.execute(
                    text(
                        "SELECT extract(epoch FROM not_before - now()) FROM backup_runs WHERE id "
                        "= :id"
                    ),
                    {"id": retry[0]},
                ).scalar_one()
            assert 5 * 60 * 2 ** (attempt - 1) - 30 < float(delay) <= 5 * 60 * 2 ** (attempt - 1)
        else:
            assert retry[0] is None
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM backup_runs WHERE status = 'failed'")).scalar()
            == 3
        )


def test_stale_running_runs_are_reclaimed(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO backup_runs (id, kind, scope, trigger, status, requested_label, "
                "heartbeat_at, started_at) "
                "VALUES (gen_random_uuid(), 'physical', 'platform', 'schedule', 'running', 'test', "
                "now() - interval '3 hours', now() - interval '3 hours')"
            )
        )
    assert _agent(owner_engine, "SELECT backup_reclaim_stale(interval '2 hours')")[0][0] == 1
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT status, trigger FROM backup_runs ORDER BY requested_at")
        ).all()
    assert rows == [("failed", "schedule"), ("queued", "retry")]


def test_history_is_never_deleted(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO backup_runs (id, kind, scope, trigger, requested_label) "
                "VALUES (gen_random_uuid(), 'physical', 'platform', 'manual', 'test')"
            )
        )
    with pytest.raises(DBAPIError, match="suppression interdite"), owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM backup_runs"))


# --- Sauvegarder ----------------------------------------------------------------------------------


def test_immediate_backups_for_all_or_one_company_with_audit(
    admin: TestClient, provision: Any, owner_db: Session
) -> None:
    alpha = provision("alpha").tenant_id
    everyone = _req(admin, "POST", "/runs", json={"kind": "logical", "reason": "Avant mise à jour"})
    assert everyone.status_code == 201, everyone.text
    assert everyone.json()["scope"] == "platform" and everyone.json()["status"] == "queued"
    one = _req(
        admin, "POST", "/runs", json={"kind": "logical", "scope": "tenant", "tenant_id": str(alpha)}
    )
    assert one.status_code == 201
    assert one.json()["tenant_name"] == "Entreprise alpha"
    # Double audit pour une entreprise : plateforme + journal de l'entreprise.
    assert len(_platform_audit(owner_db, "backup.requested")) == 2
    mirror = owner_db.execute(
        text(
            "SELECT count(*) FROM audit_logs WHERE tenant_id = :t AND action = 'backup.requested'"
        ),
        {"t": alpha},
    ).scalar()
    assert mirror == 1
    bad = _req(
        admin,
        "POST",
        "/runs",
        json={"kind": "physical", "scope": "tenant", "tenant_id": str(alpha)},
    )
    assert bad.status_code == 422 and bad.json()["code"] == "tenant_backup_must_be_logical"
    missing = _req(
        admin,
        "POST",
        "/runs",
        json={"kind": "logical", "scope": "tenant", "tenant_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 404


def test_progress_result_and_errors_are_visible_and_queued_runs_cancellable(
    admin: TestClient, owner_engine: Engine
) -> None:
    run_id = _req(admin, "POST", "/runs", json={"kind": "physical"}).json()["id"]
    cancel = _req(admin, "POST", f"/runs/{run_id}/cancel", json={"reason": "Lancée par erreur"})
    assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
    run_id = _req(admin, "POST", "/runs", json={"kind": "physical"}).json()["id"]
    [claimed] = _agent(owner_engine, "SELECT id FROM backup_claim('test', ARRAY['physical'])")
    assert str(claimed[0]) == run_id
    _agent(
        owner_engine,
        "UPDATE backup_runs SET progress_percent = 40, progress_message = 'pg_basebackup' WHERE "
        "id = :id",
        {"id": run_id},
    )
    running = _req(admin, "GET", f"/runs/{run_id}").json()
    assert (running["status"], running["progress_percent"], running["progress_message"]) == (
        "running",
        40,
        "pg_basebackup",
    )
    assert (
        _req(admin, "POST", f"/runs/{run_id}/cancel", json={"reason": "Trop tard"}).status_code
        == 409
    )
    _agent(
        owner_engine,
        "SELECT backup_finish(:id, false, '{\"x\": 1}'::jsonb, 'espace insuffisant')",
        {"id": run_id},
    )
    done = _req(admin, "GET", f"/runs/{run_id}").json()
    assert (
        done["status"] == "failed"
        and done["error"] == "espace insuffisant"
        and done["result"] == {"x": 1}
    )


def test_run_history_filters_and_company_history(
    admin: TestClient, provision: Any, owner_engine: Engine, owner_db: Session
) -> None:
    alpha = provision("alpha").tenant_id
    beta = provision("beta").tenant_id
    rev = _revision(owner_db)
    _item(owner_engine, alpha, revision=rev)  # sauvegarde globale contenant alpha
    _req(
        admin, "POST", "/runs", json={"kind": "logical", "scope": "tenant", "tenant_id": str(beta)}
    )
    _req(admin, "POST", "/runs", json={"kind": "physical"})
    all_runs = _req(admin, "GET", "/runs").json()
    assert all_runs["total"] == 3
    assert _req(admin, "GET", "/runs", params={"kind": "physical"}).json()["total"] == 1
    assert _req(admin, "GET", "/runs", params={"status": "succeeded"}).json()["total"] == 1
    assert _req(admin, "GET", "/runs", params={"tenant_id": str(alpha)}).json()["total"] == 1
    assert _req(admin, "GET", "/runs", params={"tenant_id": str(beta)}).json()["total"] == 1
    today = datetime.now(UTC).date()
    assert (
        _req(admin, "GET", "/runs", params={"date_from": str(today + timedelta(days=1))}).json()[
            "total"
        ]
        == 0
    )
    items = _req(admin, "GET", "/items", params={"tenant_id": str(alpha)}).json()
    assert items["total"] == 1 and items["items"][0]["restorable"] is True
    assert _req(admin, "GET", "/items", params={"tenant_id": str(beta)}).json()["total"] == 0


# --- Restaurer : double approbation -----------------------------------------------------------


def test_tenant_restore_requires_verification_and_a_second_distinct_admin(
    admin: TestClient, second: TestClient, provision: Any, owner_engine: Engine, owner_db: Session
) -> None:
    alpha = provision("alpha").tenant_id
    item = _item(owner_engine, alpha, revision=_revision(owner_db))
    created = _req(
        admin,
        "POST",
        "/restores",
        json={
            "tenant_id": str(alpha),
            "mode": "logical",
            "source_item_id": str(item),
            "reason": "Erreur de saisie massive",
        },
    )
    assert created.status_code == 201, created.text
    op = created.json()
    assert op["status"] == "requested" and op["actions"]["approve"] is False
    assert op["source_item"]["id"] == str(item)
    # Pas d'approbation avant la vérification sur copie isolée.
    early = _req(
        second, "POST", f"/restores/{op['id']}/approve", json={"reason": "Validé par téléphone"}
    )
    assert early.status_code == 409 and early.json()["code"] == "restore_not_verified"
    # Une seule restauration active par entreprise.
    dup = _req(
        admin,
        "POST",
        "/restores",
        json={
            "tenant_id": str(alpha),
            "mode": "logical",
            "source_item_id": str(item),
            "reason": "Deuxième demande",
        },
    )
    assert dup.status_code == 409 and dup.json()["code"] == "restore_already_active"
    _verify(owner_engine, op["id"])
    mine = _req(admin, "GET", f"/restores/{op['id']}").json()
    assert (
        mine["actions"]["approve"] is False and mine["actions"]["approve_blocked"] == "same_admin"
    )
    same = _req(
        admin, "POST", f"/restores/{op['id']}/approve", json={"reason": "Je valide moi-même"}
    )
    assert same.status_code == 403 and same.json()["code"] == "restore_same_admin"
    other = _req(second, "GET", f"/restores/{op['id']}").json()
    assert other["actions"]["approve"] is True
    approved = _req(
        second,
        "POST",
        f"/restores/{op['id']}/approve",
        json={"reason": "Vérification relue, conforme"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["approved_label"]
    # Journaux : demande et approbation, plateforme ET entreprise.
    for action in ("restore.requested", "restore.approved"):
        assert len(_platform_audit(owner_db, action)) == 1
        assert (
            owner_db.execute(
                text("SELECT count(*) FROM audit_logs WHERE tenant_id = :t AND action = :a"),
                {"t": alpha, "a": action},
            ).scalar()
            == 1
        )


def test_two_distinct_admins_is_a_database_constraint(
    owner_engine: Engine, provision: Any, owner_db: Session, platform_admin: Any
) -> None:
    alpha = provision("alpha").tenant_id
    admin_id = platform_admin()
    item = _item(owner_engine, alpha, revision=_revision(owner_db))
    with owner_engine.begin() as conn:
        op = conn.execute(
            text(
                "INSERT INTO restore_operations (id, scope, tenant_id, mode, source_item_id, "
                "reason, requested_by, requested_label) "
                "VALUES (gen_random_uuid(), 'tenant', :t, 'logical', :i, 'raison valable', :u, "
                "'a') RETURNING id"
            ),
            {"t": alpha, "i": item, "u": admin_id},
        ).scalar_one()
    _verify(owner_engine, str(op))
    with pytest.raises(DBAPIError, match="two_distinct_admins"), owner_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE stockmanager_platform"))
        conn.execute(
            text(
                "UPDATE restore_operations SET status = 'approved', approved_by = :u, approved_at "
                "= now() WHERE id = :id"
            ),
            {"u": admin_id, "id": op},
        )


@pytest.mark.parametrize(
    ("role", "transition"),
    [
        ("stockmanager_platform", "applied"),  # la console n'applique jamais
        ("stockmanager_platform", "verified"),  # ni ne se déclare vérifiée
        ("stockmanager_backup", "approved"),  # l'agent n'approuve jamais
    ],
)
def test_transitions_are_reserved_to_the_right_role(
    owner_engine: Engine,
    provision: Any,
    owner_db: Session,
    platform_admin: Any,
    role: str,
    transition: str,
) -> None:
    alpha = provision("alpha").tenant_id
    admin_id = platform_admin()
    item = _item(owner_engine, alpha, revision=_revision(owner_db))
    with owner_engine.begin() as conn:
        op = conn.execute(
            text(
                "INSERT INTO restore_operations (id, scope, tenant_id, mode, source_item_id, "
                "reason, requested_by, requested_label) "
                "VALUES (gen_random_uuid(), 'tenant', :t, 'logical', :i, 'raison valable', :u, "
                "'a') RETURNING id"
            ),
            {"t": alpha, "i": item, "u": admin_id},
        ).scalar_one()
    with pytest.raises(DBAPIError, match="refusée|permission denied"), owner_engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {role}"))
        conn.execute(
            text("UPDATE restore_operations SET status = :s WHERE id = :id"),
            {"s": transition, "id": op},
        )
    # La demande elle-même est immuable.
    with pytest.raises(DBAPIError, match="immuable"), owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE restore_operations SET source_item_id = NULL, mode = 'pitr' WHERE id = :id"
            ),
            {"id": op},
        )


def test_restore_sources_are_checked_by_the_server(
    admin: TestClient, provision: Any, owner_engine: Engine, owner_db: Session
) -> None:
    alpha = provision("alpha").tenant_id
    beta = provision("beta").tenant_id
    rev = _revision(owner_db)
    of_beta = _item(owner_engine, beta, revision=rev)
    old_schema = _item(owner_engine, alpha, revision="0001")
    failed = _item(owner_engine, alpha, revision=rev, status="failed")

    def ask(item: uuid.UUID) -> Any:
        return _req(
            admin,
            "POST",
            "/restores",
            json={
                "tenant_id": str(alpha),
                "mode": "logical",
                "source_item_id": str(item),
                "reason": "Contrôle des sources",
            },
        )

    assert ask(of_beta).json()["code"] == "source_item_other_tenant"
    assert ask(old_schema).json()["code"] == "source_item_schema_revision_differs"
    assert ask(failed).json()["code"] == "source_item_not_succeeded"
    short = _req(
        admin,
        "POST",
        "/restores",
        json={
            "tenant_id": str(alpha),
            "mode": "logical",
            "source_item_id": str(of_beta),
            "reason": "ok",
        },
    )
    assert short.status_code == 422


def test_pitr_requests_stay_within_the_restorable_window(admin: TestClient, provision: Any) -> None:
    alpha = provision("alpha").tenant_id
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    # Sans statuts de sauvegarde physique : fenêtre inconnue, refus.
    unknown = _req(
        admin,
        "POST",
        "/restores",
        json={
            "tenant_id": str(alpha),
            "mode": "pitr",
            "target_time": past,
            "reason": "Retour à 10 h",
        },
    )
    assert unknown.status_code == 409 and unknown.json()["code"] == "pitr_window_unknown"
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    ahead = _req(
        admin,
        "POST",
        "/restores",
        json={
            "tenant_id": str(alpha),
            "mode": "pitr",
            "target_time": future,
            "reason": "Retour au futur",
        },
    )
    assert ahead.status_code == 422 and ahead.json()["code"] == "target_time_in_future"
    logical_global = _req(
        admin,
        "POST",
        "/restores",
        json={"scope": "platform", "mode": "logical", "reason": "Restauration globale"},
    )
    assert (
        logical_global.status_code == 422
        and logical_global.json()["code"] == "platform_restore_must_be_pitr"
    )


def test_pitr_window_from_status(tmp_path: Path) -> None:
    from app.console.backup_admin import BackupAdminService, window_from_status

    lo = datetime(2026, 11, 1, tzinfo=UTC)
    hi = datetime(2026, 11, 4, 12, tzinfo=UTC)
    window = window_from_status(
        {"storage": {"restorable_from": lo}, "archiving": {"last_archived_at": hi}}
    )
    assert window == {"known": True, "restorable_from": lo, "restorable_to": hi}
    service = BackupAdminService(None, datetime(2026, 11, 4, 13, tzinfo=UTC))  # type: ignore[arg-type]
    service._check_window(datetime(2026, 11, 3, tzinfo=UTC), window)
    with pytest.raises(Exception, match="hors de la fenêtre"):
        service._check_window(datetime(2026, 10, 30, tzinfo=UTC), window)


def test_reject_cancel_expiry_and_rollback(
    admin: TestClient, second: TestClient, provision: Any, owner_engine: Engine, owner_db: Session
) -> None:
    alpha = provision("alpha").tenant_id
    rev = _revision(owner_db)

    def request() -> str:
        item = _item(owner_engine, alpha, revision=rev)
        r = _req(
            admin,
            "POST",
            "/restores",
            json={
                "tenant_id": str(alpha),
                "mode": "logical",
                "source_item_id": str(item),
                "reason": "Restauration de test",
            },
        )
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    op = request()
    _verify(owner_engine, op)
    rejected = _req(
        second, "POST", f"/restores/{op}/reject", json={"reason": "Mauvaise sauvegarde"}
    )
    assert rejected.json()["status"] == "rejected"
    op = request()
    assert (
        _req(admin, "POST", f"/restores/{op}/cancel", json={"reason": "Demande retirée"}).json()[
            "status"
        ]
        == "cancelled"
    )
    op = request()
    _verify(owner_engine, op, expires="-1 minute")
    late = _req(second, "POST", f"/restores/{op}/approve", json={"reason": "Validation tardive"})
    assert late.status_code == 409 and late.json()["code"] == "restore_decision_expired"
    _req(admin, "POST", f"/restores/{op}/cancel", json={"reason": "Expirée, retirée"})
    # Restauration appliquée : retour arrière possible 24 h depuis la sauvegarde préalable.
    op = request()
    _verify(owner_engine, op)
    assert (
        _req(second, "POST", f"/restores/{op}/approve", json={"reason": "Conforme"}).status_code
        == 200
    )
    pre = _item(owner_engine, alpha, revision=rev)
    with owner_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE stockmanager_backup"))
        conn.execute(
            text("UPDATE restore_operations SET status = 'applying' WHERE id = :id"), {"id": op}
        )
        conn.execute(
            text(
                "UPDATE restore_operations SET status = 'applied', applied_at = now(), "
                "pre_restore_item_id = :p, "
                "rollback_deadline = now() + interval '24 hours' WHERE id = :id"
            ),
            {"id": op, "p": pre},
        )
    assert _req(admin, "GET", f"/restores/{op}").json()["actions"]["rollback"] is True
    back = _req(
        admin,
        "POST",
        f"/restores/{op}/rollback",
        json={"reason": "La restauration était une erreur"},
    )
    assert back.status_code == 201, back.text
    assert back.json()["rollback_of"] == op and back.json()["source_item_id"] == str(pre)
    assert back.json()["status"] == "requested"  # même circuit : vérification puis approbation
    _req(
        admin,
        "POST",
        f"/restores/{back.json()['id']}/cancel",
        json={"reason": "Finalement inutile"},
    )
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE restore_operations SET rollback_deadline = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": op},
        )
    expired = _req(
        admin, "POST", f"/restores/{op}/rollback", json={"reason": "Trop tard pour revenir"}
    )
    assert expired.status_code == 409 and expired.json()["code"] == "rollback_unavailable"


def test_platform_restore_is_approved_but_applied_manually(
    admin: TestClient, second: TestClient, owner_engine: Engine, platform_admin: Any
) -> None:
    admin_id = platform_admin(email="third@technova.example", name="Troisième")
    with owner_engine.begin() as conn:
        op = conn.execute(
            text(
                "INSERT INTO restore_operations (id, scope, mode, target_time, reason, "
                "requested_by, requested_label) "
                "VALUES (gen_random_uuid(), 'platform', 'pitr', now() - interval '1 hour', "
                "'incident global', :u, 'c') RETURNING id"
            ),
            {"u": admin_id},
        ).scalar_one()
    _verify(owner_engine, str(op))
    approved = _req(
        second, "POST", f"/restores/{op}/approve", json={"reason": "Procédure globale validée"}
    )
    assert approved.status_code == 200
    assert approved.json()["manual_application"] is True and approved.json()["status"] == "approved"


# --- Supervision ----------------------------------------------------------------------------------


def test_overview_reports_agent_and_schedules(admin: TestClient, owner_engine: Engine) -> None:
    body = _req(admin, "GET", "/overview").json()
    assert body["agent"]["problem"] == "never_seen" and body["level"] == "critical"
    cats = {s["category"]: s for s in body["schedules"]}
    assert cats["physical"]["enabled_frequencies"] == ["weekly"]
    assert cats["physical"]["max_age_seconds"] == 192 * 3600
    _agent(
        owner_engine,
        "INSERT INTO backup_agent_state (id, last_seen_at, agent) VALUES (1, now(), 'agent@db')",
    )
    body = _req(admin, "GET", "/overview").json()
    assert body["agent"]["level"] == "ok"
    assert body["window"]["known"] is False


# --- Verrou d'une entreprise pendant l'application ------------------------------------------------


def test_locked_company_gets_503_and_other_companies_continue(
    provision: Any, api_for: Any, owner_engine: Engine
) -> None:
    alpha = provision("alpha")
    beta = provision("beta")
    a = api_for("owner@alpha.example.com", alpha.tenant_id)
    b = api_for("owner@beta.example.com", beta.tenant_id)
    assert a.get("/tenant").status_code == 200
    op = uuid.uuid4()
    with owner_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE stockmanager_backup"))
        conn.execute(
            text(
                "UPDATE tenants SET restore_locked_at = now(), restore_operation_id = :op WHERE "
                "id = :t"
            ),
            {"op": op, "t": alpha.tenant_id},
        )
    locked = a.get("/tenant")
    assert locked.status_code == 503 and locked.json()["code"] == "tenant_restore_in_progress"
    assert b.get("/tenant").status_code == 200
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tenants SET restore_locked_at = NULL, restore_operation_id = NULL WHERE "
                "id = :t"
            ),
            {"t": alpha.tenant_id},
        )
    assert a.get("/tenant").status_code == 200


# --- Rôles de sauvegarde / restauration -----------------------------------------------------------

EXCLUDED = {
    "subscriptions",
    "subscription_payments",
    "licenses",
    "license_activations",
    "audit_logs",
    "notifications",
    "notification_reads",
}
# Déclencheurs des tables restaurées, relus (ADR-0058 §replica) : UPDATE seulement, sauf le
# miroir des codes-barres (contenu dans l'instantané, contrôlé après chargement). Tout nouveau
# déclencheur doit être examiné avant d'être ajouté ici.
REVIEWED_TRIGGERS = {
    ("catalog_articles", "catalog_article_barcodes_sync"),
    ("catalog_packagings", "catalog_packaging_barcodes_sync"),
    ("onboarding_steps", "onboarding_steps_forward_only"),
    ("restaurant_order_lines", "trg_restaurant_final_state"),
    ("restaurant_orders", "trg_restaurant_final_state"),
    ("sales", "sales_number_immutable"),
    ("sales", "sales_origin_immutable"),
}


def test_every_company_table_is_either_backed_up_or_explicitly_excluded(owner_db: Session) -> None:
    rows = owner_db.execute(
        text(
            "SELECT c.relname, has_table_privilege('stockmanager_tenant_backup', c.oid, 'SELECT'), "
            "has_table_privilege('stockmanager_tenant_restore', c.oid, 'DELETE'), "
            "has_table_privilege('stockmanager_tenant_restore', c.oid, 'INSERT') "
            "FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' "
            "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' "
            "AND c.relrowsecurity AND c.relforcerowsecurity"
        )
    ).all()
    assert len(rows) > 60
    for table, readable, deletable, insertable in rows:
        if table in EXCLUDED:
            assert not readable and not deletable, table
            assert insertable == (table == "audit_logs"), table
        elif table == "document_sequences":
            assert readable and insertable and not deletable
        else:
            assert readable and deletable and insertable, table


def test_restored_tables_carry_only_reviewed_triggers(owner_db: Session) -> None:
    rows = owner_db.execute(
        text(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relforcerowsecurity "
            "AND has_table_privilege('stockmanager_tenant_restore', c.oid, 'DELETE')"
        )
    ).all()
    assert {tuple(r) for r in rows} == REVIEWED_TRIGGERS


def test_backup_roles_have_no_bypass_and_see_nothing_without_context(
    owner_engine: Engine, provision: Any
) -> None:
    provision("alpha")
    with owner_engine.connect() as conn:
        attrs = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname IN "
                "('stockmanager_backup', 'stockmanager_tenant_backup', "
                "'stockmanager_tenant_restore') "
                "ORDER BY 1"
            )
        ).all()
    assert [tuple(a)[1:] for a in attrs] == [(False, False)] * 3
    for role in ("stockmanager_tenant_backup", "stockmanager_tenant_restore"):
        with owner_engine.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            assert conn.execute(text("SELECT count(*) FROM catalog_categories")).scalar() == 0
            assert conn.execute(text("SELECT count(*) FROM sites")).scalar() == 0
    # L'agent : aucune donnée d'entreprise, quelle que soit la requête.
    with pytest.raises(DBAPIError, match="permission denied"), owner_engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE stockmanager_backup"))
        conn.execute(text("SELECT count(*) FROM catalog_categories"))


def test_restore_role_cannot_touch_another_company_even_in_replica_mode(
    owner_engine: Engine, provision: Any
) -> None:
    alpha = provision("alpha").tenant_id
    beta = provision("beta").tenant_id
    with owner_engine.connect() as conn, conn.begin():
        conn.execute(text("SET LOCAL ROLE stockmanager_tenant_restore"))
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(alpha)})
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        # Suppression : seulement ses propres lignes (RLS), jamais celles de beta.
        assert (
            conn.execute(
                text("DELETE FROM catalog_categories WHERE tenant_id = :b"), {"b": beta}
            ).rowcount
            == 0
        )
        with pytest.raises(DBAPIError, match="row-level security"), conn.begin_nested():
            conn.execute(
                text(
                    "INSERT INTO catalog_categories (id, tenant_id, name, is_active) VALUES "
                    "(gen_random_uuid(), :b, 'X', true)"
                ),
                {"b": beta},
            )
        # Contraintes non déclencheurs : appliquées même en mode replica.
        with pytest.raises(DBAPIError, match="null value|violates"), conn.begin_nested():
            conn.execute(
                text(
                    "INSERT INTO catalog_categories (id, tenant_id, name, is_active) VALUES "
                    "(gen_random_uuid(), :a, NULL, true)"
                ),
                {"a": alpha},
            )
        # Tables exclues : jamais écrites.
        with pytest.raises(DBAPIError, match="permission denied"), conn.begin_nested():
            conn.execute(text("DELETE FROM subscriptions"))
        conn.rollback()


def test_apply_script_purge_constants_match_the_code() -> None:
    script = (ROOT / "ops/backup/sm-tenant-apply.sh").read_text(encoding="utf-8")
    assert re.search(rf"^CONTACT_RETENTION_DAYS={CONTACT_RETENTION_DAYS}$", script, re.M)
    assert re.search(rf"^MEMBER_NOTIFICATION_RETENTION_DAYS={RETENTION_DAYS}$", script, re.M)
