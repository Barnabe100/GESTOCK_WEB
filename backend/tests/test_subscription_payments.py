"""Phase 3.3-A — Paiements d'abonnement (ADR-0032).

L'entreprise **déclare** un paiement (``PENDING``, idempotent) ; seul TechNova le **confirme**
ou le **rejette** (console, décision définitive sous verrou, double audit). Un paiement
confirmé n'active rien (licence et activation : 3.3-B). Sécurité : permission dédiée, champs
de décision interdits au client, isolation API et SQL (RLS), droits minimaux du rôle de la
console (colonnes de décision d'une ligne ``PENDING`` seulement), déclencheur de finalité.
"""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor
from app.console.payments import PaymentDecisionService
from app.core.db import create_session_factory, set_db_context
from app.core.errors import ConflictError
from app.platform.audit.service import RequestMeta
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

TEMPORARY = "Provisoire-123"
REASON = "Virement reçu sur le compte TechNova"
REJECTION = "Référence introuvable sur le relevé bancaire"


# --- Aides --------------------------------------------------------------------------------


@pytest.fixture
def alpha(provision: Any) -> Any:
    return provision("alpha", plan="STANDARD")


@pytest.fixture
def owner(alpha: Any, api_for: Any) -> Api:
    api: Api = api_for("owner@alpha.example.com")
    return api


@pytest.fixture
def admin(console: TestClient, platform_admin: Any) -> TestClient:
    platform_admin()
    assert console_login(console).status_code == 200
    return console


def _body(subscription_id: uuid.UUID, **fields: Any) -> dict[str, Any]:
    return {
        "subscription_id": str(subscription_id),
        "amount": "10000.00",
        "period_start": "2026-10-01",
        "period_end": "2026-11-01",
        "payment_method": "BANK_TRANSFER",
        "declared_reference": "VIR-2026-001",
        "idempotency_key": str(uuid.uuid4()),
    } | fields


def _declare(api: Api, subscription_id: uuid.UUID, **fields: Any) -> dict[str, Any]:
    response = api.post("/subscription/payments", json=_body(subscription_id, **fields))
    assert response.status_code == 201, response.text
    return dict(response.json())


def _member(owner: Api, client: TestClient, email: str, template: str) -> Api:
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
    token = login(client, email, TEMPORARY).json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _cpost(c: TestClient, path: str, body: dict[str, Any] | None = None) -> Any:
    return c.post(f"{CONSOLE_PREFIX}{path}", json=body or {}, headers=CONSOLE_HEADERS)


def _cget(c: TestClient, path: str, **kw: Any) -> Any:
    return c.get(f"{CONSOLE_PREFIX}{path}", **kw)


def _sql(owner_db: Session, sql: str, **params: Any) -> Any:
    result = owner_db.execute(text(sql), params)
    owner_db.commit()
    return result


def _payment_row(owner_db: Session, payment_id: str) -> Any:
    return owner_db.execute(
        text("SELECT * FROM subscription_payments WHERE id = :id"), {"id": payment_id}
    ).one()


def _platform_audit(owner_db: Session, action: str) -> list[Any]:
    return list(
        owner_db.execute(
            text(
                "SELECT id, actor_user_id, tenant_id, target_type, target_id, before, after, "
                "reason, data FROM platform_audit_logs WHERE action = :a ORDER BY occurred_at"
            ),
            {"a": action},
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


def _state(owner_db: Session, tenant_id: uuid.UUID) -> tuple[Any, ...]:
    """État de l'entreprise et de son abonnement (inchangé par une décision de paiement)."""
    return tuple(
        owner_db.execute(
            text(
                "SELECT t.status, s.status, s.plan_code, s.current_period_start, "
                "s.current_period_end, s.price_at_subscription, s.currency_at_subscription, "
                "s.updated_at FROM tenants t JOIN subscriptions s ON s.tenant_id = t.id "
                "WHERE t.id = :t"
            ),
            {"t": tenant_id},
        ).one()
    )


# --- Déclaration -------------------------------------------------------------------------


def test_declaration_is_pending_with_server_side_currency_and_audit(
    owner: Api, alpha: Any, owner_db: Session
) -> None:
    subscription = owner.get("/subscription").json()
    assert subscription["id"] == str(alpha.subscription_id)
    payment = _declare(owner, alpha.subscription_id, declared_reference="  VIR-2026-001  ")
    assert payment["status"] == "PENDING"
    assert payment["amount"] == "10000.00"
    assert payment["currency"] == "XOF"  # fixée par le serveur (Burkina Faso)
    assert payment["declared_reference"] == "VIR-2026-001"
    assert payment["declared_by"] == str(alpha.owner_user_id)
    assert payment["decided_at"] is None and payment["rejection_reason"] is None
    assert "decided_by" not in payment

    assert owner.get(f"/subscription/payments/{payment['id']}").json() == payment
    listing = owner.get("/subscription/payments").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == payment["id"]

    [entry] = _tenant_audit(owner_db, "subscription_payment.declared", alpha.tenant_id)
    assert entry.user_id == alpha.owner_user_id
    assert entry.entity_type == "subscription_payment"
    assert str(entry.entity_id) == payment["id"]
    assert entry.data["amount"] == "10000.00" and entry.data["currency"] == "XOF"
    # Déclarer ne modifie pas l'abonnement.
    assert owner.get("/subscription").json()["status"] == subscription["status"]


def test_declared_currency_follows_the_frozen_subscription_price(
    owner: Api, alpha: Any, owner_db: Session
) -> None:
    _sql(
        owner_db,
        "UPDATE subscriptions SET price_at_subscription = 15000, "
        "currency_at_subscription = 'EUR' WHERE tenant_id = :t",
        t=alpha.tenant_id,
    )
    assert _declare(owner, alpha.subscription_id)["currency"] == "EUR"


def test_payment_list_is_paginated_filtered_and_sorted(
    owner: Api, alpha: Any, admin: TestClient
) -> None:
    first = _declare(owner, alpha.subscription_id, amount="5000", declared_reference="A")
    second = _declare(owner, alpha.subscription_id, amount="20000", declared_reference="B")
    third = _declare(owner, alpha.subscription_id, amount="12000", declared_reference="C")
    assert (
        _cpost(admin, f"/payments/{second['id']}/reject", {"reason": REJECTION}).status_code == 200
    )

    page = owner.get("/subscription/payments").json()
    assert [p["id"] for p in page["items"]] == [third["id"], second["id"], first["id"]]
    by_amount = owner.get("/subscription/payments", params={"sort": "amount"}).json()
    assert [p["amount"] for p in by_amount["items"]] == ["5000.00", "12000.00", "20000.00"]
    pending = owner.get("/subscription/payments", params={"status": "PENDING"}).json()
    assert {p["id"] for p in pending["items"]} == {first["id"], third["id"]}
    rejected = owner.get("/subscription/payments", params={"status": "REJECTED"}).json()
    assert rejected["items"][0]["rejection_reason"] == REJECTION
    limited = owner.get("/subscription/payments", params={"limit": 2, "offset": 2}).json()
    assert limited["total"] == 3 and len(limited["items"]) == 1
    assert owner.get("/subscription/payments", params={"sort": "tenant_id"}).status_code == 400
    assert owner.get("/subscription/payments", params={"status": "PAID"}).status_code == 422


@pytest.mark.parametrize(
    "fields",
    [
        {"amount": "0"},
        {"amount": "-10"},
        {"amount": "10.001"},
        {"declared_reference": "   "},
        {"declared_reference": "x" * 101},
        {"payment_method": "BITCOIN"},
        {"idempotency_key": "pas-une-uuid"},
        {"period_start": "2026-13-01"},
    ],
)
def test_declaration_validation(owner: Api, alpha: Any, fields: dict[str, Any]) -> None:
    response = owner.post("/subscription/payments", json=_body(alpha.subscription_id, **fields))
    assert response.status_code == 422, response.text


def test_declared_period_is_checked(owner: Api, alpha: Any) -> None:
    for start, end, code in (
        ("2026-10-01", "2026-10-01", "invalid_period"),
        ("2026-10-01", "2026-09-01", "invalid_period"),
        ("2026-10-01", "2028-10-02", "period_too_long"),
    ):
        response = owner.post(
            "/subscription/payments",
            json=_body(alpha.subscription_id, period_start=start, period_end=end),
        )
        assert response.status_code == 422
        assert response.json()["code"] == code
    _declare(owner, alpha.subscription_id, period_start="2026-10-01", period_end="2028-10-01")


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "CONFIRMED"),
        ("decided_by", str(uuid.uuid4())),
        ("decided_at", "2026-10-01T00:00:00Z"),
        ("rejection_reason", "aucun"),
        ("currency", "EUR"),
        ("tenant_id", str(uuid.uuid4())),
        ("declared_by", str(uuid.uuid4())),
        ("id", str(uuid.uuid4())),
        ("licence", "abc"),
    ],
)
def test_client_can_never_send_decision_or_unknown_fields(
    owner: Api, alpha: Any, owner_db: Session, field: str, value: str
) -> None:
    response = owner.post(
        "/subscription/payments", json=_body(alpha.subscription_id, **{field: value})
    )
    assert response.status_code == 422
    assert owner_db.scalar(text("SELECT count(*) FROM subscription_payments")) == 0


def test_tenant_api_exposes_no_decision_route(owner: Api, alpha: Any) -> None:
    payment = _declare(owner, alpha.subscription_id)
    for path in (
        f"/subscription/payments/{payment['id']}/confirm",
        f"/subscription/payments/{payment['id']}/reject",
    ):
        assert owner.post(path, json={"reason": REASON}).status_code in (404, 405)
    for method in ("patch", "put"):
        response = getattr(owner, method)(
            f"/subscription/payments/{payment['id']}", json={"status": "CONFIRMED"}
        )
        assert response.status_code == 405
    assert owner.delete(f"/subscription/payments/{payment['id']}").status_code == 405
    assert owner.get(f"/subscription/payments/{payment['id']}").json()["status"] == "PENDING"


# --- Idempotence -------------------------------------------------------------------------


def test_declaration_is_idempotent(owner: Api, alpha: Any, owner_db: Session) -> None:
    body = _body(alpha.subscription_id)
    first = owner.post("/subscription/payments", json=body)
    replay = owner.post("/subscription/payments", json=body)
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json() == first.json()
    conflict = owner.post("/subscription/payments", json=body | {"amount": "9999.00"})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_reused"
    assert owner_db.scalar(text("SELECT count(*) FROM subscription_payments")) == 1
    assert len(_tenant_audit(owner_db, "subscription_payment.declared", alpha.tenant_id)) == 1


def test_concurrent_identical_declarations_create_one_payment(
    alpha: Any, client: TestClient, owner_db: Session
) -> None:
    token = login(client, "owner@alpha.example.com").json()["access_token"]
    body = _body(alpha.subscription_id)
    statuses: list[int] = []

    def declare() -> None:
        with TestClient(client.app) as c:
            statuses.append(Api(c, token).post("/subscription/payments", json=body).status_code)

    threads = [threading.Thread(target=declare) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [200, 200, 200, 201]
    assert owner_db.scalar(text("SELECT count(*) FROM subscription_payments")) == 1


def test_same_key_is_independent_between_tenants(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    a = provision("alpha")
    b = provision("beta")
    key = str(uuid.uuid4())
    _declare(api_for("owner@alpha.example.com"), a.subscription_id, idempotency_key=key)
    _declare(api_for("owner@beta.example.com"), b.subscription_id, idempotency_key=key)
    assert owner_db.scalar(text("SELECT count(*) FROM subscription_payments")) == 2


# --- Permissions, statut de l'entreprise et politique d'abonnement ------------------------


def test_only_billing_roles_may_declare(owner: Api, alpha: Any, client: TestClient) -> None:
    payment = _declare(owner, alpha.subscription_id)
    seller = _member(owner, client, "vendeur@alpha.example.com", "seller")
    viewer = _member(owner, client, "consultant@alpha.example.com", "viewer")
    manager = _member(owner, client, "gestion@alpha.example.com", "manager")
    administrator = _member(owner, client, "admin@alpha.example.com", "administrator")

    for api in (seller, manager):
        assert api.get("/subscription/payments").status_code == 403
        assert api.get(f"/subscription/payments/{payment['id']}").status_code == 403
    for api in (seller, viewer, manager):
        response = api.post("/subscription/payments", json=_body(alpha.subscription_id))
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"
    # Consultant : lecture seule.
    assert viewer.get("/subscription/payments").json()["total"] == 1
    # Administrateur de l'entreprise (tous les droits de l'offre) : déclare.
    _declare(administrator, alpha.subscription_id)


def test_billing_is_allowed_while_pending_activation_or_expired(
    owner: Api, alpha: Any, owner_db: Session
) -> None:
    for status in ("pending_activation", "expired"):
        _sql(
            owner_db,
            "UPDATE subscriptions SET status = :s, current_period_end = now() WHERE tenant_id = :t",
            s=status,
            t=alpha.tenant_id,
        )
        _declare(owner, alpha.subscription_id)
        assert owner.get("/subscription/payments").status_code == 200
    # Résilié : plus de facturation (politique d'abonnement, donnée du catalogue).
    _sql(
        owner_db,
        "UPDATE subscriptions SET status = 'cancelled' WHERE tenant_id = :t",
        t=alpha.tenant_id,
    )
    response = owner.post("/subscription/payments", json=_body(alpha.subscription_id))
    assert response.status_code == 403
    assert response.json()["code"] == "subscription_restricted"


def test_suspended_tenant_cannot_declare_nor_read(
    owner: Api, alpha: Any, owner_db: Session
) -> None:
    payment = _declare(owner, alpha.subscription_id)
    _sql(owner_db, "UPDATE tenants SET status = 'suspended' WHERE id = :t", t=alpha.tenant_id)
    for response in (
        owner.post("/subscription/payments", json=_body(alpha.subscription_id)),
        owner.get("/subscription/payments"),
        owner.get(f"/subscription/payments/{payment['id']}"),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "tenant_suspended"
    assert owner_db.scalar(text("SELECT count(*) FROM subscription_payments")) == 1


def test_anonymous_access_is_refused(client: TestClient, alpha: Any) -> None:
    assert client.get("/api/v1/subscription/payments").status_code == 401
    response = client.post("/api/v1/subscription/payments", json=_body(alpha.subscription_id))
    assert response.status_code == 401


# --- Isolation ---------------------------------------------------------------------------


def test_tenants_never_see_each_other_payments(
    provision: Any, api_for: Any, app_engine: Engine
) -> None:
    a = provision("alpha")
    b = provision("beta")
    api_a = api_for("owner@alpha.example.com")
    api_b = api_for("owner@beta.example.com")
    payment_a = _declare(api_a, a.subscription_id)

    assert api_b.get("/subscription/payments").json()["total"] == 0
    response = api_b.get(f"/subscription/payments/{payment_a['id']}")
    assert response.status_code == 404
    assert response.json()["code"] == "subscription_payment_not_found"
    # Déclarer sur l'abonnement d'une autre entreprise : introuvable.
    foreign = api_b.post("/subscription/payments", json=_body(a.subscription_id))
    assert foreign.status_code == 404
    assert foreign.json()["code"] == "subscription_not_found"

    # SQL, rôle applicatif réel : RLS.
    with create_session_factory(app_engine)() as db:
        assert db.scalar(text("SELECT count(*) FROM subscription_payments")) == 0
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.scalar(text("SELECT count(*) FROM subscription_payments")) == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO subscription_payments (id, tenant_id, subscription_id, amount, "
                    "currency, period_start, period_end, payment_method, declared_reference, "
                    "idempotency_key, declared_by, status) VALUES (gen_random_uuid(), :t, :s, "
                    "1, 'XOF', '2026-10-01', '2026-11-01', 'CASH', 'X', gen_random_uuid(), "
                    ":u, 'PENDING')"
                ),
                {"t": a.tenant_id, "s": a.subscription_id, "u": b.owner_user_id},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=a.tenant_id)
        assert db.scalar(text("SELECT count(*) FROM subscription_payments")) == 1


def test_app_role_can_neither_decide_nor_delete(owner: Api, alpha: Any, app_engine: Engine) -> None:
    payment = _declare(owner, alpha.subscription_id)
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=alpha.tenant_id)
        for sql in (
            "UPDATE subscription_payments SET status = 'CONFIRMED'",
            "UPDATE subscription_payments SET amount = 1",
            "DELETE FROM subscription_payments",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(sql))
            db.rollback()
            set_db_context(db, tenant_id=alpha.tenant_id)
    assert owner.get(f"/subscription/payments/{payment['id']}").json()["status"] == "PENDING"


# --- Console : lecture -------------------------------------------------------------------


def test_console_lists_and_filters_payments_of_all_tenants(
    provision: Any, api_for: Any, admin: TestClient
) -> None:
    a = provision("alpha")
    b = provision("beta")
    pa = _declare(api_for("owner@alpha.example.com"), a.subscription_id, declared_reference="OM-1")
    pb = _declare(
        api_for("owner@beta.example.com"), b.subscription_id, declared_reference="VIR-50%_x"
    )

    page = _cget(admin, "/payments").json()
    assert page["total"] == 2
    assert [p["id"] for p in page["items"]] == [pb["id"], pa["id"]]
    item = page["items"][1]
    assert item["tenant_id"] == str(a.tenant_id)
    assert item["tenant_name"] == "Entreprise alpha"
    assert item["plan_code"] == "ENTREPRISE"
    assert item["status"] == "PENDING" and item["decided_by_email"] is None
    assert "declared_by" not in item  # identité d'un utilisateur de l'entreprise : jamais

    by_tenant = _cget(admin, "/payments", params={"tenant_id": str(b.tenant_id)}).json()
    assert [p["id"] for p in by_tenant["items"]] == [pb["id"]]
    assert _cget(admin, "/payments", params={"search": "om-"}).json()["total"] == 1
    # Recherche littérale (caractères LIKE échappés).
    assert _cget(admin, "/payments", params={"search": "50%_"}).json()["total"] == 1
    assert _cget(admin, "/payments", params={"search": "%"}).json()["total"] == 1
    assert _cget(admin, "/payments", params={"status": "CONFIRMED"}).json()["total"] == 0
    assert _cget(admin, "/payments", params={"status": "PAID"}).status_code == 422
    detail = _cget(admin, f"/payments/{pa['id']}").json()
    assert detail == item
    missing = _cget(admin, f"/payments/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "subscription_payment_not_found"


# --- Console : décisions -----------------------------------------------------------------


def test_confirmation_is_final_audited_and_does_not_activate(
    owner: Api, alpha: Any, admin: TestClient, owner_db: Session
) -> None:
    _sql(
        owner_db,
        "UPDATE subscriptions SET status = 'pending_activation', current_period_start = now(), "
        "current_period_end = now() WHERE tenant_id = :t",
        t=alpha.tenant_id,
    )
    payment = _declare(owner, alpha.subscription_id)
    before = _state(owner_db, alpha.tenant_id)

    response = _cpost(admin, f"/payments/{payment['id']}/confirm", {"reason": REASON})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "CONFIRMED"
    assert body["decided_by_email"] == PLATFORM_ADMIN_EMAIL
    assert body["decided_at"] is not None and body["rejection_reason"] is None

    row = _payment_row(owner_db, payment["id"])
    assert row.status == "CONFIRMED" and row.decided_by is not None
    # Payment CONFIRMED ≠ activation : ni l'abonnement ni l'entreprise ne changent.
    assert _state(owner_db, alpha.tenant_id) == before
    assert owner.get("/subscription").json()["effective_status"] == "pending_activation"
    tenant_view = owner.get(f"/subscription/payments/{payment['id']}").json()
    assert tenant_view["status"] == "CONFIRMED" and "decided_by" not in tenant_view

    [platform] = _platform_audit(owner_db, "subscription_payment.confirmed")
    assert platform.actor_user_id == row.decided_by
    assert platform.tenant_id == alpha.tenant_id
    assert platform.target_type == "subscription_payment"
    assert platform.target_id == payment["id"]
    assert platform.before == {"status": "PENDING"}
    assert platform.after["status"] == "CONFIRMED"
    assert platform.reason == REASON
    assert platform.data["activation"] is False and platform.data["amount"] == "10000.00"
    [mirror] = _tenant_audit(owner_db, "subscription_payment.confirmed", alpha.tenant_id)
    assert mirror.user_id is None
    assert mirror.data["actor"] == "technova"
    assert mirror.data["reason"] == REASON
    assert mirror.data["platform_audit_id"] == str(platform.id)
    assert PLATFORM_ADMIN_EMAIL not in str(mirror.data)
    # Aucune autre action (activation, licence) n'est journalisée.
    assert not _platform_audit(owner_db, "subscription.manually_activated")

    # Définitive : ni seconde confirmation, ni rejet.
    for path, body_in in (("confirm", {"reason": REASON}), ("reject", {"reason": REJECTION})):
        again = _cpost(admin, f"/payments/{payment['id']}/{path}", body_in)
        assert again.status_code == 409
        assert again.json()["code"] == "payment_already_decided"
    assert len(_platform_audit(owner_db, "subscription_payment.confirmed")) == 1
    assert not _platform_audit(owner_db, "subscription_payment.rejected")


def test_rejection_requires_a_reason_visible_to_the_tenant(
    owner: Api, alpha: Any, admin: TestClient, owner_db: Session
) -> None:
    payment = _declare(owner, alpha.subscription_id)
    for body in ({}, {"reason": ""}, {"reason": "   "}):
        response = _cpost(admin, f"/payments/{payment['id']}/reject", body)
        assert response.status_code == 422
    assert _cpost(admin, f"/payments/{payment['id']}/confirm", {}).status_code == 422
    assert _payment_row(owner_db, payment["id"]).status == "PENDING"

    before = _state(owner_db, alpha.tenant_id)
    response = _cpost(admin, f"/payments/{payment['id']}/reject", {"reason": REJECTION})
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert response.json()["rejection_reason"] == REJECTION
    assert _state(owner_db, alpha.tenant_id) == before
    tenant_view = owner.get(f"/subscription/payments/{payment['id']}").json()
    assert tenant_view["status"] == "REJECTED"
    assert tenant_view["rejection_reason"] == REJECTION
    [mirror] = _tenant_audit(owner_db, "subscription_payment.rejected", alpha.tenant_id)
    assert mirror.data["after"]["rejection_reason"] == REJECTION

    for path in ("confirm", "reject"):
        again = _cpost(admin, f"/payments/{payment['id']}/{path}", {"reason": REASON})
        assert again.status_code == 409
    # Un nouveau paiement peut être déclaré après un rejet.
    assert _declare(owner, alpha.subscription_id)["status"] == "PENDING"


def test_decision_on_unknown_payment(admin: TestClient) -> None:
    response = _cpost(admin, f"/payments/{uuid.uuid4()}/confirm", {"reason": REASON})
    assert response.status_code == 404
    assert response.json()["code"] == "subscription_payment_not_found"


@pytest.mark.parametrize(
    "failing", ["app.console.audit.record_audit", "app.console.audit.record_platform_audit"]
)
def test_decision_and_both_audits_are_atomic(
    owner: Api,
    alpha: Any,
    admin: TestClient,
    owner_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    payment = _declare(owner, alpha.subscription_id)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("écriture d'audit impossible")

    monkeypatch.setattr(failing, broken)
    with pytest.raises(RuntimeError):
        _cpost(admin, f"/payments/{payment['id']}/confirm", {"reason": REASON})
    row = _payment_row(owner_db, payment["id"])
    assert row.status == "PENDING" and row.decided_by is None and row.decided_at is None
    assert not _platform_audit(owner_db, "subscription_payment.confirmed")
    assert not _tenant_audit(owner_db, "subscription_payment.confirmed", alpha.tenant_id)


@pytest.mark.parametrize(
    "first,second",
    [("confirm", "reject"), ("confirm", "confirm"), ("reject", "reject"), ("reject", "confirm")],
)
def test_concurrent_decisions_produce_a_single_decision(
    owner: Api,
    alpha: Any,
    platform_engine: Engine,
    platform_admin: Any,
    owner_db: Session,
    first: str,
    second: str,
) -> None:
    payment_id = uuid.UUID(_declare(owner, alpha.subscription_id)["id"])
    admins = [
        PlatformActor(user_id=platform_admin(), label=PLATFORM_ADMIN_EMAIL),
        PlatformActor(
            user_id=platform_admin("ops@technova.example", "Ops"), label="ops@technova.example"
        ),
    ]
    outcomes: list[str] = []
    first_locked = threading.Event()

    def decide(action: str, actor: PlatformActor, hold: bool) -> None:
        with Session(platform_engine) as db:
            service = PaymentDecisionService(db, utcnow())
            try:
                getattr(service, action)(payment_id, REJECTION, actor, RequestMeta())
                if hold:
                    first_locked.set()
                    threading.Event().wait(0.5)  # le second attend le verrou
                db.commit()
                outcomes.append(action)
            except ConflictError as exc:
                outcomes.append(exc.code)

    one = threading.Thread(target=decide, args=(first, admins[0], True))
    one.start()
    assert first_locked.wait(5)
    two = threading.Thread(target=decide, args=(second, admins[1], False))
    two.start()
    one.join()
    two.join()
    assert sorted(outcomes) == sorted([first, "payment_already_decided"])
    row = _payment_row(owner_db, str(payment_id))
    assert row.status == ("CONFIRMED" if first == "confirm" else "REJECTED")
    assert row.decided_by == admins[0].user_id
    decided = _platform_audit(owner_db, "subscription_payment.confirmed") + _platform_audit(
        owner_db, "subscription_payment.rejected"
    )
    assert len(decided) == 1
    mirrors = _tenant_audit(
        owner_db, "subscription_payment.confirmed", alpha.tenant_id
    ) + _tenant_audit(owner_db, "subscription_payment.rejected", alpha.tenant_id)
    assert len(mirrors) == 1


# --- Console : sécurité ------------------------------------------------------------------


def test_payment_routes_are_reserved_to_platform_admins(
    console: TestClient, admin: TestClient, owner: Api, alpha: Any, owner_db: Session
) -> None:
    payment = _declare(owner, alpha.subscription_id)
    anonymous = TestClient(console.app)
    paths = [
        ("get", "/payments"),
        ("get", f"/payments/{payment['id']}"),
        ("post", f"/payments/{payment['id']}/confirm"),
        ("post", f"/payments/{payment['id']}/reject"),
    ]
    for method, path in paths:
        kwargs = {"json": {"reason": REASON}} if method == "post" else {}
        anon = getattr(anonymous, method)(
            f"{CONSOLE_PREFIX}{path}", headers=CONSOLE_HEADERS, **kwargs
        )
        assert anon.status_code == 401, path
        tenant_token = getattr(anonymous, method)(
            f"{CONSOLE_PREFIX}{path}",
            headers={"Authorization": f"Bearer {owner.token}"} | CONSOLE_HEADERS,
            **kwargs,
        )
        assert tenant_token.status_code == 401, path
    # Anti-CSRF, même pour un administrateur TechNova connecté.
    no_header = admin.post(
        f"{CONSOLE_PREFIX}/payments/{payment['id']}/confirm", json={"reason": REASON}
    )
    assert no_header.status_code == 403
    assert _payment_row(owner_db, payment["id"]).status == "PENDING"
    # Aucun paiement ne peut être créé ni supprimé par la console.
    assert _cpost(admin, "/payments", {"reason": REASON}).status_code == 405
    assert (
        admin.delete(
            f"{CONSOLE_PREFIX}/payments/{payment['id']}", headers=CONSOLE_HEADERS
        ).status_code
        == 405
    )


# --- Base de données : rôle de la console, contraintes, déclencheur, migration ----------


def test_platform_role_may_only_decide_a_pending_payment(
    owner: Api, alpha: Any, platform_engine: Engine, platform_admin: Any
) -> None:
    payment = _declare(owner, alpha.subscription_id)
    admin_id = platform_admin()
    with platform_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM subscription_payments")) == 1
        for sql in (
            "DELETE FROM subscription_payments",
            "UPDATE subscription_payments SET amount = 1",
            "UPDATE subscription_payments SET currency = 'EUR'",
            "UPDATE subscription_payments SET declared_reference = 'X'",
            "UPDATE subscription_payments SET tenant_id = gen_random_uuid()",
            "INSERT INTO subscription_payments (id) VALUES (gen_random_uuid())",
            "TRUNCATE subscription_payments",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                conn.execute(text(sql))
            conn.rollback()
        # Une décision doit aboutir à CONFIRMED / REJECTED.
        with pytest.raises(DBAPIError, match="row-level security"):
            conn.execute(
                text("UPDATE subscription_payments SET updated_at = now() WHERE id = :id"),
                {"id": payment["id"]},
            )
        conn.rollback()
        decided = conn.execute(
            text(
                "UPDATE subscription_payments SET status = 'CONFIRMED', decided_by = :u, "
                "decided_at = now() WHERE id = :id"
            ),
            {"u": admin_id, "id": payment["id"]},
        )
        assert decided.rowcount == 1
        conn.commit()
        # Ligne décidée : invisible pour la politique de décision (aucune ligne modifiée).
        again = conn.execute(
            text(
                "UPDATE subscription_payments SET status = 'REJECTED', "
                "rejection_reason = 'x' WHERE id = :id"
            ),
            {"id": payment["id"]},
        )
        assert again.rowcount == 0
        conn.commit()
    assert owner.get(f"/subscription/payments/{payment['id']}").json()["status"] == "CONFIRMED"


def test_final_decisions_are_enforced_by_the_database(
    owner: Api, alpha: Any, owner_db: Session, platform_admin: Any
) -> None:
    """Même le propriétaire du schéma ne peut modifier un paiement décidé ni les données
    déclarées d'un paiement en attente (déclencheur ``subscription_payments_final``)."""
    pending = _declare(owner, alpha.subscription_id)
    decided = _declare(owner, alpha.subscription_id)
    admin_id = platform_admin()
    _sql(
        owner_db,
        "UPDATE subscription_payments SET status = 'CONFIRMED', decided_by = :u, "
        "decided_at = now() WHERE id = :id",
        u=admin_id,
        id=decided["id"],
    )
    for sql, payment in (
        (
            "UPDATE subscription_payments SET status = 'PENDING', decided_by = NULL, "
            "decided_at = NULL WHERE id = :id",
            decided,
        ),
        (
            "UPDATE subscription_payments SET status = 'REJECTED', rejection_reason = 'x' "
            "WHERE id = :id",
            decided,
        ),
        ("UPDATE subscription_payments SET amount = 1 WHERE id = :id", pending),
        ("UPDATE subscription_payments SET currency = 'EUR' WHERE id = :id", pending),
        ("UPDATE subscription_payments SET declared_by = :u WHERE id = :id", pending),
    ):
        with pytest.raises(DBAPIError, match="final or immutable"):
            owner_db.execute(text(sql), {"id": payment["id"], "u": admin_id})
        owner_db.rollback()


@pytest.mark.parametrize(
    "assignments,constraint",
    [
        ("status = 'CONFIRMED'", "decision_consistent"),
        ("decided_at = now()", "decision_consistent"),
        ("status = 'REJECTED', decided_by = :u, decided_at = now()", "rejection_has_reason"),
        (
            "status = 'CONFIRMED', decided_by = :u, decided_at = now(), rejection_reason = 'x'",
            "rejection_has_reason",
        ),
        (
            "status = 'REJECTED', decided_by = :u, decided_at = now(), rejection_reason = ' '",
            "rejection_reason_present",
        ),
        ("status = 'PAID'", "status"),
    ],
)
def test_decision_constraints(
    owner: Api,
    alpha: Any,
    owner_db: Session,
    platform_admin: Any,
    assignments: str,
    constraint: str,
) -> None:
    payment = _declare(owner, alpha.subscription_id)
    admin_id = platform_admin()
    with pytest.raises(IntegrityError, match=constraint):
        owner_db.execute(
            text(f"UPDATE subscription_payments SET {assignments} WHERE id = :id"),
            {"id": payment["id"], "u": admin_id},
        )
    owner_db.rollback()


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        ({"amount": "0"}, "amount_positive"),
        ({"currency": "'xof'"}, "iso_currency"),
        ({"period_end": "'2026-10-01'"}, "period_ordered"),
        ({"declared_reference": "'  '"}, "reference_present"),
        ({"payment_method": "'BITCOIN'"}, "payment_method"),
    ],
)
def test_declaration_constraints(
    alpha: Any, owner_db: Session, overrides: dict[str, str], constraint: str
) -> None:
    values = {
        "amount": "1",
        "currency": "'XOF'",
        "period_end": "'2026-11-01'",
        "declared_reference": "'REF'",
        "payment_method": "'CASH'",
    } | overrides
    with pytest.raises(IntegrityError, match=constraint):
        owner_db.execute(
            text(
                "INSERT INTO subscription_payments (id, tenant_id, subscription_id, amount, "
                "currency, period_start, period_end, payment_method, declared_reference, "
                "idempotency_key, declared_by, status) VALUES (gen_random_uuid(), :t, :s, "
                f"{values['amount']}, {values['currency']}, '2026-10-01', "
                f"{values['period_end']}, {values['payment_method']}, "
                f"{values['declared_reference']}, gen_random_uuid(), :u, 'PENDING')"
            ),
            {"t": alpha.tenant_id, "s": alpha.subscription_id, "u": alpha.owner_user_id},
        )
    owner_db.rollback()


def test_payment_cannot_reference_another_tenant_subscription(
    provision: Any, owner_db: Session, owner: Api, alpha: Any
) -> None:
    beta = provision("beta")
    with pytest.raises(IntegrityError, match="fk_subscription_payments_tenant_id_subscription_id"):
        owner_db.execute(
            text(
                "INSERT INTO subscription_payments (id, tenant_id, subscription_id, amount, "
                "currency, period_start, period_end, payment_method, declared_reference, "
                "idempotency_key, declared_by, status) VALUES (gen_random_uuid(), :t, :s, 1, "
                "'XOF', '2026-10-01', '2026-11-01', 'CASH', 'REF', gen_random_uuid(), :u, "
                "'PENDING')"
            ),
            {"t": alpha.tenant_id, "s": beta.subscription_id, "u": alpha.owner_user_id},
        )
    owner_db.rollback()
    # Clé d'idempotence unique par tenant, en base.
    key = uuid.uuid4()
    _declare(owner, alpha.subscription_id, idempotency_key=str(key))
    with pytest.raises(IntegrityError, match="uq_subscription_payments_tenant_id_idempotency_key"):
        owner_db.execute(
            text(
                "INSERT INTO subscription_payments (id, tenant_id, subscription_id, amount, "
                "currency, period_start, period_end, payment_method, declared_reference, "
                "idempotency_key, declared_by, status) VALUES (gen_random_uuid(), :t, :s, 1, "
                "'XOF', '2026-10-01', '2026-11-01', 'CASH', 'REF', :k, :u, 'PENDING')"
            ),
            {"t": alpha.tenant_id, "s": alpha.subscription_id, "u": alpha.owner_user_id, "k": key},
        )
    owner_db.rollback()


def test_table_security_and_indexes(owner_db: Session) -> None:
    rls = owner_db.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'subscription_payments'"
        )
    ).one()
    assert tuple(rls) == (True, True)
    policies = {
        row.policyname: (row.cmd, list(row.roles))
        for row in owner_db.execute(
            text(
                "SELECT policyname, cmd, roles::text[] AS roles FROM pg_policies "
                "WHERE tablename = 'subscription_payments'"
            )
        )
    }
    assert policies == {
        "tenant_isolation": ("ALL", ["stockmanager_app"]),
        "platform_read": ("SELECT", ["stockmanager_platform"]),
        "platform_decide": ("UPDATE", ["stockmanager_platform"]),
    }
    indexes = set(
        owner_db.scalars(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'subscription_payments'")
        )
    )
    assert {
        "pk_subscription_payments",
        "ix_subscription_payments_tenant_id",
        "ix_subscription_payments_tenant_created",
        "ix_subscription_payments_status_created",
        "uq_subscription_payments_tenant_id_idempotency_key",
    } <= indexes

    def privileges(role: str) -> set[str]:
        return set(
            owner_db.scalars(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'subscription_payments' AND grantee = :r"
                ),
                {"r": role},
            )
        )

    assert privileges("stockmanager_app") == {"SELECT", "INSERT"}
    assert privileges("stockmanager_platform") == {"SELECT"}
    columns = set(
        owner_db.scalars(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE table_name = 'subscription_payments' "
                "AND grantee = 'stockmanager_platform' AND privilege_type = 'UPDATE'"
            )
        )
    )
    assert columns == {"status", "decided_by", "decided_at", "rejection_reason", "updated_at"}
    assert not owner_db.scalar(
        text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'stockmanager_platform'")
    )
