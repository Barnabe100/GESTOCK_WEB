"""Phase 3.2-A — Inscription publique : offres publiées, profils, création compte + entreprise
(propriétaire et administrateur), abonnement ``pending_activation`` ou essai, sécurité
(anti-énumération, limitation de fréquence, champs imposés par le serveur), isolation."""

import threading
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.provisioning.service import TenantProvisioningService
from tests.conftest import Api, login

PASSWORD = "Motdepasse-Signup-1"


@pytest.fixture
def offers(owner_db: Session) -> Iterator[None]:
    """STANDARD publié (mensuel 5 000 et annuel 50 000 XOF, prix affichés, sans essai) ;
    ENTREPRISE publié sur contact commercial."""
    owner_db.execute(
        text(
            "UPDATE plans SET listed = true, price_display_enabled = true, currency = 'XOF', "
            "monthly_price = 5000, monthly_price_enabled = true, annual_price = 50000, "
            "annual_price_enabled = true, commercial_description = 'Pour démarrer', "
            "display_order = 1 WHERE code = 'STANDARD'"
        )
    )
    owner_db.execute(
        text(
            "UPDATE plans SET listed = true, contact_required = true, display_order = 2 "
            "WHERE code = 'ENTREPRISE'"
        )
    )
    owner_db.commit()
    yield
    owner_db.execute(
        text(
            "UPDATE plans SET listed = false, price_display_enabled = false, monthly_price = NULL, "
            "monthly_price_enabled = false, annual_price = NULL, annual_price_enabled = false, "
            "currency = NULL, contact_required = false, commercial_description = NULL, "
            "display_order = 0, trial_days = 0"
        )
    )
    owner_db.commit()


def _body(email: str = "awa@superette.example", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "account": {"full_name": "Awa Traoré", "email": email, "password": PASSWORD},
        "company": {
            "name": "Supérette Awa SARL",
            "country_code": "BF",
            "trade_name": "Chez Awa",
            "phone": "+226 70 11 22 33",
            "city": "Ouagadougou",
        },
        "business_profile": "retail.alimentation",
        "plan_code": "STANDARD",
        "billing_period": "monthly",
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(body.get(key), dict):
            body[key] = {**body[key], **value}
        else:
            body[key] = value
    return body


def _signup(client: TestClient, **overrides: Any) -> Any:
    return client.post("/api/v1/public/signup", json=_body(**overrides))


def _api(client: TestClient, response: Any) -> Api:
    assert response.status_code == 201, response.text
    return Api(client=client, token=response.json()["access_token"])


# --- Catalogue public -----------------------------------------------------------------------


def test_public_plans_expose_only_published_offers(client: TestClient, owner_db: Session) -> None:
    empty = client.get("/api/v1/public/plans").json()
    assert empty == {"contact_email": "ventes@technova.example", "plans": []}


def test_public_plans_follow_commercial_settings(
    client: TestClient, offers: None, owner_db: Session
) -> None:
    plans = client.get("/api/v1/public/plans").json()["plans"]
    assert [p["code"] for p in plans] == ["STANDARD", "ENTREPRISE"]
    standard, entreprise = plans
    assert standard["self_service"] is True and standard["price_displayed"] is True
    assert standard["description"] == "Pour démarrer"
    assert standard["periods"] == [
        {"billing_period": "monthly", "price": "5000.00"},
        {"billing_period": "annual", "price": "50000.00"},
    ]
    assert standard["currency"] == "XOF" and standard["trial_days"] == 0
    assert standard["limits"] == {"max_sites": 1, "max_users": 5}
    assert "pos" in standard["modules"] and "restaurant.tables" not in standard["modules"]
    assert entreprise["contact_required"] is True and entreprise["self_service"] is False
    assert entreprise["periods"] == [] and entreprise["limits"]["max_sites"] is None
    # Aucune donnée interne (structure, dates, statut technique) n'est exposée.
    assert set(standard) == {
        "code",
        "name",
        "description",
        "contact_required",
        "self_service",
        "trial_days",
        "price_displayed",
        "currency",
        "periods",
        "limits",
        "modules",
    }

    # Prix masqué : périodes ouvertes sans montant ; période fermée absente ; plan retiré.
    owner_db.execute(
        text(
            "UPDATE plans SET price_display_enabled = false, annual_price_enabled = false "
            "WHERE code = 'STANDARD'"
        )
    )
    owner_db.execute(text("UPDATE plans SET listed = false WHERE code = 'ENTREPRISE'"))
    owner_db.commit()
    plans = client.get("/api/v1/public/plans").json()["plans"]
    assert [p["code"] for p in plans] == ["STANDARD"]
    assert plans[0]["periods"] == [{"billing_period": "monthly", "price": None}]
    assert plans[0]["currency"] is None


def test_public_business_profiles(client: TestClient) -> None:
    data = client.get("/api/v1/public/business-profiles").json()
    assert [s["code"] for s in data["sectors"]] == [
        "retail",
        "restaurant",
        "automobile",
        "distribution",
    ]
    assert len(data["profiles"]) == 28
    assert {"code": "restaurant.maquis", "sector": "restaurant"}.items() <= next(
        p for p in data["profiles"] if p["code"] == "restaurant.maquis"
    ).items()


# --- Inscription réussie ------------------------------------------------------------------


def test_signup_creates_owner_admin_tenant_pending_activation(
    client: TestClient, offers: None, owner_db: Session
) -> None:
    response = _signup(client)
    assert response.cookies.get("sm_refresh")
    session = response.json()
    assert session["user"]["must_change_password"] is False
    assert [m["is_owner"] for m in session["memberships"]] == [True]
    api = _api(client, response)

    caps = api.get("/me/capabilities").json()
    assert caps["is_owner"] is True
    assert caps["profile"]["code"] == "retail.alimentation"
    assert caps["tenant"]["currency"] == "XOF"
    assert caps["sites"] == []  # premier site : étape d'onboarding
    assert caps["subscription"]["status"] == "pending_activation"
    assert caps["subscription"]["allowed_access"] == ["admin", "billing", "read"]
    for permission in ("sales.sale.create", "stock.entry.create", "pos.terminal.use"):
        assert permission in caps["restricted_permissions"]
    for permission in ("organization.site.manage", "users.member.manage", "users.role.manage"):
        assert permission in caps["permissions"]

    # Propriétaire ET administrateur principal (rôle protégé), sur tout le tenant.
    member = api.get("/members").json()[0]
    roles = {r["id"]: r for r in api.get("/roles").json()}
    assert member["is_owner"] is True and member["all_sites"] is True
    assert [roles[r["role_id"]]["protected"] for r in member["roles"]] == [True]

    tenant = api.get("/tenant").json()
    assert (tenant["name"], tenant["trade_name"], tenant["phone"], tenant["country_code"]) == (
        "Supérette Awa SARL",
        "Chez Awa",
        "+22670112233",
        "BF",
    )

    # Accès administratif : entreprise, premier site. Opérations métier : refusées.
    assert api.patch("/tenant", json={"city": "Bobo-Dioulasso"}).status_code == 200
    site = api.post("/sites", json={"name": "Boutique", "code": "BTQ"})
    assert site.status_code == 201, site.text
    refused = api.post("/catalog/categories", json={"name": "Riz"})
    assert refused.status_code == 403
    assert refused.json()["code"] == "subscription_restricted"

    tenant_id = session["tenant_id"]
    row = owner_db.execute(
        text(
            "SELECT status, billing_period, price_at_subscription, currency_at_subscription "
            "FROM subscriptions WHERE tenant_id = :t"
        ),
        {"t": tenant_id},
    ).one()
    assert tuple(row) == ("pending_activation", "monthly", Decimal("5000.00"), "XOF")
    audit = owner_db.execute(
        text("SELECT data FROM audit_logs WHERE tenant_id = :t AND action = 'tenant.provisioned'"),
        {"t": tenant_id},
    ).scalar_one()
    assert (audit["actor"], audit["first_site"], audit["subscription_status"]) == (
        "signup",
        False,
        "pending_activation",
    )


def test_signup_with_trial_configured_by_technova(
    client: TestClient, offers: None, owner_db: Session
) -> None:
    owner_db.execute(text("UPDATE plans SET trial_days = 14 WHERE code = 'STANDARD'"))
    owner_db.commit()
    api = _api(client, _signup(client, billing_period="annual"))
    caps = api.get("/me/capabilities").json()
    assert caps["subscription"]["status"] == "trial"
    assert caps["restricted_permissions"] == []
    assert api.post("/catalog/categories", json={"name": "Riz"}).status_code == 201
    price = owner_db.execute(
        text("SELECT price_at_subscription FROM subscriptions WHERE tenant_id = :t"),
        {"t": caps["tenant"]["id"]},
    ).scalar_one()
    assert price == Decimal("50000.00")


def test_cli_keeps_immediate_activation(provision: Any, owner_db: Session) -> None:
    t = provision("alpha", plan="STANDARD")
    status = owner_db.execute(
        text("SELECT status FROM subscriptions WHERE tenant_id = :t"), {"t": t.tenant_id}
    ).scalar_one()
    assert status == "active"


# --- Validation -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"plan_code": "ENTREPRISE"}, "plan_not_available"),  # contact commercial
        ({"plan_code": "INCONNU"}, "plan_not_available"),
        ({"business_profile": "retail.inconnu"}, "unknown_profile"),
        ({"company": {"country_code": "AQ"}}, "unknown_country"),
        ({"company": {"currency": "ZZZ"}}, "invalid_currency"),
        ({"account": {"password": "court"}}, "password_too_short"),
    ],
)
def test_signup_business_validation(
    client: TestClient, offers: None, owner_db: Session, overrides: dict[str, Any], code: str
) -> None:
    before = owner_db.execute(text("SELECT count(*) FROM tenants")).scalar_one()
    response = _signup(client, **overrides)
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert owner_db.execute(text("SELECT count(*) FROM tenants")).scalar_one() == before


def test_closed_period_or_unpublished_plan_is_refused(
    client: TestClient, offers: None, owner_db: Session
) -> None:
    owner_db.execute(text("UPDATE plans SET annual_price_enabled = false WHERE code = 'STANDARD'"))
    owner_db.commit()
    assert _signup(client, billing_period="annual").json()["code"] == "plan_not_available"
    owner_db.execute(text("UPDATE plans SET listed = false WHERE code = 'STANDARD'"))
    owner_db.commit()
    assert _signup(client).json()["code"] == "plan_not_available"


@pytest.mark.parametrize(
    "overrides",
    [
        {"company": {"country_code": None}},
        {"company": {"name": ""}},
        {"account": {"email": "pas-un-email"}},
        {"company": {"logo_url": "http://exemple.com/logo.png"}},
        # Aucun champ d'état, de paiement ou d'activation n'est accepté du client.
        {"subscription_status": "active"},
        {"payment_confirmed": True},
        {"company": {"is_owner": True}},
        {"account": {"is_platform_admin": True}},
    ],
)
def test_signup_rejects_malformed_or_forbidden_fields(
    client: TestClient, offers: None, overrides: dict[str, Any]
) -> None:
    response = _signup(client, **overrides)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --- Anti-énumération, transaction, concurrence ---------------------------------------------


def test_existing_email_gets_a_generic_answer(
    client: TestClient, offers: None, provision: Any, owner_db: Session
) -> None:
    provision("alpha", owner_email="deja@exemple.example")
    before = owner_db.execute(text("SELECT count(*) FROM tenants")).scalar_one()
    response = _signup(client, email="deja@exemple.example")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "signup_unavailable"
    assert "deja@" not in body["detail"] and "existe" not in body["detail"]
    assert owner_db.execute(text("SELECT count(*) FROM tenants")).scalar_one() == before
    # Les autres validations passent avant : aucune information sur l'e-mail sans elles.
    weak = _signup(client, email="deja@exemple.example", account={"password": "court"})
    assert weak.json()["code"] == "password_too_short"
    # Le compte existant est intact (aucun rattachement, aucun mot de passe modifié).
    assert login(client, "deja@exemple.example").status_code == 200


def test_signup_is_atomic(
    client: TestClient, offers: None, owner_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("panne pendant le provisioning")

    monkeypatch.setattr(TenantProvisioningService, "_create_roles", fail)
    counts = "SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM tenants)"
    before = tuple(owner_db.execute(text(counts)).one())
    with pytest.raises(RuntimeError):
        _signup(client, email="atomique@exemple.example")
    assert tuple(owner_db.execute(text(counts)).one()) == before


def test_concurrent_signups_with_the_same_email(app: Any, offers: None, owner_db: Session) -> None:
    results: list[int] = []
    barrier = threading.Barrier(2)

    def attempt(name: str) -> None:
        with TestClient(app) as c:
            barrier.wait()
            results.append(
                c.post(
                    "/api/v1/public/signup",
                    json=_body(email="course@exemple.example", company={"name": name}),
                ).status_code
            )

    threads = [threading.Thread(target=attempt, args=(n,)) for n in ("Un", "Deux")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [201, 422]
    users = owner_db.execute(
        text("SELECT count(*) FROM users WHERE email = 'course@exemple.example'")
    ).scalar_one()
    assert users == 1


# --- Limitation de fréquence et fermeture ------------------------------------------------------


def test_signup_is_rate_limited_per_ip(app: Any, offers: None, settings: Any) -> None:
    app.state.settings = settings.model_copy(update={"signup_rate_limit_attempts": 2})
    try:
        with TestClient(app, client=("203.0.113.10", 5000)) as c:
            first = c.post("/api/v1/public/signup", json=_body(email="r1@exemple.example"))
            # Un échec de validation métier est compté lui aussi.
            second = c.post("/api/v1/public/signup", json=_body(plan_code="ENTREPRISE"))
            third = c.post("/api/v1/public/signup", json=_body(email="r3@exemple.example"))
        assert (first.status_code, second.status_code, third.status_code) == (201, 422, 429)
        assert third.json()["code"] == "rate_limited"
        assert int(third.headers["retry-after"]) > 0
        # Autre adresse IP : non concernée.
        with TestClient(app, client=("203.0.113.11", 5000)) as other:
            ok = other.post("/api/v1/public/signup", json=_body(email="r4@exemple.example"))
        assert ok.status_code == 201
    finally:
        app.state.settings = settings


def test_signup_can_be_closed(app: Any, client: TestClient, offers: None, settings: Any) -> None:
    app.state.settings = settings.model_copy(update={"signup_enabled": False})
    try:
        response = _signup(client)
        assert (response.status_code, response.json()["code"]) == (403, "signup_closed")
    finally:
        app.state.settings = settings


# --- Isolation --------------------------------------------------------------------------------


def test_signed_up_tenants_are_isolated(client: TestClient, offers: None) -> None:
    a = _api(client, _signup(client, email="a@exemple.example"))
    b_response = _signup(client, email="b@exemple.example", company={"name": "Autre SARL"})
    b = _api(client, b_response)
    assert a.get("/tenant").json()["name"] == "Supérette Awa SARL"
    assert b.get("/tenant").json()["name"] == "Autre SARL"
    assert [m["email"] for m in a.get("/members").json()] == ["a@exemple.example"]
    # Le compte A ne peut pas ouvrir l'entreprise B.
    denied = login(client, "a@exemple.example", PASSWORD, b_response.json()["tenant_id"])
    assert denied.status_code == 403
    assert denied.json()["code"] == "tenant_access_denied"
