"""Point de vente (Phase 3.0, ADR-0023) : recherche d'articles du site, encaissement en une
étape (création + validation + paiements, une seule transaction, idempotent) réutilisant
SaleService / StockService / PaymentService / caisse / créances. Sans caisse : ventes et
paiements électroniques possibles, espèces refusées. RBAC, sites, abonnement, module,
concurrence, isolation API et SQL (RLS)."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

# --- Aides ---------------------------------------------------------------------------------------


@pytest.fixture
def priced(world: World, owner_db: Session) -> World:
    """Article 0 à 10 000 (100 en boutique, 20 au dépôt), article 1 à 2 500 (50 en boutique),
    article 2 inactif. Aucune caisse ouverte."""
    for index, price in ((0, 10000), (1, 2500)):
        owner_db.execute(
            text("UPDATE catalog_articles SET sale_price = :p WHERE id = :id"),
            {"p": price, "id": world.articles[index]},
        )
    owner_db.commit()
    sh.validated_entry(world, [(0, "100", "6000"), (1, "50", "1000")])
    sh.validated_entry(world, [(0, "20", "6000")], site_id=world.site2)
    assert world.owner.post(f"/catalog/articles/{world.articles[2]}/deactivate").status_code == 200
    return world


def _body(
    w: World,
    lines: list[tuple[int, str]],
    payments: list[tuple[str, str]] = (),  # type: ignore[assignment]
    key: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "site_id": w.site,
        "lines": [{"article_id": w.articles[i], "quantity": q} for i, q in lines],
        "payments": [{"amount": a, "method": m} for a, m in payments],
        "idempotency_key": key or str(uuid.uuid4()),
        **extra,
    }


def _checkout(api: Api, body: dict[str, Any]) -> Any:
    return api.post("/pos/checkout", json=body)


def _done(api: Api, body: dict[str, Any]) -> dict[str, Any]:
    response = _checkout(api, body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _customer(w: World, limit: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"customer_type": "INDIVIDUAL", "name": "Awa Traoré"}
    if limit is not None:
        body["credit_limit"] = limit
    return dict(w.owner.post("/customers", json=body).json())


def _custom_member(w: World, client: TestClient, email: str, permissions: list[str]) -> Api:
    role = w.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions})
    assert role.status_code == 201, role.text
    created = w.owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": [{"role_id": role.json()["id"]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _counts(owner_db: Session) -> tuple[int, int, int]:
    return (
        sh.count(owner_db, "SELECT count(*) FROM sales"),
        sh.count(owner_db, "SELECT count(*) FROM payments"),
        sh.count(owner_db, "SELECT count(*) FROM cash_movements"),
    )


def _run_concurrently(app: Any, token: str, calls: list[tuple[str, Any]]) -> list[int]:
    barrier = threading.Barrier(len(calls))
    statuses: list[int] = []

    def run(path: str, body: Any) -> None:
        with TestClient(app) as client:
            api = Api(client, token)
            barrier.wait()
            statuses.append(api.post(path, json=body).status_code)

    threads = [threading.Thread(target=run, args=call) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return sorted(statuses)


# --- Recherche d'articles -------------------------------------------------------------------------


def test_article_search_on_the_selling_site(priced: World) -> None:
    found = priced.owner.get("/pos/articles", params={"site_id": priced.site}).json()
    by_ref = {a["reference"]: a for a in found}
    assert by_ref["A-0"]["sale_price"] == "10000.00" and by_ref["A-0"]["quantity"] == "100.000"
    assert by_ref["A-1"]["sale_price"] == "2500.00" and by_ref["A-1"]["is_active"] is True
    # Article inactif : renvoyé en dernier, signalé, jamais vendable.
    assert found[-1]["reference"] == "A-2" and found[-1]["is_active"] is False
    depot = priced.owner.get("/pos/articles", params={"site_id": priced.site2}).json()
    assert {a["reference"]: a["quantity"] for a in depot}["A-0"] == "20.000"
    # Recherche serveur (référence ou désignation) et nombre limité.
    assert [
        a["reference"]
        for a in priced.owner.get(
            "/pos/articles", params={"site_id": priced.site, "search": "article 1"}
        ).json()
    ] == ["A-1"]
    assert (
        len(priced.owner.get("/pos/articles", params={"site_id": priced.site, "limit": 2}).json())
        == 2
    )
    assert (
        priced.owner.get("/pos/articles", params={"limit": 51, "site_id": priced.site}).status_code
        == 422
    )
    # Site obligatoire (aucun site sélectionné).
    missing = priced.owner.get("/pos/articles")
    assert missing.status_code == 422 and missing.json()["code"] == "site_required"


# --- Encaissement en une étape ------------------------------------------------------------------


def test_checkout_without_payment_creates_a_validated_unpaid_sale(
    priced: World, owner_db: Session
) -> None:
    result = _done(priced.owner, _body(priced, [(0, "2"), (1, "1")]))
    sale = result["sale"]
    assert sale["status"] == "VALIDATED" and sale["channel"] == "POS"
    # Prix et total recalculés par le serveur (catalogue).
    assert sale["total"] == "22500.00" and [line["unit_price"] for line in sale["lines"]] == [
        "10000.00",
        "2500.00",
    ]
    assert sale["payment_status"] == "UNPAID" and result["payments"] == []
    assert result["replayed"] is False
    assert sh.level(owner_db, priced, 0)[0] == "98.000"
    assert sh.level(owner_db, priced, 1)[0] == "49.000"
    # Même vente que dans le module Ventes (aucune seconde logique) ; filtre par canal.
    assert priced.owner.get(f"/sales/{sale['id']}").json()["number"] == sale["number"]
    pos_sales = priced.owner.get("/sales", params={"channel": "POS"}).json()
    assert [s["number"] for s in pos_sales["items"]] == [sale["number"]]
    assert priced.owner.get("/sales", params={"channel": "BACKOFFICE"}).json()["total"] == 0


def test_checkout_cash_uses_the_open_cash_session(priced: World) -> None:
    session = sh.open_cash(priced, opening_float="5000")
    result = _done(priced.owner, _body(priced, [(0, "3")], [("30000", "CASH")]))
    assert result["sale"]["payment_status"] == "PAID"
    assert [(p["method"], p["amount"]) for p in result["payments"]] == [("CASH", "30000.00")]
    journal = priced.owner.get(f"/cash/sessions/{session['id']}/movements").json()["items"]
    cash_in = [m for m in journal if m["movement_type"] == "SALE_CASH_IN"]
    assert len(cash_in) == 1 and cash_in[0]["payment_id"] == result["payments"][0]["id"]
    assert cash_in[0]["source_number"] == result["sale"]["number"]


def test_checkout_cash_without_session_changes_nothing(priced: World, owner_db: Session) -> None:
    refused = _checkout(priced.owner, _body(priced, [(0, "3")], [("30000", "CASH")]))
    assert refused.status_code == 422 and refused.json()["code"] == "cash_session_required"
    # Tout ou rien : ni vente (même brouillon), ni stock, ni paiement, ni mouvement.
    assert _counts(owner_db) == (0, 0, 0)
    assert sh.level(owner_db, priced, 0)[0] == "100.000"


def test_mixed_and_partial_payments(priced: World, owner_db: Session) -> None:
    session = sh.open_cash(priced)
    mixed = _done(
        priced.owner,
        _body(
            priced,
            [(0, "10")],
            [("40000", "CASH"), ("35000", "MOBILE_MONEY"), ("25000", "CARD")],
        ),
    )
    assert mixed["sale"]["payment_status"] == "PAID" and mixed["sale"]["paid_amount"] == "100000.00"
    assert sorted(p["method"] for p in mixed["payments"]) == ["CARD", "CASH", "MOBILE_MONEY"]
    journal = priced.owner.get(f"/cash/sessions/{session['id']}/movements").json()["items"]
    assert [m["amount"] for m in journal] == ["40000.00"]  # espèces seulement
    partial = _done(priced.owner, _body(priced, [(1, "4")], [("4000", "BANK_TRANSFER")]))
    assert partial["sale"]["payment_status"] == "PARTIALLY_PAID"
    assert partial["sale"]["remaining_amount"] == "6000.00"
    # Surpaiement : refusé, rien n'est créé.
    before = _counts(owner_db)
    over = _checkout(priced.owner, _body(priced, [(1, "1")], [("2000", "CARD"), ("1000", "CARD")]))
    assert over.status_code == 422 and over.json()["code"] == "payment_exceeds_balance"
    assert _counts(owner_db) == before


def test_electronic_payments_without_any_cash_register(priced: World) -> None:
    # Aucune caisse, puis une caisse désactivée : les ventes non espèces restent possibles.
    first = _done(priced.owner, _body(priced, [(0, "1")], [("10000", "MOBILE_MONEY")]))
    assert first["sale"]["payment_status"] == "PAID"
    register = priced.owner.post(
        "/cash/registers", json={"site_id": priced.site, "name": "Caisse arrêtée"}
    ).json()
    priced.owner.post(f"/cash/registers/{register['id']}/deactivate")
    second = _done(priced.owner, _body(priced, [(0, "1")], [("10000", "OTHER")]))
    assert second["sale"]["payment_status"] == "PAID"
    # Module Caisse désactivé : POS et ventes disponibles, espèces refusées.
    assert priced.owner.put("/modules/cash_register", json={"enabled": False}).status_code == 204
    assert _done(priced.owner, _body(priced, [(0, "1")], [("10000", "CARD")]))["sale"]
    cash = _checkout(priced.owner, _body(priced, [(0, "1")], [("10000", "CASH")]))
    assert cash.json()["code"] == "cash_session_required"


@pytest.mark.parametrize(
    ("lines", "code"),
    [
        ([(0, "101")], "insufficient_stock"),
        ([(2, "1")], "article_inactive"),
        ([(0, "1"), (0, "2")], "duplicate_article_line"),
    ],
)
def test_business_rules_are_those_of_sales(
    priced: World, owner_db: Session, lines: list[tuple[int, str]], code: str
) -> None:
    refused = _checkout(priced.owner, _body(priced, lines))
    assert refused.status_code == 422 and refused.json()["code"] == code
    assert _counts(owner_db) == (0, 0, 0)


def test_inactive_customer_and_credit_limit(priced: World, owner_db: Session) -> None:
    inactive = _customer(priced)
    priced.owner.post(f"/customers/{inactive['id']}/deactivate")
    refused = _checkout(priced.owner, _body(priced, [(0, "1")], customer_id=inactive["id"]))
    assert refused.json()["code"] == "customer_inactive"
    # Vente à crédit : limite du client contrôlée (règles et verrou des créances).
    limited = _customer(priced, limit="50000")
    over = _checkout(priced.owner, _body(priced, [(0, "10")], customer_id=limited["id"]))
    assert over.status_code == 422 and over.json()["code"] == "credit_limit_exceeded"
    assert _counts(owner_db) == (0, 0, 0)
    ok = _done(
        priced.owner,
        _body(priced, [(0, "10")], [("50000", "MOBILE_MONEY")], customer_id=limited["id"]),
    )
    assert ok["sale"]["remaining_amount"] == "50000.00"
    exposure = priced.owner.get(f"/customers/{limited['id']}/credit-exposure").json()
    assert exposure["current_exposure"] == "50000.00" and exposure["available_credit"] == "0.00"
    receivables = priced.owner.get("/receivables", params={"customer_id": limited["id"]}).json()
    assert [r["sale_number"] for r in receivables["items"]] == [ok["sale"]["number"]]
    unlimited = _customer(priced)
    assert _done(priced.owner, _body(priced, [(0, "20")], customer_id=unlimited["id"]))["sale"]


# --- Idempotence et concurrence -------------------------------------------------------------------


def test_same_cart_submitted_twice_gives_one_sale(
    priced: World, app: Any, owner_db: Session
) -> None:
    sh.open_cash(priced)
    body = _body(priced, [(0, "2")], [("20000", "CASH")])
    first = _checkout(priced.owner, body)
    replay = _checkout(priced.owner, body)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json()["replayed"] is True
    assert replay.json()["sale"]["id"] == first.json()["sale"]["id"]
    assert _counts(owner_db) == (1, 1, 1)
    # Deux envois simultanés du même panier : une seule vente, un seul paiement, un mouvement.
    other = _body(priced, [(1, "2")], [("5000", "CASH")])
    assert _run_concurrently(
        app, priced.owner.token, [("/pos/checkout", other), ("/pos/checkout", other)]
    ) == [200, 201]
    assert _counts(owner_db) == (2, 2, 2)
    assert sh.level(owner_db, priced, 1)[0] == "48.000"


def test_concurrent_checkouts_never_oversell(priced: World, app: Any, owner_db: Session) -> None:
    statuses = _run_concurrently(
        app,
        priced.owner.token,
        [
            ("/pos/checkout", _body(priced, [(0, "60")])),
            ("/pos/checkout", _body(priced, [(0, "60")])),
        ],
    )
    assert statuses == [201, 422]
    assert sh.level(owner_db, priced, 0)[0] == "40.000"
    assert sh.count(owner_db, "SELECT count(*) FROM sales") == 1


# --- Sécurité : RBAC, sites, abonnement, module ---------------------------------------------------


def test_permissions(priced: World, client: TestClient) -> None:
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    viewer = sh.member(priced, client, "consultant@example.com", "viewer", all_sites=True)
    assert _done(seller, _body(priced, [(0, "1")], [("10000", "CARD")]))["sale"]
    assert _done(manager, _body(priced, [(0, "1")]))["sale"]
    # Consultant : consulter les ventes ne donne aucune capacité de vente.
    for response in (
        viewer.get("/pos/articles", params={"site_id": priced.site}),
        _checkout(viewer, _body(priced, [(0, "1")])),
    ):
        assert response.status_code == 403 and response.json()["code"] == "permission_denied"
    # Le POS n'ouvre aucun droit : il faut aussi les permissions de vente (et d'encaissement).
    terminal_only = _custom_member(priced, client, "borne@example.com", ["pos.terminal.use"])
    assert terminal_only.get("/pos/articles", params={"site_id": priced.site}).status_code == 200
    denied = _checkout(terminal_only, _body(priced, [(0, "1")]))
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    no_payment = _custom_member(
        priced,
        client,
        "sans-encaissement@example.com",
        ["pos.terminal.use", "sales.sale.create", "sales.sale.validate"],
    )
    assert _done(no_payment, _body(priced, [(0, "1")]))["sale"]
    refused = _checkout(no_payment, _body(priced, [(0, "1")], [("10000", "CARD")]))
    assert refused.status_code == 403 and refused.json()["permissions"] == ["sales.payment.create"]
    sales_only = _custom_member(
        priced, client, "ventes@example.com", ["sales.sale.create", "sales.sale.validate"]
    )
    assert _checkout(sales_only, _body(priced, [(0, "1")])).json()["code"] == "permission_denied"


def test_site_scope(priced: World, client: TestClient) -> None:
    shop = sh.member(priced, client, "boutique@example.com", "seller", site_ids=[priced.site])
    for response in (
        shop.get("/pos/articles", params={"site_id": priced.site2}),
        _checkout(shop, _body(priced, [(0, "1")], site_id=priced.site2)),
    ):
        assert response.status_code == 403 and response.json()["code"] == "site_access_denied"
    # Site sélectionné : la vente y est rattachée ; un autre site demandé est refusé.
    shop.site_id = uuid.UUID(priced.site)
    sale = _done(shop, _body(priced, [(0, "1")], site_id=None))["sale"]
    assert sale["site_id"] == priced.site
    mismatch = _checkout(shop, _body(priced, [(0, "1")], site_id=priced.site2))
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "site_mismatch"


def test_subscription_and_module(priced: World, owner_db: Session) -> None:
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    for response in (
        priced.owner.get("/pos/articles", params={"site_id": priced.site}),
        _checkout(priced.owner, _body(priced, [(0, "1")])),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "subscription_restricted"
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() + interval '30 days'")
    )
    owner_db.execute(text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'pos'"))
    owner_db.commit()
    denied = _checkout(priced.owner, _body(priced, [(0, "1")]))
    assert denied.status_code == 403 and denied.json()["code"] == "module_unavailable"
    # Les ventes classiques ne dépendent pas du POS.
    assert priced.owner.get("/sales").status_code == 200
    owner_db.execute(text("UPDATE tenants SET status = 'suspended'"))
    owner_db.commit()
    assert _checkout(priced.owner, _body(priced, [(0, "1")])).json()["code"] == "tenant_suspended"


def test_audit_trail(priced: World) -> None:
    result = _done(priced.owner, _body(priced, [(0, "1")], [("10000", "CARD")]))
    logs = priced.owner.get("/audit-logs", params={"limit": 50}).json()["items"]
    actions = {
        log["action"]
        for log in logs
        if log["entity_id"] in {result["sale"]["id"], result["payments"][0]["id"]}
    }
    assert {"sale.created", "sale.validated", "payment.created", "payment.completed"} <= actions
    created = next(log for log in logs if log["action"] == "sale.created")
    assert created["data"]["channel"] == "POS"


# --- Multi-tenant ---------------------------------------------------------------------------------


def test_tenant_isolation(
    priced: World, provision: Any, api_for: Any, app_engine: Engine, owner_db: Session
) -> None:
    sale = _done(priced.owner, _body(priced, [(0, "1")]))["sale"]
    b = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    beta: Api = api_for("owner@beta.example.com")
    assert beta.get("/pos/articles", params={"site_id": str(b.site_id)}).json() == []
    stolen = _checkout(beta, _body(priced, [(0, "1")], site_id=str(b.site_id)))
    assert stolen.status_code == 422 and stolen.json()["code"] == "article_not_found"
    assert _checkout(beta, _body(priced, [(0, "1")])).json()["code"] == "site_access_denied"
    # Rôle applicatif réel : les ventes POS du tenant A sont invisibles depuis B.
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert (
            db.execute(text("SELECT count(*) FROM sales WHERE channel = 'POS'")).scalar_one() == 0
        )
    tenant_a = owner_db.execute(text("SELECT tenant_id FROM sales")).scalar_one()
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        row = db.execute(
            text("SELECT channel, idempotency_key IS NOT NULL FROM sales WHERE id = :id"),
            {"id": sale["id"]},
        ).one()
        assert tuple(row) == ("POS", True)
