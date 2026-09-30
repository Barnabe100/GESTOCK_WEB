"""Phase 3.3-B4 — Rappels d'échéance des abonnements de site (ADR-0036).

Job ``stockmanager notifications run`` : étapes J-30 … J+7 (``SM_RENEWAL_NOTICE_DAYS``), essais
limités à J-5 / J-1 / J0, statuts exclus, idempotence (abonnement, étape, échéance), nouvelle
série quand l'échéance change, pas de rafale après un job manqué, exclusion mutuelle. API :
visibilité par site et par droit, isolation des tenants, état lu / non lu par membre, RLS.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.cli import main
from app.console.audit import CLI_ACTOR
from app.console.notifications import LOCK_KEY, RenewalNoticeJob, due_step
from app.core.config import Settings, parse_notice_days
from app.core.db import set_db_context
from tests.conftest import PASSWORD, Api, _sites_engine, add_site, login

STEPS = (30, 15, 10, 5, 1, 0, -1, -7)
# Échéance fixe : dernier jour couvert = 31/12/2026 (fuseau du Burkina Faso = UTC).
END = datetime(2027, 1, 1, tzinfo=UTC)
LAST_DAY = END - timedelta(days=1)
TEMPORARY = "Provisoire-2026!"


def _at(days_left: int) -> datetime:
    """Instant du job (10 h) quand il reste ``days_left`` jours avant l'échéance."""
    return LAST_DAY.replace(hour=10) - timedelta(days=days_left)


def _set(subscription_id: Any, status: str = "active", end: datetime = END) -> None:
    with _sites_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE subscriptions SET status = :st, current_period_end = :e, "
                "current_period_start = :e - interval '1 month' WHERE id = :s"
            ),
            {"st": status, "e": end, "s": str(subscription_id)},
        )


def _run(platform_engine: Engine, now: datetime, steps: tuple[int, ...] = STEPS) -> Any:
    with Session(platform_engine) as session:
        result = RenewalNoticeJob(session, steps, now, CLI_ACTOR).run()
        session.commit()
    return result


def _rows(owner_db: Session, subscription_id: Any) -> list[tuple[int, str, str]]:
    owner_db.rollback()
    return [
        (r.step, r.status, r.reference_date.isoformat())
        for r in owner_db.execute(
            text(
                "SELECT step, status, reference_date FROM notifications "
                "WHERE subscription_id = :s ORDER BY reference_date, step DESC"
            ),
            {"s": str(subscription_id)},
        )
    ]


@pytest.fixture
def alpha(provision: Any) -> Any:
    return provision("alpha", plan="STANDARD")


@pytest.fixture
def owner(alpha: Any, api_for: Any) -> Api:
    api: Api = api_for("owner@alpha.example.com")
    return api


# --- Configuration -----------------------------------------------------------------------------


def test_notice_days_are_configurable() -> None:
    assert parse_notice_days("30,15,10,5,1,0,-1,-7") == STEPS
    assert parse_notice_days(" 1, 7 ,3") == (7, 3, 1)
    assert parse_notice_days("1,1") == (1,)
    for bad in ("", "a,b", "400", "-90"):
        with pytest.raises(ValueError):
            parse_notice_days(bad)
    assert Settings(renewal_notice_days="10,0").renewal_notice_steps == (10, 0)


def test_due_step() -> None:
    assert due_step(STEPS, 31) is None and due_step(STEPS, -8) is None
    assert [due_step(STEPS, d) for d in (30, 29, 16, 15, 11, 6, 2, 1, 0, -1, -2, -7)] == [
        30, 30, 30, 15, 15, 10, 5, 1, 0, -1, -1, -7,
    ]  # fmt: skip


# --- Étapes ------------------------------------------------------------------------------------


@pytest.mark.parametrize("step", STEPS)
def test_each_step_is_sent_on_its_day(
    alpha: Any, platform_engine: Engine, owner_db: Session, step: int
) -> None:
    _set(alpha.subscription_id)
    # La veille de l'étape, l'étape précédente est due (sauf pour J-30 : aucune).
    previous = step + 1
    before = _run(platform_engine, _at(previous))
    result = _run(platform_engine, _at(step))
    assert result.sent == 1
    rows = _rows(owner_db, alpha.subscription_id)
    assert (step, "SENT", "2026-12-31") in rows
    if step == 30:
        assert before.sent == 0
    data = owner_db.scalar(
        text("SELECT data FROM notifications WHERE subscription_id = :s AND step = :t"),
        {"s": str(alpha.subscription_id), "t": step},
    )
    assert data["days_left"] == step and data["plan_code"] == "STANDARD"


def test_nothing_outside_the_steps(alpha: Any, platform_engine: Engine, owner_db: Session) -> None:
    _set(alpha.subscription_id)
    assert _run(platform_engine, _at(31)).sent == 0
    _set(alpha.subscription_id, status="expired")
    assert _run(platform_engine, _at(-8)).sent == 0
    assert _rows(owner_db, alpha.subscription_id) == []


def test_full_series_is_sent_once(alpha: Any, platform_engine: Engine, owner_db: Session) -> None:
    _set(alpha.subscription_id)
    for days_left in range(31, -9, -1):
        if days_left == -1:
            _set(alpha.subscription_id, status="past_due")
        _run(platform_engine, _at(days_left))
        _run(platform_engine, _at(days_left) + timedelta(hours=5))  # seconde exécution du jour
    rows = _rows(owner_db, alpha.subscription_id)
    assert [(s, st) for s, st, _ in rows] == [(s, "SENT") for s in STEPS]


def test_double_run_is_idempotent(alpha: Any, platform_engine: Engine, owner_db: Session) -> None:
    _set(alpha.subscription_id)
    first = _run(platform_engine, _at(15))
    second = _run(platform_engine, _at(15))
    assert (first.sent, second.sent, second.skipped) == (1, 0, 0)
    assert len(_rows(owner_db, alpha.subscription_id)) == 2  # J-15 envoyée, J-30 notée


def test_missed_job_sends_only_the_latest_due_step(
    alpha: Any, platform_engine: Engine, owner_db: Session
) -> None:
    _set(alpha.subscription_id)
    _run(platform_engine, _at(30))
    # Job arrêté 26 jours : au retour, seule J-5 (la plus récente due) est envoyée.
    result = _run(platform_engine, _at(4))
    assert (result.sent, result.skipped) == (1, 2)
    assert [(s, st) for s, st, _ in _rows(owner_db, alpha.subscription_id)] == [
        (30, "SENT"),
        (15, "SKIPPED"),
        (10, "SKIPPED"),
        (5, "SENT"),
    ]


def test_changed_end_date_starts_a_new_series(
    alpha: Any, platform_engine: Engine, owner_db: Session
) -> None:
    _set(alpha.subscription_id)
    _run(platform_engine, _at(5))
    # Renouvellement : l'échéance recule d'un an ; l'ancienne série s'arrête.
    _set(alpha.subscription_id, end=END.replace(year=2028))
    assert _run(platform_engine, _at(1)).sent == 0
    later = datetime(2027, 12, 1, 10, tzinfo=UTC)  # J-30 de la nouvelle échéance
    assert _run(platform_engine, later).sent == 1
    rows = _rows(owner_db, alpha.subscription_id)
    assert (30, "SENT", "2027-12-31") in rows
    assert (5, "SENT", "2026-12-31") in rows


# --- Statuts -----------------------------------------------------------------------------------


def test_trial_gets_only_short_steps(
    provision: Any, platform_engine: Engine, owner_db: Session
) -> None:
    trial = provision("gamma", plan="STANDARD", trial_days=14)
    _set(trial.subscription_id, status="trial")
    sent = [d for d in STEPS if _run(platform_engine, _at(d)).sent]
    assert sent == [5, 1, 0]
    data = owner_db.scalar(
        text("SELECT data FROM notifications WHERE subscription_id = :s AND step = 0"),
        {"s": str(trial.subscription_id)},
    )
    assert data["trial"] is True


@pytest.mark.parametrize("status", ["pending_activation", "suspended", "cancelled"])
def test_excluded_statuses(
    alpha: Any, platform_engine: Engine, owner_db: Session, status: str
) -> None:
    _set(alpha.subscription_id, status=status)
    for days_left in (30, 0, -1):
        _run(platform_engine, _at(days_left))
    assert _rows(owner_db, alpha.subscription_id) == []


def test_suspended_tenant_is_excluded(
    alpha: Any, platform_engine: Engine, owner_db: Session
) -> None:
    _set(alpha.subscription_id)
    with _sites_engine().begin() as conn:
        conn.execute(
            text("UPDATE tenants SET status = 'suspended' WHERE id = :t"),
            {"t": str(alpha.tenant_id)},
        )
    _run(platform_engine, _at(0))
    assert _rows(owner_db, alpha.subscription_id) == []


def test_grace_and_expired_are_notified(
    alpha: Any, platform_engine: Engine, owner_db: Session
) -> None:
    _set(alpha.subscription_id, status="past_due")
    _run(platform_engine, _at(-1))
    _set(alpha.subscription_id, status="expired")
    _run(platform_engine, _at(-7))
    rows = [(s, st) for s, st, _ in _rows(owner_db, alpha.subscription_id)]
    # Étapes antérieures jamais envoyées (abonnement arrivé en grâce) : notées, pas envoyées.
    assert rows == [(s, "SKIPPED") for s in (30, 15, 10, 5, 1, 0)] + [(-1, "SENT"), (-7, "SENT")]


# --- Exclusion mutuelle et CLI -----------------------------------------------------------------


def test_concurrent_run_stops_without_writing(
    alpha: Any, platform_engine: Engine, owner_db: Session
) -> None:
    _set(alpha.subscription_id)
    with Session(platform_engine) as holder:
        assert holder.scalar(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
        result = _run(platform_engine, _at(0))
        assert result.locked and result.sent == 0
        holder.rollback()
    assert _rows(owner_db, alpha.subscription_id) == []
    assert _run(platform_engine, _at(0)).sent == 1


def test_parallel_runs_create_each_notice_once(
    alpha: Any, platform_engine: Engine, owner_db: Session
) -> None:
    _set(alpha.subscription_id)
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            _run(platform_engine, _at(10))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    rows = _rows(owner_db, alpha.subscription_id)
    assert [(s, st) for s, st, _ in rows] == [(30, "SKIPPED"), (15, "SKIPPED"), (10, "SENT")]


def test_cli_run(
    alpha: Any, settings: Settings, owner_db: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    _set(alpha.subscription_id)
    assert main(["notifications", "run", "--now", _at(1).isoformat()], settings) == 0
    assert "rappels envoyés : 1" in capsys.readouterr().out
    assert main(["notifications", "run", "--now", _at(1).isoformat()], settings) == 0
    assert "rappels envoyés : 0" in capsys.readouterr().out
    audit = owner_db.execute(
        text("SELECT count(*) FROM platform_audit_logs WHERE action = 'notifications.run'")
    ).scalar()
    assert audit == 2


# --- API : visibilité, lu / non lu, isolation --------------------------------------------------


def _member(
    owner: Api, client: Any, email: str, roles: list[dict[str, Any]], sites: list[str]
) -> Api:
    response = owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": TEMPORARY,
            "roles": roles,
            "site_ids": sites,
        },
    )
    assert response.status_code == 201, response.text
    first = login(client, email, TEMPORARY)
    Api(client, first.json()["access_token"]).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )
    return Api(client, login(client, email, PASSWORD).json()["access_token"])


@pytest.fixture
def two_sites(alpha: Any, owner: Api, platform_engine: Engine) -> Any:
    site_b = add_site(owner, "Dépôt", "DEP", "warehouse").json()
    subscriptions = {s["site"]["id"]: s["id"] for s in owner.get("/subscriptions").json()}
    for subscription_id in subscriptions.values():
        _set(subscription_id)
    _run(platform_engine, _at(15))
    roles = {r["template_code"] or r["name"]: r["id"] for r in owner.get("/roles").json()}
    return str(alpha.site_id), site_b["id"], roles


def test_owner_sees_notifications_of_all_sites(owner: Api, two_sites: Any) -> None:
    site_a, site_b, _ = two_sites
    page = owner.get("/notifications").json()
    assert page["total"] == 2
    assert {n["site"]["id"] for n in page["items"]} == {site_a, site_b}
    assert all(n["step"] == 15 and n["read_at"] is None for n in page["items"])
    assert owner.get("/notifications/unread-count").json() == {"unread": 2}
    only_b = owner.get("/notifications", params={"site_id": site_b}).json()
    assert only_b["total"] == 1
    # Site sélectionné : seulement ce site.
    scoped = Api(owner.client, owner.token, site_id=uuid.UUID(site_a))
    assert [n["site"]["id"] for n in scoped.get("/notifications").json()["items"]] == [site_a]


def test_site_restricted_member_sees_only_their_site(
    owner: Api, client: Any, two_sites: Any
) -> None:
    site_a, site_b, roles = two_sites
    # Droit sur toute l'entreprise, mais accès au seul site B.
    member = _member(
        owner, client, "awa@alpha.example.com", [{"role_id": roles["administrator"]}], [site_b]
    )
    items = member.get("/notifications").json()["items"]
    assert [n["site"]["id"] for n in items] == [site_b]
    hidden = owner.get("/notifications", params={"site_id": site_a}).json()["items"][0]
    assert member.post(f"/notifications/{hidden['id']}/read").status_code == 404
    assert member.get("/notifications", params={"site_id": site_a}).json()["total"] == 0
    assert member.get("/notifications/unread-count").json() == {"unread": 1}


def test_site_scoped_role_sees_its_site_once_selected(
    owner: Api, client: Any, two_sites: Any
) -> None:
    site_a, site_b, roles = two_sites
    member = _member(
        owner,
        client,
        "ali@alpha.example.com",
        [{"role_id": roles["administrator"], "site_id": site_b}],
        [site_b],
    )
    # Rôle limité au site B : le droit ne vaut qu'une fois ce site sélectionné.
    assert member.get("/notifications").status_code == 403
    on_b = Api(member.client, member.token, site_id=uuid.UUID(site_b))
    assert [n["site"]["id"] for n in on_b.get("/notifications").json()["items"]] == [site_b]
    on_a = Api(member.client, member.token, site_id=uuid.UUID(site_a))
    assert on_a.get("/notifications").status_code in (403, 404)


def test_notifications_follow_the_subscription_view_right(
    owner: Api, client: Any, two_sites: Any
) -> None:
    site_a, _, roles = two_sites
    for email, role in (("paul@alpha.example.com", "manager"), ("ali@alpha.example.com", "seller")):
        member = _member(owner, client, email, [{"role_id": roles[role]}], [site_a])
        assert member.get("/notifications").status_code == 403
        assert member.get("/notifications/unread-count").status_code == 403
    # Le Consultant consulte l'abonnement (« *.view ») : il voit donc les rappels.
    viewer = _member(
        owner, client, "zoe@alpha.example.com", [{"role_id": roles["viewer"]}], [site_a]
    )
    assert [n["site"]["id"] for n in viewer.get("/notifications").json()["items"]] == [site_a]


def test_read_state_is_per_member(owner: Api, client: Any, two_sites: Any) -> None:
    site_a, site_b, roles = two_sites
    other = _member(
        owner, client, "awa@alpha.example.com", [{"role_id": roles["administrator"]}],
        [site_a, site_b],
    )  # fmt: skip
    first = owner.get("/notifications").json()["items"][0]
    assert owner.post(f"/notifications/{first['id']}/read").status_code == 204
    assert owner.post(f"/notifications/{first['id']}/read").status_code == 204  # idempotent
    assert owner.get("/notifications/unread-count").json() == {"unread": 1}
    unread = owner.get("/notifications", params={"unread": True}).json()
    assert unread["total"] == 1 and unread["items"][0]["id"] != first["id"]
    # L'historique reste consultable : la notification lue est toujours listée.
    assert owner.get("/notifications").json()["total"] == 2
    assert other.get("/notifications/unread-count").json() == {"unread": 2}
    assert other.post("/notifications/read-all").status_code == 204
    assert other.get("/notifications/unread-count").json() == {"unread": 0}
    assert owner.get("/notifications/unread-count").json() == {"unread": 1}


def test_skipped_steps_are_never_shown(
    owner: Api, alpha: Any, platform_engine: Engine, owner_db: Session
) -> None:
    _set(alpha.subscription_id)
    _run(platform_engine, _at(1))
    assert len(_rows(owner_db, alpha.subscription_id)) == 5  # 4 notées + J-1
    assert [n["step"] for n in owner.get("/notifications").json()["items"]] == [1]


def test_tenants_are_isolated(
    provision: Any, api_for: Any, owner: Api, two_sites: Any, app_engine: Engine
) -> None:
    beta = provision("beta", plan="STANDARD")
    owner_b = api_for("owner@beta.example.com")
    assert owner_b.get("/notifications").json()["total"] == 0
    foreign = owner.get("/notifications").json()["items"][0]
    assert owner_b.post(f"/notifications/{foreign['id']}/read").status_code == 404
    with Session(app_engine) as session:
        set_db_context(session, tenant_id=beta.tenant_id, user_id=None)
        assert session.scalar(text("SELECT count(*) FROM notifications")) == 0


def test_application_role_cannot_write_notifications(
    alpha: Any, owner: Api, two_sites: Any, app_engine: Engine, owner_db: Session
) -> None:
    owner_db.rollback()
    owner_user = owner_db.scalar(
        text("SELECT id FROM users WHERE email = 'owner@alpha.example.com'")
    )
    notification = owner.get("/notifications").json()["items"][0]["id"]
    with Session(app_engine) as session:
        set_db_context(session, tenant_id=alpha.tenant_id, user_id=owner_user)
        for statement in (
            "UPDATE notifications SET step = 99",
            "DELETE FROM notifications",
            "INSERT INTO notifications (id, tenant_id, kind, step, reference_date, status, data, "
            "created_at) VALUES (gen_random_uuid(), current_setting('app.tenant_id')::uuid, "
            "'x', 1, current_date, 'SENT', '{}', now())",
            "DELETE FROM notification_reads",
        ):
            with pytest.raises(ProgrammingError):
                session.execute(text(statement))
            session.rollback()
            set_db_context(session, tenant_id=alpha.tenant_id, user_id=owner_user)
        # Une lecture au nom d'un autre membre est refusée par la politique RLS.
        with pytest.raises(ProgrammingError):
            session.execute(
                text(
                    "INSERT INTO notification_reads (tenant_id, notification_id, user_id, "
                    "read_at) VALUES (:t, :n, gen_random_uuid(), now())"
                ),
                {"t": str(alpha.tenant_id), "n": notification},
            )
