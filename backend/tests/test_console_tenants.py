"""Phase 3.2-G — Console TechNova : tenants et abonnements (ADR-0031).

Liste et détail des entreprises (métadonnées plateforme seulement), statut du tenant distinct du
statut de l'abonnement, suspension / réactivation, activation manuelle transitoire (aucun
paiement), prolongation, changement de plan (prix figé), idempotence sous verrou, double audit
transactionnel (plateforme + tenant), droits SQL minimaux.
"""

import threading
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor
from app.console.tenants import TenantAdminService
from app.core.errors import ConflictError
from app.platform.audit.service import RequestMeta
from app.platform.registry import get_registry
from app.shared.clock import utcnow
from tests.conftest import (
    CONSOLE_HEADERS,
    CONSOLE_PREFIX,
    PASSWORD,
    PLATFORM_ADMIN_EMAIL,
    Api,
    console_login,
    login,
)

REASON = "Régularisation commerciale TechNova"


@pytest.fixture(autouse=True)
def priced_plans(owner_db: Session) -> Iterator[None]:
    """Tarifs mensuels ouverts (prix figés vérifiables), remis à zéro ensuite."""
    owner_db.execute(
        text(
            "UPDATE plans SET monthly_price_enabled = true, currency = 'XOF', monthly_price = "
            "CASE code WHEN 'STANDARD' THEN 10000 ELSE 25000 END"
        )
    )
    owner_db.commit()
    yield
    owner_db.execute(
        text(
            "UPDATE plans SET monthly_price_enabled = false, monthly_price = NULL, currency = NULL"
        )
    )
    owner_db.commit()


@pytest.fixture
def admin(console: TestClient, platform_admin: Any) -> TestClient:
    platform_admin()
    assert console_login(console).status_code == 200
    return console


def _get(c: TestClient, path: str, **kw: Any) -> Any:
    return c.get(f"{CONSOLE_PREFIX}{path}", **kw)


def _post(c: TestClient, path: str, body: dict[str, Any] | None = None) -> Any:
    return c.post(f"{CONSOLE_PREFIX}{path}", json=body or {}, headers=CONSOLE_HEADERS)


def _sql(owner_db: Session, sql: str, **params: Any) -> Any:
    result = owner_db.execute(text(sql), params)
    owner_db.commit()
    return result


def _pending(owner_db: Session, tenant_id: uuid.UUID) -> None:
    """État laissé par l'inscription publique sans essai (ADR-0025)."""
    _sql(
        owner_db,
        "UPDATE subscriptions SET status = 'pending_activation', current_period_start = now(), "
        "current_period_end = now() WHERE tenant_id = :t",
        t=tenant_id,
    )


def _platform_audit(owner_db: Session, action: str, tenant_id: uuid.UUID) -> list[Any]:
    return list(
        owner_db.execute(
            text(
                "SELECT id, actor_user_id, actor_label, target_type, target_id, tenant_id, "
                "before, after, reason, data FROM platform_audit_logs "
                "WHERE action = :a AND tenant_id = :t ORDER BY occurred_at"
            ),
            {"a": action, "t": tenant_id},
        ).all()
    )


def _tenant_audit(owner_db: Session, action: str, tenant_id: uuid.UUID) -> list[Any]:
    return list(
        owner_db.execute(
            text(
                "SELECT user_id, entity_type, entity_id, data FROM audit_logs "
                "WHERE action = :a AND tenant_id = :t ORDER BY occurred_at"
            ),
            {"a": action, "t": tenant_id},
        ).all()
    )


def _subscription(owner_db: Session, tenant_id: uuid.UUID) -> Any:
    return owner_db.execute(
        text("SELECT * FROM subscriptions WHERE tenant_id = :t"), {"t": tenant_id}
    ).one()


# --- Liste et détail ------------------------------------------------------------------------


def test_tenant_list_is_paginated_searchable_filtered_and_sorted(
    admin: TestClient, provision: Any
) -> None:
    alpha = provision("alpha", plan="ENTREPRISE")
    beta = provision("beta", plan="STANDARD", profile="restaurant.maquis")
    provision("gamma", plan="STANDARD", trial_days=14)

    page = _get(admin, "/tenants").json()
    assert page["total"] == 3
    assert [t["name"] for t in page["items"]] == [
        "Entreprise alpha",
        "Entreprise beta",
        "Entreprise gamma",
    ]
    first = page["items"][0]
    assert first["id"] == str(alpha.tenant_id)
    assert (first["plan_code"], first["status"], first["sites"], first["users"]) == (
        "ENTREPRISE",
        "active",
        1,
        1,
    )
    assert first["effective_status"] == "active"
    # Métadonnées plateforme seulement.
    assert not {"email", "phone", "address", "tax_id"} & set(first)

    paged = _get(admin, "/tenants", params={"limit": 2, "offset": 2}).json()
    assert (paged["total"], len(paged["items"])) == (3, 1)
    assert _get(admin, "/tenants", params={"search": "BET"}).json()["total"] == 1
    assert _get(admin, "/tenants", params={"search": "beta"}).json()["items"][0]["id"] == str(
        beta.tenant_id
    )
    assert _get(admin, "/tenants", params={"plan_code": "STANDARD"}).json()["total"] == 2
    trial = _get(admin, "/tenants", params={"subscription_status": "trial"}).json()
    assert [t["name"] for t in trial["items"]] == ["Entreprise gamma"]
    assert _get(admin, "/tenants", params={"status": "suspended"}).json()["total"] == 0
    newest = _get(admin, "/tenants", params={"sort": "-created_at"}).json()["items"]
    assert newest[0]["name"] == "Entreprise gamma"
    invalid = _get(admin, "/tenants", params={"sort": "email"})
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_sort"


def test_tenant_detail_counters_limits_and_subscription(
    admin: TestClient, provision: Any, api_for: Any
) -> None:
    tenant = provision("alpha", plan="STANDARD")
    owner: Api = api_for("owner@alpha.example.com")
    created = owner.post(
        "/members",
        json={"email": "vendeur@alpha.example.com", "full_name": "V", "password": "Tempo-2026-x"},
    )
    assert created.status_code == 201, created.text

    detail = _get(admin, f"/tenants/{tenant.tenant_id}").json()
    assert (detail["country_code"], detail["country_name"]) == ("BF", "Burkina Faso")
    assert (detail["currency"], detail["timezone"]) == ("XOF", "Africa/Ouagadougou")
    assert detail["usage"] == {
        "max_sites": {"used": 1, "limit": 1},
        "max_users": {"used": 2, "limit": 5},
    }
    subscription = detail["subscription"]
    assert (subscription["plan_code"], subscription["status"]) == ("STANDARD", "active")
    assert subscription["billing_period"] == "monthly"
    assert (subscription["price_at_subscription"], subscription["currency_at_subscription"]) == (
        "10000.00",
        "XOF",
    )
    assert subscription["grace_days"] == 7
    actions = detail["actions"]
    assert actions["can_suspend"] and not actions["can_reactivate"]
    assert actions["can_extend"] and not actions["can_activate"]
    assert [p["code"] for p in actions["available_plans"]] == ["ENTREPRISE"]
    assert not {"email", "phone", "address", "tax_id", "trade_register"} & set(detail)

    missing = _get(admin, f"/tenants/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "tenant_not_found"
    assert _get(admin, "/tenants/pas-un-uuid").status_code == 422


# --- Statut du tenant : suspension, réactivation ---------------------------------------------


def test_suspension_and_reactivation_with_double_audit(
    admin: TestClient, provision: Any, api_for: Any, client: TestClient, owner_db: Session
) -> None:
    tenant = provision("alpha")
    owner: Api = api_for("owner@alpha.example.com")
    assert owner.get("/sites").status_code == 200

    missing_reason = _post(admin, f"/tenants/{tenant.tenant_id}/suspend", {"reason": "   "})
    assert missing_reason.status_code == 422
    suspended = _post(admin, f"/tenants/{tenant.tenant_id}/suspend", {"reason": REASON})
    assert suspended.status_code == 200, suspended.text
    body = suspended.json()
    assert body["status"] == "suspended"
    # Statut du tenant ≠ statut de l'abonnement.
    assert body["subscription"]["status"] == "active"
    assert body["actions"]["can_reactivate"] and not body["actions"]["can_suspend"]

    blocked = owner.get("/sites")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "tenant_suspended"
    # Données conservées.
    assert _get(admin, f"/tenants/{tenant.tenant_id}").json()["sites"] == 1
    again = _post(admin, f"/tenants/{tenant.tenant_id}/suspend", {"reason": REASON})
    assert again.status_code == 409
    assert again.json()["code"] == "tenant_already_suspended"
    assert len(_platform_audit(owner_db, "tenant.suspended", tenant.tenant_id)) == 1

    reactivated = _post(admin, f"/tenants/{tenant.tenant_id}/reactivate", {"reason": "Réglé"})
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"
    relogged = Api(
        client, login(client, "owner@alpha.example.com", PASSWORD).json()["access_token"]
    )
    assert relogged.get("/sites").status_code == 200
    twice = _post(admin, f"/tenants/{tenant.tenant_id}/reactivate", {"reason": "Réglé"})
    assert twice.status_code == 409
    assert twice.json()["code"] == "tenant_not_suspended"

    platform = _platform_audit(owner_db, "tenant.suspended", tenant.tenant_id)[0]
    assert platform.actor_label == PLATFORM_ADMIN_EMAIL and platform.actor_user_id is not None
    assert (platform.target_type, platform.target_id) == ("tenant", str(tenant.tenant_id))
    assert (platform.before, platform.after) == ({"status": "active"}, {"status": "suspended"})
    assert platform.reason == REASON
    mirror = _tenant_audit(owner_db, "tenant.suspended", tenant.tenant_id)
    assert len(mirror) == 1
    assert mirror[0].user_id is None
    assert mirror[0].data["actor"] == "technova"
    assert mirror[0].data["reason"] == REASON
    assert mirror[0].data["platform_audit_id"] == str(platform.id)
    assert PLATFORM_ADMIN_EMAIL not in str(mirror[0].data)
    # Le journal du tenant montre l'action TechNova à l'entreprise.
    actions = [e["action"] for e in relogged.get("/audit-logs").json()["items"]]
    assert {"tenant.suspended", "tenant.reactivated"} <= set(actions)


# --- Abonnement : activation, prolongation, expiration, changement de plan --------------------


def test_manual_activation_is_transitional_idempotent_and_audited(
    admin: TestClient, provision: Any, api_for: Any, owner_db: Session
) -> None:
    tenant = provision("alpha", plan="STANDARD")
    _pending(owner_db, tenant.tenant_id)
    owner: Api = api_for("owner@alpha.example.com")
    restricted = owner.post("/catalog/categories", json={"name": "Boissons"})
    assert restricted.status_code == 403
    assert restricted.json()["code"] == "subscription_restricted"

    detail = _get(admin, f"/tenants/{tenant.tenant_id}").json()
    assert detail["subscription"]["effective_status"] == "pending_activation"
    assert detail["actions"]["can_activate"] and not detail["actions"]["can_extend"]
    start = date.fromisoformat(detail["actions"]["activation_start"])
    end = date.fromisoformat(detail["actions"]["activation_end"])
    assert end > start

    activated = _post(
        admin, f"/tenants/{tenant.tenant_id}/subscription/activate", {"reason": REASON}
    )
    assert activated.status_code == 200, activated.text
    subscription = activated.json()["subscription"]
    assert subscription["status"] == subscription["effective_status"] == "active"
    # Africa/Ouagadougou = UTC : minuit local.
    assert subscription["current_period_end"].startswith(end.isoformat())
    assert subscription["price_at_subscription"] == "10000.00"  # prix figé inchangé
    assert owner.post("/catalog/categories", json={"name": "Boissons"}).status_code == 201

    replay = _post(admin, f"/tenants/{tenant.tenant_id}/subscription/activate", {"reason": REASON})
    assert replay.status_code == 409
    assert replay.json()["code"] == "subscription_not_activable"
    entries = _platform_audit(owner_db, "subscription.manually_activated", tenant.tenant_id)
    assert len(entries) == 1
    assert entries[0].before["status"] == "pending_activation"
    assert entries[0].after["status"] == "active"
    assert entries[0].data["payment_confirmed"] is False
    mirror = _tenant_audit(owner_db, "subscription.manually_activated", tenant.tenant_id)
    assert len(mirror) == 1 and mirror[0].data["transitional"] is True
    # Aucun paiement créé (3.3-A).
    assert owner_db.scalar(text("SELECT count(*) FROM payments")) == 0


@pytest.mark.parametrize(
    ("offset_start", "offset_end", "code"),
    [
        (0, -1, "invalid_period"),  # échéance avant le début
        (-40, -10, "invalid_period"),  # période déjà échue
        (0, 800, "period_too_long"),
    ],
)
def test_activation_dates_are_validated(
    admin: TestClient,
    provision: Any,
    owner_db: Session,
    offset_start: int,
    offset_end: int,
    code: str,
) -> None:
    tenant = provision("alpha")
    _pending(owner_db, tenant.tenant_id)
    today = utcnow().date()
    response = _post(
        admin,
        f"/tenants/{tenant.tenant_id}/subscription/activate",
        {
            "reason": REASON,
            "period_start": (today + timedelta(days=offset_start)).isoformat(),
            "period_end": (today + timedelta(days=offset_end)).isoformat(),
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert _subscription(owner_db, tenant.tenant_id).status == "pending_activation"
    assert not _platform_audit(owner_db, "subscription.manually_activated", tenant.tenant_id)


def test_concurrent_activations_produce_a_single_activation(
    platform_engine: Engine, provision: Any, owner_db: Session, platform_admin: Any
) -> None:
    tenant = provision("alpha")
    _pending(owner_db, tenant.tenant_id)
    admin_id = platform_admin()
    actor = PlatformActor(user_id=admin_id, label=PLATFORM_ADMIN_EMAIL)
    outcomes: list[str] = []
    first_locked = threading.Event()

    def activate(hold: bool) -> None:
        with Session(platform_engine) as db:
            service = TenantAdminService(db, get_registry(), utcnow())
            try:
                service.activate(
                    tenant.tenant_id,
                    period_start=None,
                    period_end_on=None,
                    reason=REASON,
                    actor=actor,
                    meta=RequestMeta(),
                )
                if hold:
                    first_locked.set()
                    threading.Event().wait(0.5)  # le second attend le verrou
                db.commit()
                outcomes.append("activated")
            except ConflictError as exc:
                outcomes.append(exc.code)

    first = threading.Thread(target=activate, args=(True,))
    first.start()
    assert first_locked.wait(5)
    second = threading.Thread(target=activate, args=(False,))
    second.start()
    first.join()
    second.join()
    assert sorted(outcomes) == ["activated", "subscription_not_activable"]
    assert len(_platform_audit(owner_db, "subscription.manually_activated", tenant.tenant_id)) == 1
    assert len(_tenant_audit(owner_db, "subscription.manually_activated", tenant.tenant_id)) == 1


def test_expiration_and_extension(
    admin: TestClient, provision: Any, api_for: Any, owner_db: Session
) -> None:
    tenant = provision("alpha", plan="STANDARD")
    owner: Api = api_for("owner@alpha.example.com")
    # Échéance dépassée depuis 10 jours (grâce STANDARD : 7 jours) → expiré.
    _sql(
        owner_db,
        "UPDATE subscriptions SET current_period_start = now() - interval '40 days', "
        "current_period_end = now() - interval '10 days' WHERE tenant_id = :t",
        t=tenant.tenant_id,
    )
    detail = _get(admin, f"/tenants/{tenant.tenant_id}").json()
    assert detail["subscription"]["status"] == "active"
    assert detail["subscription"]["effective_status"] == "expired"
    expired = _get(admin, "/tenants", params={"subscription_status": "expired"}).json()
    assert [t["id"] for t in expired["items"]] == [str(tenant.tenant_id)]
    assert owner.get("/sites").status_code == 200  # consultation autorisée
    assert owner.post("/catalog/categories", json={"name": "X"}).status_code == 403

    # Dans le délai de grâce → échéance dépassée (même règle en SQL et en Python).
    _sql(
        owner_db,
        "UPDATE subscriptions SET current_period_end = now() - interval '2 days' "
        "WHERE tenant_id = :t",
        t=tenant.tenant_id,
    )
    listed = _get(admin, "/tenants").json()["items"][0]["effective_status"]
    assert (
        listed
        == _get(admin, f"/tenants/{tenant.tenant_id}").json()["subscription"]["effective_status"]
        == "past_due"
    )
    _sql(
        owner_db,
        "UPDATE subscriptions SET current_period_end = now() - interval '10 days' "
        "WHERE tenant_id = :t",
        t=tenant.tenant_id,
    )

    new_end = (utcnow() + timedelta(days=60)).date()
    extended = _post(
        admin,
        f"/tenants/{tenant.tenant_id}/subscription/extend",
        {"period_end": new_end.isoformat(), "reason": REASON},
    )
    assert extended.status_code == 200, extended.text
    subscription = extended.json()["subscription"]
    assert subscription["effective_status"] == "active"
    assert subscription["current_period_end"].startswith(new_end.isoformat())
    # Période échue : elle repart du jour même.
    assert subscription["current_period_start"].startswith(utcnow().date().isoformat())
    assert owner.post("/catalog/categories", json={"name": "X"}).status_code == 201

    shorter = _post(
        admin,
        f"/tenants/{tenant.tenant_id}/subscription/extend",
        {"period_end": (new_end - timedelta(days=1)).isoformat(), "reason": REASON},
    )
    assert shorter.status_code == 422
    assert shorter.json()["code"] == "invalid_period"
    entry = _platform_audit(owner_db, "subscription.extended", tenant.tenant_id)
    assert len(entry) == 1
    assert entry[0].after["current_period_end"].startswith(new_end.isoformat())

    _pending(owner_db, tenant.tenant_id)
    refused = _post(
        admin,
        f"/tenants/{tenant.tenant_id}/subscription/extend",
        {"period_end": new_end.isoformat(), "reason": REASON},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "subscription_not_extendable"


def test_plan_change_freezes_the_new_price_and_keeps_old_snapshots(
    admin: TestClient, provision: Any, api_for: Any, owner_db: Session
) -> None:
    tenant = provision("alpha", plan="STANDARD")
    other = provision("beta", plan="STANDARD")
    owner: Api = api_for("owner@alpha.example.com")
    assert "stock.transfers" not in owner.get("/me/capabilities").json()["features"]
    # Nouveau tarif du plan : jamais rétroactif.
    _sql(owner_db, "UPDATE plans SET monthly_price = 15000 WHERE code = 'STANDARD'")
    assert _subscription(owner_db, tenant.tenant_id).price_at_subscription == Decimal("10000.00")

    changed = _post(
        admin,
        f"/tenants/{tenant.tenant_id}/subscription/change-plan",
        {"plan_code": "ENTREPRISE", "reason": REASON},
    )
    assert changed.status_code == 200, changed.text
    subscription = changed.json()["subscription"]
    assert subscription["plan_code"] == "ENTREPRISE"
    assert subscription["price_at_subscription"] == "25000.00"
    assert changed.json()["usage"]["max_sites"]["limit"] is None
    assert "stock.transfers" in owner.get("/me/capabilities").json()["features"]
    # Autre abonnement : prix figé inchangé.
    assert _subscription(owner_db, other.tenant_id).price_at_subscription == Decimal("10000.00")

    entry = _platform_audit(owner_db, "subscription.plan_changed", tenant.tenant_id)[0]
    assert entry.before == {
        "plan_code": "STANDARD",
        "price_at_subscription": "10000.00",
        "currency_at_subscription": "XOF",
    }
    assert entry.after["plan_code"] == "ENTREPRISE"
    mirror = _tenant_audit(owner_db, "subscription.plan_changed", tenant.tenant_id)[0]
    assert (mirror.data["previous_plan"], mirror.data["plan"]) == ("STANDARD", "ENTREPRISE")
    assert mirror.data["actor"] == "technova"

    for body, code in (
        ({"plan_code": "ENTREPRISE", "reason": REASON}, "plan_unchanged"),
        ({"plan_code": "INCONNU", "reason": REASON}, "unknown_plan"),
    ):
        refused = _post(admin, f"/tenants/{tenant.tenant_id}/subscription/change-plan", body)
        assert refused.status_code == 422
        assert refused.json()["code"] == code
    extra = _post(
        admin,
        f"/tenants/{tenant.tenant_id}/subscription/change-plan",
        {"plan_code": "STANDARD", "reason": REASON, "price_at_subscription": "1"},
    )
    assert extra.status_code == 422  # aucun champ financier accepté du client
    assert extra.json()["code"] == "validation_error"


# --- Audit : transaction, append-only ---------------------------------------------------------


@pytest.mark.parametrize(
    "failing", ["app.console.tenants.record_audit", "app.console.tenants.record_platform_audit"]
)
def test_action_and_both_audits_are_atomic(
    admin: TestClient,
    provision: Any,
    owner_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    tenant = provision("alpha")

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("écriture d'audit impossible")

    monkeypatch.setattr(failing, broken)
    with pytest.raises(RuntimeError):
        _post(admin, f"/tenants/{tenant.tenant_id}/suspend", {"reason": REASON})
    status = owner_db.scalar(
        text("SELECT status FROM tenants WHERE id = :t"), {"t": tenant.tenant_id}
    )
    assert status == "active"
    assert not _platform_audit(owner_db, "tenant.suspended", tenant.tenant_id)
    assert not _tenant_audit(owner_db, "tenant.suspended", tenant.tenant_id)


def test_mirror_entries_are_insert_only_for_the_platform_role(
    admin: TestClient, provision: Any, platform_engine: Engine, owner_db: Session
) -> None:
    tenant = provision("alpha")
    assert (
        _post(admin, f"/tenants/{tenant.tenant_id}/suspend", {"reason": REASON}).status_code == 200
    )
    with platform_engine.connect() as conn:
        for sql in (
            "SELECT * FROM audit_logs",
            "UPDATE audit_logs SET action = 'x'",
            "DELETE FROM audit_logs",
            "DELETE FROM platform_audit_logs",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(sql))
            conn.rollback()
        # Une entrée miroir ne porte jamais d'utilisateur et toujours un tenant.
        for tenant_value, user_value in (
            ("NULL", "NULL"),
            (f"'{tenant.tenant_id}'", "gen_random_uuid()"),
        ):
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id, tenant_id, user_id, action, data, occurred_at) VALUES "
                        f"(gen_random_uuid(), {tenant_value}, {user_value}, 'x', '{{}}', now())"
                    )
                )
            conn.rollback()


# --- Sécurité -------------------------------------------------------------------------------


def test_tenant_routes_are_reserved_to_platform_admins(
    console: TestClient, admin: TestClient, provision: Any, client: TestClient, owner_db: Session
) -> None:
    tenant = provision("alpha")
    paths = [
        ("get", "/tenants"),
        ("get", f"/tenants/{tenant.tenant_id}"),
        ("post", f"/tenants/{tenant.tenant_id}/suspend"),
        ("post", f"/tenants/{tenant.tenant_id}/reactivate"),
        ("post", f"/tenants/{tenant.tenant_id}/subscription/activate"),
        ("post", f"/tenants/{tenant.tenant_id}/subscription/extend"),
        ("post", f"/tenants/{tenant.tenant_id}/subscription/change-plan"),
    ]
    anonymous = TestClient(console.app)
    token = login(client, "owner@alpha.example.com", PASSWORD).json()["access_token"]
    for method, path in paths:
        url = f"{CONSOLE_PREFIX}{path}"
        body = {"reason": REASON, "plan_code": "STANDARD", "period_end": "2099-01-01"}
        anon = getattr(anonymous, method)(
            url, headers=CONSOLE_HEADERS, **({"json": body} if method == "post" else {})
        )
        assert anon.status_code == 401, path
        tenant_admin = getattr(anonymous, method)(
            url,
            headers={"Authorization": f"Bearer {token}"} | CONSOLE_HEADERS,
            **({"json": body} if method == "post" else {}),
        )
        assert tenant_admin.status_code == 401, path
    # Anti-CSRF même pour un administrateur TechNova connecté.
    no_header = admin.post(
        f"{CONSOLE_PREFIX}/tenants/{tenant.tenant_id}/suspend", json={"reason": REASON}
    )
    assert no_header.status_code == 403
    assert (
        owner_db.scalar(text("SELECT status FROM tenants WHERE id = :t"), {"t": tenant.tenant_id})
        == "active"
    )
    # L'API des entreprises n'expose aucune de ces actions.
    owner = Api(client, token)
    for path in ("/tenant/suspend", "/subscription/activate", "/subscription/extend"):
        assert owner.post(path, json={"reason": REASON}).status_code in (404, 405)


# --- Tableau de bord --------------------------------------------------------------------------


def test_dashboard_aggregates_tenants_and_subscriptions(
    admin: TestClient, provision: Any, owner_db: Session
) -> None:
    alpha = provision("alpha")
    beta = provision("beta", trial_days=14)
    gamma = provision("gamma")
    _pending(owner_db, beta.tenant_id)
    for tenant, days in ((alpha, 90), (gamma, 5)):
        _sql(
            owner_db,
            "UPDATE subscriptions SET current_period_end = now() + make_interval(days => :d) "
            "WHERE tenant_id = :t",
            t=tenant.tenant_id,
            d=days,
        )
    assert (
        _post(admin, f"/tenants/{alpha.tenant_id}/suspend", {"reason": REASON}).status_code == 200
    )
    counts = _get(admin, "/dashboard").json()["tenants"]
    assert counts == {
        "tenants_total": 3,
        "tenants_active": 2,
        "tenants_suspended": 1,
        "subscriptions_active": 2,
        "subscriptions_trial": 0,
        "subscriptions_pending_activation": 1,
        "subscriptions_past_due": 0,
        "subscriptions_expired": 0,
        "subscriptions_renewal_due": 1,
    }
    history = _get(admin, "/audit", params={"tenant_id": str(alpha.tenant_id)}).json()
    assert [e["action"] for e in history["items"]] == ["tenant.suspended"]
    assert datetime.fromisoformat(history["items"][0]["occurred_at"]).tzinfo is not None
