"""Phase 3.2-F — Console TechNova (ADR-0031) : socle et offres & tarifs.

Identité TechNova (CLI uniquement, compte dédié, invisible pour l'application des tenants),
processus et rôle SQL distincts (droits minimaux, aucune donnée de tenant), journal de la
plateforme append-only, catalogue technique en lecture seule, paramètres commerciaux des plans
(validation serveur, raison obligatoire, audit avant/après), intégration à ``/public/plans``,
``catalog sync`` et prix figé des souscriptions.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.cli import main as cli_main
from app.platform.catalog.loader import load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.registry import get_registry
from app.shared.clock import utcnow
from tests.conftest import (
    CONSOLE_HEADERS,
    CONSOLE_PREFIX,
    PASSWORD,
    PLATFORM_ADMIN_EMAIL,
    PLATFORM_ADMIN_PASSWORD,
    Api,
    console_login,
    login,
)

TEMPORARY = "Provisoire-Console-1"
REASON = "Révision tarifaire annuelle TechNova"


def _reset_plans(owner_db: Session) -> None:
    owner_db.execute(
        text(
            "UPDATE plans SET listed = false, price_display_enabled = false, monthly_price = NULL, "
            "monthly_price_enabled = false, annual_price = NULL, annual_price_enabled = false, "
            "currency = NULL, contact_required = false, commercial_description = NULL, "
            "display_order = 0, trial_days = 0"
        )
    )
    owner_db.commit()


@pytest.fixture(autouse=True)
def neutral_plans(owner_db: Session) -> Iterator[None]:
    _reset_plans(owner_db)
    yield
    _reset_plans(owner_db)


@pytest.fixture
def admin(console: TestClient, platform_admin: Any) -> TestClient:
    """Client de la console connecté (cookie de session) comme administrateur TechNova."""
    platform_admin()
    response = console_login(console)
    assert response.status_code == 200, response.text
    return console


def _get(console: TestClient, path: str, **kw: Any) -> Any:
    return console.get(f"{CONSOLE_PREFIX}{path}", **kw)


def _patch(console: TestClient, code: str, body: dict[str, Any]) -> Any:
    return console.patch(
        f"{CONSOLE_PREFIX}/plans/{code}/commercial", json=body, headers=CONSOLE_HEADERS
    )


def _publish_standard(console: TestClient, **extra: Any) -> Any:
    body = {
        "listed": True,
        "price_display_enabled": True,
        "monthly_price": "10000",
        "monthly_price_enabled": True,
        "annual_price": "120000",
        "annual_price_enabled": True,
        "currency": "XOF",
        "reason": REASON,
    } | extra
    response = _patch(console, "STANDARD", body)
    assert response.status_code == 200, response.text
    return response.json()


def _platform_audit(owner_db: Session, action: str) -> list[Any]:
    return list(
        owner_db.execute(
            text(
                "SELECT actor_user_id, actor_label, target_type, target_id, before, after, "
                "reason, data, tenant_id FROM platform_audit_logs WHERE action = :a "
                "ORDER BY occurred_at"
            ),
            {"a": action},
        ).all()
    )


def _tenant_member(client: TestClient, owner: Api, email: str, template: str) -> None:
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    created = owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": TEMPORARY,
            "roles": [{"role_id": roles[template]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    first = login(client, email, TEMPORARY)
    Api(client, first.json()["access_token"]).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )


# --- Accès : Platform Admin seulement -------------------------------------------------------


def test_platform_admin_reaches_every_console_page(admin: TestClient) -> None:
    me = _get(admin, "/me")
    assert me.status_code == 200
    assert me.json()["email"] == PLATFORM_ADMIN_EMAIL
    for path in ("/dashboard", "/plans", "/plans/STANDARD", "/catalog", "/audit"):
        assert _get(admin, path).status_code == 200, path
    dashboard = _get(admin, "/dashboard").json()
    assert dashboard["admin"]["email"] == PLATFORM_ADMIN_EMAIL
    assert dashboard["plans_active"] >= 2
    assert dashboard["plans_listed"] == 0
    # Aucun indicateur de tenant (ventes, stock, clients, entreprises) en 3.2-F.
    assert not {k for k in dashboard if "tenant" in k or "sale" in k or "customer" in k}


@pytest.mark.parametrize("template", ["administrator", "manager", "seller", "viewer"])
def test_tenant_users_never_reach_the_console(
    provision: Any, api_for: Any, client: TestClient, console: TestClient, template: str
) -> None:
    provision("alpha")
    owner: Api = api_for("owner@alpha.example.com")
    email = f"{template}@alpha.example.com"
    _tenant_member(client, owner, email, template)

    # Ni le compte de l'entreprise, ni son propriétaire n'existent pour la console.
    for account in (email, "owner@alpha.example.com"):
        refused = console_login(console, account, PASSWORD)
        assert refused.status_code == 401
        assert refused.json()["code"] == "invalid_credentials"
    # Un jeton de l'API des tenants n'ouvre rien dans la console.
    token = login(client, email, PASSWORD).json()["access_token"]
    for path in ("/dashboard", "/plans", "/audit"):
        response = _get(console, path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
    patched = console.patch(
        f"{CONSOLE_PREFIX}/plans/STANDARD/commercial",
        json={"trial_days": 30, "reason": "tentative"},
        headers={"Authorization": f"Bearer {token}"} | CONSOLE_HEADERS,
    )
    assert patched.status_code == 401


def test_tenant_admin_cannot_become_platform_admin_through_the_api(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    provision("alpha")
    owner: Api = api_for("owner@alpha.example.com")
    created = owner.post(
        "/members",
        json={
            "email": "escalade@alpha.example.com",
            "full_name": "Escalade",
            "password": TEMPORARY,
            "is_platform_admin": True,
        },
    )
    assert created.status_code == 422  # champ inconnu refusé (extra="forbid")
    members = owner.get("/members").json()["items"]
    patched = owner.patch(f"/members/{members[0]['id']}", json={"is_platform_admin": True})
    assert patched.status_code == 422
    assert owner.get("/me").json()["user"].get("is_platform_admin") is None
    assert owner_db.scalar(text("SELECT count(*) FROM users WHERE is_platform_admin")) == 0


def test_anonymous_and_forged_sessions_are_refused(console: TestClient, admin: TestClient) -> None:
    anonymous = TestClient(console.app)
    for path in ("/me", "/dashboard", "/plans", "/plans/STANDARD", "/catalog", "/audit"):
        response = anonymous.get(f"{CONSOLE_PREFIX}{path}")
        assert response.status_code == 401, path
    forged = TestClient(console.app, cookies={"sm_platform_session": f"{uuid.uuid4()}.secret"})
    assert forged.get(f"{CONSOLE_PREFIX}/me").status_code == 401
    # Anti-CSRF : une requête modifiante sans l'en-tête de la console est refusée.
    no_header = admin.patch(
        f"{CONSOLE_PREFIX}/plans/STANDARD/commercial", json={"trial_days": 3, "reason": REASON}
    )
    assert no_header.status_code == 403
    assert no_header.json()["code"] == "console_header_required"


def test_session_expiry_logout_and_lockout(
    admin: TestClient, console_app: Any, owner_db: Session
) -> None:
    owner_db.execute(
        text("UPDATE platform_sessions SET last_used_at = :t"),
        {"t": utcnow() - timedelta(minutes=31)},
    )
    owner_db.commit()
    expired = _get(admin, "/me")
    assert expired.status_code == 401
    assert expired.json()["code"] == "session_expired"

    assert console_login(admin).status_code == 200
    assert admin.post(f"{CONSOLE_PREFIX}/auth/logout", headers=CONSOLE_HEADERS).status_code == 204
    assert _get(admin, "/me").status_code == 401

    for _ in range(5):
        assert console_login(admin, password="mauvais").status_code == 401
    locked = console_login(admin)
    assert locked.status_code == 401
    assert locked.json()["code"] == "account_locked"
    failures = _platform_audit(owner_db, "auth.login.failed")
    assert failures and failures[-1].data["reason"] == "locked"


def test_the_console_exposes_no_route_to_grant_platform_admin(console_app: Any) -> None:
    paths = console_app.openapi()["paths"]
    writes = sorted(
        (path, method.upper())
        for path, operations in paths.items()
        for method in operations
        if method not in ("get", "head", "options")
    )
    assert writes == [
        (f"{CONSOLE_PREFIX}/auth/login", "POST"),
        (f"{CONSOLE_PREFIX}/auth/logout", "POST"),
        (f"{CONSOLE_PREFIX}/plans/{{code}}/commercial", "PATCH"),
    ]
    assert not [p for p in paths if "admin" in p or "user" in p or "tenant" in p]


# --- Identité TechNova : CLI uniquement, compte dédié hors tenant ---------------------------


def test_platform_admin_is_created_and_revoked_by_the_cli_only(
    settings: Any,
    console: TestClient,
    owner_db: Session,
    capsys: Any,
    monkeypatch: pytest.MonkeyPatch,
    provision: Any,
) -> None:
    monkeypatch.setenv("SM_PLATFORM_ADMIN_PASSWORD", PLATFORM_ADMIN_PASSWORD)
    args = ["platform-admin", "create", "--email", "Ops@TechNova.example", "--name", "Ops"]
    assert cli_main(args, settings) == 0
    user = owner_db.execute(
        text(
            "SELECT id, is_platform_admin, must_change_password FROM users "
            "WHERE email = 'ops@technova.example'"
        )
    ).one()
    assert user.is_platform_admin and not user.must_change_password
    assert (
        owner_db.scalar(
            text("SELECT count(*) FROM tenant_memberships WHERE user_id = :u"), {"u": user.id}
        )
        == 0
    )
    created = _platform_audit(owner_db, "platform_admin.created")
    assert created[-1].target_id == str(user.id)
    assert created[-1].actor_label.startswith("cli")

    # Compte dédié : e-mail déjà utilisé refusé (TechNova ou entreprise).
    assert cli_main(args, settings) == 1
    provision("alpha")
    taken = ["platform-admin", "create", "--email", "owner@alpha.example.com", "--name", "X"]
    assert cli_main(taken, settings) == 1
    assert "déjà utilisé" in capsys.readouterr().err

    assert cli_main(["platform-admin", "list"], settings) == 0
    assert "ops@technova.example" in capsys.readouterr().out

    assert console_login(console, "ops@technova.example").status_code == 200
    assert cli_main(["platform-admin", "revoke", "--email", "ops@technova.example"], settings) == 0
    assert _get(console, "/me").status_code == 401  # session révoquée
    assert console_login(console, "ops@technova.example").status_code == 401
    revoked = _platform_audit(owner_db, "platform_admin.revoked")
    assert revoked[-1].after == {"is_platform_admin": False, "is_active": False}


def test_platform_admin_is_invisible_to_the_tenant_application(
    admin: TestClient,
    provision: Any,
    api_for: Any,
    client: TestClient,
    app_engine: Engine,
    owner_db: Session,
) -> None:
    # Pas de connexion à l'application des tenants.
    refused = login(client, PLATFORM_ADMIN_EMAIL, PLATFORM_ADMIN_PASSWORD)
    assert refused.status_code == 401
    assert refused.json()["code"] == "invalid_credentials"
    # Jamais ajouté comme membre ni propriétaire d'une entreprise.
    provision("alpha")
    owner: Api = api_for("owner@alpha.example.com")
    added = owner.post(
        "/members",
        json={"email": PLATFORM_ADMIN_EMAIL, "full_name": "X", "password": TEMPORARY},
    )
    assert added.status_code == 409
    assert added.json()["code"] == "account_unavailable"
    assert all(m["email"] != PLATFORM_ADMIN_EMAIL for m in owner.get("/members").json()["items"])
    with pytest.raises(Exception, match="account_unavailable|propriétaire"):
        provision("beta", owner_email=PLATFORM_ADMIN_EMAIL)
    assert (
        owner_db.scalar(
            text(
                "SELECT count(*) FROM tenant_memberships m JOIN users u ON u.id = m.user_id "
                "WHERE u.is_platform_admin"
            )
        )
        == 0
    )
    # Rôle applicatif : le compte n'existe pas, et la colonne n'est pas modifiable.
    with app_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM users WHERE email = :e"), {"e": PLATFORM_ADMIN_EMAIL}
            ).scalar()
            == 0
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text("UPDATE users SET is_platform_admin = true"))
        conn.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(
                text(
                    "INSERT INTO users (id, email, full_name, password_hash, is_active, "
                    "must_change_password, failed_login_count, locale, is_platform_admin) "
                    "VALUES (gen_random_uuid(), 'x@y.z', 'x', 'h', true, false, 0, 'fr', true)"
                )
            )


def test_signup_with_a_platform_admin_email_gets_the_generic_refusal(
    admin: TestClient, client: TestClient, owner_db: Session
) -> None:
    _publish_standard(admin)
    response = client.post(
        "/api/v1/public/signup",
        json={
            "account": {
                "email": PLATFORM_ADMIN_EMAIL,
                "full_name": "Intrus",
                "password": "Inscription-2026",
            },
            "company": {"name": "Intrus SARL", "country_code": "BF", "currency": "XOF"},
            "business_profile": "retail.alimentation",
            "plan_code": "STANDARD",
            "billing_period": "monthly",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "signup_unavailable"
    assert owner_db.scalar(text("SELECT count(*) FROM tenants")) == 0


# --- Rôle SQL de la console : privilèges minimaux, aucune donnée de tenant ------------------


def test_platform_db_role_has_minimal_privileges(
    platform_engine: Engine, owner_db: Session, provision: Any, platform_admin: Any
) -> None:
    attrs = owner_db.execute(
        text(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
            "WHERE rolname = 'stockmanager_platform'"
        )
    ).one()
    assert tuple(attrs) == (False, False, False, False)
    provision("alpha")
    platform_admin()
    denied_reads = (
        "tenants",
        "subscriptions",
        "tenant_memberships",
        "sites",
        "sales",
        "payments",
        "customers",
        "stock_levels",
        "stock_movements",
        "catalog_articles",
        "cash_sessions",
        "audit_logs",
        "auth_sessions",
        "roles",
    )
    with platform_engine.connect() as conn:
        for table in denied_reads:
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            conn.rollback()
        # Comptes : seuls les administrateurs TechNova sont visibles (RLS).
        emails = conn.execute(text("SELECT email FROM users")).scalars().all()
        assert emails == [PLATFORM_ADMIN_EMAIL]
        for sql in (
            "UPDATE plans SET limits = '{}'",
            "UPDATE plans SET name = 'X'",
            "UPDATE plans SET is_active = false",
            "UPDATE plans SET grace_days = 0",
            "DELETE FROM plans",
            "INSERT INTO plan_modules (plan_code, module_code) VALUES ('STANDARD', 'x')",
            "DELETE FROM plan_modules",
            "UPDATE business_profiles SET name = 'X'",
            "UPDATE users SET is_platform_admin = true",
            "UPDATE users SET password_hash = 'x'",
            "INSERT INTO users (id, email, full_name, password_hash) "
            "VALUES (gen_random_uuid(), 'a@b.c', 'a', 'h')",
            "DELETE FROM platform_audit_logs",
            "UPDATE platform_audit_logs SET reason = 'x'",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(sql))
            conn.rollback()
        # Seules les colonnes commerciales des plans sont modifiables.
        conn.execute(text("UPDATE plans SET trial_days = 5 WHERE code = 'STANDARD'"))
        conn.rollback()


def test_platform_audit_is_append_only_even_for_the_owner(
    admin: TestClient, owner_db: Session
) -> None:
    _publish_standard(admin)
    for sql in ("UPDATE platform_audit_logs SET reason = 'x'", "DELETE FROM platform_audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            owner_db.execute(text(sql))
        owner_db.rollback()


# --- Offres & tarifs -------------------------------------------------------------------------


def test_plans_list_and_read_only_structure(admin: TestClient) -> None:
    plans = {p["code"]: p for p in _get(admin, "/plans").json()}
    assert {"STANDARD", "ENTREPRISE"} <= set(plans)
    standard = plans["STANDARD"]
    assert standard["listed"] is False and standard["self_service"] is False
    assert standard["trial_days"] == 0 and standard["monthly_price"] is None

    entreprise = _get(admin, "/plans/ENTREPRISE").json()["structure"]
    assert entreprise["features"] == ["stock.transfers"]
    assert entreprise["limits"] == {"max_sites": None, "max_users": None}
    permissions = {p["code"] for p in entreprise["permissions"]}
    assert "stock.transfer.create" in permissions
    modules = {m["code"]: m for m in entreprise["modules"]}
    assert modules["stock"]["status"] == "available"
    assert modules["restaurant.qr"]["status"] == "planned"
    assert any(m["core"] for m in modules.values())

    structure = _get(admin, "/plans/STANDARD").json()["structure"]
    assert structure["limits"] == {"max_sites": 1, "max_users": 5}
    assert "stock.transfer.create" not in {p["code"] for p in structure["permissions"]}
    assert _get(admin, "/plans/INCONNU").status_code == 404


@pytest.mark.parametrize(
    "field",
    [
        {"limits": {"max_users": 99}},
        {"modules": ["stock"]},
        {"features": ["stock.transfers"]},
        {"name": "Autre"},
        {"is_active": False},
        {"grace_days": 99},
        {"code": "AUTRE"},
    ],
)
def test_technical_structure_cannot_be_modified(
    admin: TestClient, owner_db: Session, field: dict[str, Any]
) -> None:
    response = _patch(admin, "STANDARD", field | {"trial_days": 3, "reason": REASON})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    row = owner_db.execute(
        text("SELECT name, limits, grace_days, trial_days FROM plans WHERE code = 'STANDARD'")
    ).one()
    assert tuple(row) == ("Standard", {"max_sites": 1, "max_users": 5}, 7, 0)


def test_commercial_update_round_trip(admin: TestClient) -> None:
    plan = _publish_standard(
        admin,
        contact_required=False,
        commercial_description="  Pour démarrer  ",
        display_order=2,
        trial_days=14,
    )
    assert plan["listed"] is True
    assert plan["monthly_price"] == "10000.00" and plan["annual_price"] == "120000.00"
    assert plan["currency"] == "XOF"
    assert plan["commercial_description"] == "Pour démarrer"
    assert plan["trial_days"] == 14 and plan["display_order"] == 2
    assert plan["self_service"] is True
    assert plan["structure"]["limits"] == {"max_sites": 1, "max_users": 5}
    # Aucune modification : refusée (jamais d'écriture silencieuse dans le journal).
    same = _patch(admin, "STANDARD", {"trial_days": 14, "reason": REASON})
    assert same.status_code == 422
    assert same.json()["code"] == "no_changes"
    # Essai : 0 = aucun essai (jamais de valeur par défaut imposée).
    off = _patch(admin, "STANDARD", {"trial_days": 0, "reason": REASON})
    assert off.json()["trial_days"] == 0


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"monthly_price": "-1"}, "validation_error"),
        ({"monthly_price": "10.123"}, "validation_error"),
        ({"annual_price": "abc"}, "validation_error"),
        ({"currency": "xof"}, "validation_error"),
        ({"currency": "ZZZ"}, "unknown_currency"),
        ({"monthly_price": "5000"}, "currency_required"),
        ({"monthly_price_enabled": True, "currency": "XOF"}, "price_required"),
        (
            {"annual_price_enabled": True, "monthly_price": "10", "currency": "XOF"},
            "price_required",
        ),
        ({"price_display_enabled": True}, "price_display_without_period"),
        ({"listed": True}, "plan_not_subscribable"),
        ({"trial_days": -1}, "validation_error"),
        ({"trial_days": 366}, "validation_error"),
        ({"trial_days": "sept"}, "validation_error"),
        ({"display_order": -1}, "validation_error"),
        ({"commercial_description": "x" * 2001}, "validation_error"),
    ],
)
def test_commercial_validations_are_enforced_by_the_server(
    admin: TestClient, owner_db: Session, body: dict[str, Any], code: str
) -> None:
    response = _patch(admin, "STANDARD", body | {"reason": REASON})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    plan_audit = "SELECT count(*) FROM platform_audit_logs WHERE action LIKE 'plan.%'"
    assert owner_db.scalar(text(plan_audit)) == 0


def test_contact_only_plan_can_be_published_without_period(admin: TestClient) -> None:
    response = _patch(
        admin, "ENTREPRISE", {"listed": True, "contact_required": True, "reason": REASON}
    )
    assert response.status_code == 200, response.text
    assert response.json()["self_service"] is False


@pytest.mark.parametrize("reason", [None, "", "   ", "\t\n"])
def test_reason_is_mandatory(admin: TestClient, reason: str | None) -> None:
    body: dict[str, Any] = {"trial_days": 7}
    if reason is not None:
        body["reason"] = reason
    response = _patch(admin, "STANDARD", body)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert any(e["loc"][-1] == "reason" for e in response.json()["errors"])


def test_commercial_changes_are_audited_with_before_after_and_reason(
    admin: TestClient, owner_db: Session
) -> None:
    _publish_standard(admin)
    published = _platform_audit(owner_db, "plan.published")
    assert len(published) == 1
    entry = published[0]
    assert entry.actor_label == PLATFORM_ADMIN_EMAIL and entry.actor_user_id is not None
    assert (entry.target_type, entry.target_id, entry.tenant_id) == ("plan", "STANDARD", None)
    assert entry.reason == REASON
    assert entry.before["listed"] is False and entry.after["listed"] is True
    assert entry.after["annual_price"] == "120000.00"

    response = _patch(admin, "STANDARD", {"annual_price": "150000", "reason": "  Révision 2027  "})
    assert response.status_code == 200
    updated = _platform_audit(owner_db, "plan.commercial.updated")
    assert len(updated) == 1
    assert updated[0].before == {"annual_price": "120000.00"}
    assert updated[0].after == {"annual_price": "150000.00"}
    assert updated[0].reason == "Révision 2027"
    assert updated[0].data == {"fields": ["annual_price"]}

    assert _patch(admin, "STANDARD", {"listed": False, "reason": "Retrait"}).status_code == 200
    assert len(_platform_audit(owner_db, "plan.unpublished")) == 1

    # Journal consultable (filtres) ; aucune entrée dans le journal des tenants.
    page = _get(admin, "/audit", params={"target_type": "plan", "target_id": "STANDARD"}).json()
    assert [e["action"] for e in page["items"]] == [
        "plan.unpublished",
        "plan.commercial.updated",
        "plan.published",
    ]
    assert page["items"][1]["before"] == {"annual_price": "120000.00"}
    assert _get(admin, "/audit", params={"action": "plan.pub"}).json()["total"] == 1
    assert owner_db.scalar(text("SELECT count(*) FROM audit_logs WHERE action LIKE 'plan%'")) == 0


# --- Intégration : catalogue, /public/plans, souscriptions ----------------------------------


def test_catalog_sync_keeps_console_commercial_settings(
    admin: TestClient, owner_db: Session
) -> None:
    _publish_standard(admin, trial_days=10, contact_required=False, display_order=4)
    sync_catalog(owner_db, load_catalog(get_registry()))
    owner_db.commit()
    plan = _get(admin, "/plans/STANDARD").json()
    assert plan["listed"] is True
    assert (plan["monthly_price"], plan["annual_price"]) == ("10000.00", "120000.00")
    assert (plan["currency"], plan["trial_days"], plan["display_order"]) == ("XOF", 10, 4)
    # La structure reste celle du fichier versionné.
    assert plan["structure"]["limits"] == {"max_sites": 1, "max_users": 5}


def test_public_plans_reflect_console_settings(admin: TestClient, client: TestClient) -> None:
    def public() -> dict[str, Any]:
        return {p["code"]: p for p in client.get("/api/v1/public/plans").json()["plans"]}

    assert public() == {}
    _publish_standard(admin, trial_days=7)
    standard = public()["STANDARD"]
    assert standard["self_service"] is True and standard["trial_days"] == 7
    assert standard["currency"] == "XOF"
    assert {p["billing_period"]: p["price"] for p in standard["periods"]} == {
        "monthly": "10000.00",
        "annual": "120000.00",
    }
    # Prix masqué, période fermée, contact commercial.
    _patch(
        admin,
        "STANDARD",
        {"price_display_enabled": False, "annual_price_enabled": False, "reason": REASON},
    )
    standard = public()["STANDARD"]
    assert standard["currency"] is None
    assert standard["periods"] == [{"billing_period": "monthly", "price": None}]
    _patch(admin, "STANDARD", {"contact_required": True, "reason": REASON})
    assert public()["STANDARD"]["self_service"] is False
    # Dépublié : absent.
    _patch(admin, "STANDARD", {"listed": False, "reason": REASON})
    assert "STANDARD" not in public()


def test_subscription_keeps_its_price_snapshot(
    admin: TestClient, provision: Any, owner_db: Session
) -> None:
    _publish_standard(admin)
    tenant = provision("alpha", plan="STANDARD")
    assert (
        _patch(admin, "STANDARD", {"monthly_price": "15000", "reason": REASON}).status_code == 200
    )
    snapshot = owner_db.execute(
        text(
            "SELECT price_at_subscription, currency_at_subscription FROM subscriptions "
            "WHERE tenant_id = :t"
        ),
        {"t": tenant.tenant_id},
    ).one()
    assert tuple(snapshot) == (Decimal("10000.00"), "XOF")
    later = provision("beta", plan="STANDARD")
    assert owner_db.scalar(
        text("SELECT price_at_subscription FROM subscriptions WHERE tenant_id = :t"),
        {"t": later.tenant_id},
    ) == Decimal("15000.00")


def test_catalog_is_read_only_and_complete(admin: TestClient) -> None:
    catalog = _get(admin, "/catalog").json()
    modules = {m["code"]: m for m in catalog["modules"]}
    assert "stock.transfer.create" in {p["code"] for p in modules["stock"]["permissions"]}
    assert modules["stock"]["features"] == ["stock.transfers"]
    assert {"administrator", "manager", "seller", "viewer"} <= {
        r["code"] for r in catalog["role_templates"]
    }
    assert {"active", "expired", "pending_activation"} <= {p["status"] for p in catalog["policies"]}
    assert "XOF" in catalog["currencies"] and catalog["active_countries"] > 100
    assert catalog["profiles"] and catalog["sectors"] and catalog["ux_profiles"]
    for path in ("/catalog", "/catalog/modules/stock"):
        for method in ("post", "put", "patch", "delete"):
            response = getattr(admin, method)(f"{CONSOLE_PREFIX}{path}", headers=CONSOLE_HEADERS)
            assert response.status_code in (404, 405), (method, path)


def test_console_refuses_an_insecure_cookie_in_production(settings: Any) -> None:
    from app.console.main import create_console_app

    insecure = settings.model_copy(
        update={"environment": "production", "platform_cookie_secure": False}
    )
    with pytest.raises(ValueError, match="SM_PLATFORM_COOKIE_SECURE"):
        create_console_app(insecure)
