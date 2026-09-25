"""Paiements des ventes (Phase 2.7) : paiement complet, partiel, successifs, mixte, vente sans
client, contrôles (montant, surpaiement, statut de la vente), état calculé (non payée /
partiellement payée / payée), annulation (solde recalculé, vente toujours validée, stock
inchangé), annulation d'une vente encaissée refusée, RBAC, sites, abonnement, module,
concurrence, double soumission (clé d'idempotence), audit, isolation API et SQL (RLS)."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

# --- Aides ---------------------------------------------------------------------------------------


@pytest.fixture
def priced(world: World, owner_db: Session) -> World:
    """Article 0 vendu 10 000 (vente de 10 = 100 000) ; 50 en stock sur le site principal."""
    owner_db.execute(
        text("UPDATE catalog_articles SET sale_price = 10000 WHERE id = :id"),
        {"id": world.articles[0]},
    )
    owner_db.commit()
    sh.validated_entry(world, [(0, "50", "6000")])
    sh.open_cash(world)  # paiements espèces : caisse ouverte sur le site (Phase 2.9)
    return world


def _sale(
    w: World, quantity: str = "10", validate: bool = True, api: Api | None = None, **extra: Any
) -> dict[str, Any]:
    client = api or w.owner
    created = client.post(
        "/sales",
        json={
            "site_id": w.site,
            "lines": [{"article_id": w.articles[0], "quantity": quantity}],
            **extra,
        },
    )
    assert created.status_code == 201, created.text
    sale = dict(created.json())
    if validate:
        validated = client.post(f"/sales/{sale['id']}/validate")
        assert validated.status_code == 200, validated.text
        sale = dict(validated.json())
    return sale


def _pay(api: Api, sale: dict[str, Any], amount: str, method: str = "CASH", **extra: Any) -> Any:
    return api.post(
        f"/sales/{sale['id']}/payments", json={"amount": amount, "method": method, **extra}
    )


def _paid(api: Api, sale: dict[str, Any], amount: str, method: str = "CASH") -> dict[str, Any]:
    response = _pay(api, sale, amount, method)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _history(api: Api, sale: dict[str, Any]) -> dict[str, Any]:
    response = api.get(f"/sales/{sale['id']}/payments")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _summary(api: Api, sale: dict[str, Any]) -> tuple[str, str, str]:
    s = _history(api, sale)["summary"]
    return s["paid_amount"], s["remaining_amount"], s["payment_status"]


def _cancel(api: Api, sale: dict[str, Any], payment: dict[str, Any], reason: str) -> Any:
    return api.post(f"/sales/{sale['id']}/payments/{payment['id']}/cancel", json={"reason": reason})


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


# --- Paiement complet, partiel, successifs, mixte -----------------------------------------------


def test_full_payment(priced: World, owner_db: Session) -> None:
    sale = _sale(priced)
    assert sale["status"] == "VALIDATED" and sale["total"] == "100000.00"
    # Validée sans aucun paiement : stock déjà sorti, non payée.
    assert sh.level(owner_db, priced, 0)[0] == "40.000"
    assert (sale["paid_amount"], sale["remaining_amount"], sale["payment_status"]) == (
        "0.00",
        "100000.00",
        "UNPAID",
    )
    history = _history(priced.owner, sale)
    assert history["items"] == [] and history["sale_status"] == "VALIDATED"
    assert history["summary"]["payment_status"] == "UNPAID"

    payment = _paid(priced.owner, sale, "100000")
    assert payment["number"] == "PAY-000001" and payment["status"] == "COMPLETED"
    assert payment["amount"] == "100000.00" and payment["method"] == "CASH"
    assert payment["sale_number"] == sale["number"] and payment["site_id"] == priced.site
    assert payment["created_by_name"] and payment["paid_at"]
    assert _summary(priced.owner, sale) == ("100000.00", "0.00", "PAID")
    detail = priced.owner.get(f"/sales/{sale['id']}").json()
    assert detail["status"] == "VALIDATED" and detail["payment_status"] == "PAID"
    assert detail["remaining_amount"] == "0.00"
    # Le paiement ne touche jamais au stock.
    assert sh.level(owner_db, priced, 0)[0] == "40.000"
    assert priced.owner.get(f"/sales/{sale['id']}/payments/{payment['id']}").json() == payment


def test_partial_successive_and_mixed_payments(priced: World) -> None:
    sale = _sale(priced)
    _paid(priced.owner, sale, "30000", "CASH")
    assert _summary(priced.owner, sale) == ("30000.00", "70000.00", "PARTIALLY_PAID")
    assert priced.owner.get(f"/sales/{sale['id']}").json()["status"] == "VALIDATED"
    mobile = _pay(
        priced.owner,
        sale,
        "20000",
        "MOBILE_MONEY",
        provider="Orange Money",
        reference="OM-123456",
    )
    assert mobile.status_code == 201, mobile.text
    assert (mobile.json()["provider"], mobile.json()["reference"]) == ("Orange Money", "OM-123456")
    assert _summary(priced.owner, sale) == ("50000.00", "50000.00", "PARTIALLY_PAID")
    _paid(priced.owner, sale, "50000", "CARD")
    assert _summary(priced.owner, sale) == ("100000.00", "0.00", "PAID")
    # Chaque paiement est conservé individuellement, dans l'ordre d'encaissement.
    items = _history(priced.owner, sale)["items"]
    assert [(p["number"], p["method"], p["amount"]) for p in items] == [
        ("PAY-000001", "CASH", "30000.00"),
        ("PAY-000002", "MOBILE_MONEY", "20000.00"),
        ("PAY-000003", "CARD", "50000.00"),
    ]


def test_anonymous_sale_and_customer_receivable(priced: World) -> None:
    counter = _sale(priced, "5")  # vente comptoir, sans client
    assert counter["customer_id"] is None
    _paid(priced.owner, counter, "50000")
    assert _summary(priced.owner, counter)[2] == "PAID"
    customer = priced.owner.post(
        "/customers", json={"customer_type": "INDIVIDUAL", "name": "Awa Traoré"}
    ).json()
    credit = _sale(priced, "3", customer_id=customer["id"])
    _paid(priced.owner, credit, "10000", "BANK_TRANSFER")
    # Reste dû identifiable (future créance) : filtre de la liste des ventes.
    partial = priced.owner.get("/sales", params={"payment_status": "PARTIALLY_PAID"}).json()
    assert [s["number"] for s in partial["items"]] == [credit["number"]]
    assert partial["items"][0]["customer_id"] == customer["id"]
    assert partial["items"][0]["remaining_amount"] == "20000.00"
    paid = priced.owner.get("/sales", params={"payment_status": "PAID"}).json()
    assert [s["number"] for s in paid["items"]] == [counter["number"]]
    unpaid_sale = _sale(priced, "1")
    unpaid = priced.owner.get("/sales", params={"payment_status": "UNPAID"}).json()
    assert [s["number"] for s in unpaid["items"]] == [unpaid_sale["number"]]


# --- Contrôles ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"amount": "0", "method": "CASH"},
        {"amount": "-5", "method": "CASH"},
        {"amount": "10.001", "method": "CASH"},
        {"amount": "abc", "method": "CASH"},
        {"amount": "10", "method": "BITCOIN"},
        {"method": "CASH"},
    ],
)
def test_invalid_payment_input(priced: World, body: dict[str, Any]) -> None:
    sale = _sale(priced)
    response = priced.owner.post(f"/sales/{sale['id']}/payments", json=body)
    assert response.status_code == 422
    assert _history(priced.owner, sale)["items"] == []


def test_no_overpayment(priced: World) -> None:
    sale = _sale(priced)
    _paid(priced.owner, sale, "80000")
    over = _pay(priced.owner, sale, "30000")
    assert over.status_code == 422 and over.json()["code"] == "payment_exceeds_balance"
    assert over.json()["remaining"] == "20000.00"
    _paid(priced.owner, sale, "20000")
    done = _pay(priced.owner, sale, "1")
    assert done.status_code == 422 and done.json()["code"] == "sale_already_paid"
    assert _summary(priced.owner, sale) == ("100000.00", "0.00", "PAID")


def test_draft_and_cancelled_sales_cannot_be_paid(priced: World) -> None:
    draft = _sale(priced, validate=False)
    refused = _pay(priced.owner, draft, "1000")
    assert refused.status_code == 409 and refused.json()["code"] == "sale_not_payable"
    assert refused.json()["status"] == "DRAFT"
    assert _history(priced.owner, draft)["summary"] is None
    cancelled = _sale(priced)
    assert (
        priced.owner.post(
            f"/sales/{cancelled['id']}/cancel", json={"reason": "Erreur de saisie"}
        ).status_code
        == 200
    )
    refused = _pay(priced.owner, cancelled, "1000")
    assert refused.status_code == 409 and refused.json()["status"] == "CANCELLED"


# --- Annulation d'un paiement ---------------------------------------------------------------------


def test_cancel_payment_recalculates_balance_keeps_sale_and_stock(
    priced: World, owner_db: Session
) -> None:
    sale = _sale(priced)
    first = _paid(priced.owner, sale, "60000")
    second = _paid(priced.owner, sale, "40000", "MOBILE_MONEY")
    assert _summary(priced.owner, sale)[2] == "PAID"
    stock_before = sh.level(owner_db, priced, 0)

    short = _cancel(priced.owner, sale, second, "no")
    assert short.status_code == 422
    cancelled = _cancel(priced.owner, sale, second, "Erreur de montant")
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "CANCELLED" and body["cancellation_reason"] == "Erreur de montant"
    assert body["cancelled_by_name"] and body["cancelled_at"]
    assert body["amount"] == "40000.00"  # montant historique conservé
    assert _summary(priced.owner, sale) == ("60000.00", "40000.00", "PARTIALLY_PAID")
    # Historique conservé, vente toujours validée, stock inchangé (aucune remise en stock).
    assert [p["status"] for p in _history(priced.owner, sale)["items"]] == [
        "COMPLETED",
        "CANCELLED",
    ]
    assert priced.owner.get(f"/sales/{sale['id']}").json()["status"] == "VALIDATED"
    assert sh.level(owner_db, priced, 0) == stock_before
    assert (
        sh.count(
            owner_db, "SELECT count(*) FROM stock_movements WHERE movement_type = 'CANCELLATION'"
        )
        == 0
    )
    again = _cancel(priced.owner, sale, second, "Encore une fois")
    assert again.status_code == 409 and again.json()["code"] == "payment_already_cancelled"
    # Correction : nouveau paiement correct ; annulation du premier → non payée.
    _paid(priced.owner, sale, "40000", "CASH")
    assert _summary(priced.owner, sale)[2] == "PAID"
    _cancel(priced.owner, sale, first, "Doublon de caisse")
    assert _summary(priced.owner, sale) == ("40000.00", "60000.00", "PARTIALLY_PAID")
    unknown = priced.owner.post(
        f"/sales/{sale['id']}/payments/{uuid.uuid4()}/cancel", json={"reason": "Inconnu"}
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "payment_not_found"
    # Aucune modification ni suppression d'un paiement par l'API.
    url = f"/sales/{sale['id']}/payments/{first['id']}"
    assert priced.owner.put(url, json={"amount": "1"}).status_code == 405
    assert priced.owner.delete(url).status_code == 405


def test_cancelled_payment_back_to_unpaid(priced: World) -> None:
    sale = _sale(priced)
    payment = _paid(priced.owner, sale, "100000")
    _cancel(priced.owner, sale, payment, "Chèque refusé")
    assert _summary(priced.owner, sale) == ("0.00", "100000.00", "UNPAID")


def test_paid_sale_cannot_be_cancelled_before_its_payments(
    priced: World, owner_db: Session
) -> None:
    sale = _sale(priced)
    payment = _paid(priced.owner, sale, "30000")
    refused = priced.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur client"})
    assert refused.status_code == 409 and refused.json()["code"] == "sale_has_payments"
    assert sh.level(owner_db, priced, 0)[0] == "40.000"  # rien n'a bougé
    _cancel(priced.owner, sale, payment, "Remboursé en espèces")
    cancelled = priced.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur client"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert sh.level(owner_db, priced, 0)[0] == "50.000"


# --- Concurrence et double soumission -------------------------------------------------------------


def _run_concurrently(app: Any, token: str, calls: list[tuple[str, dict[str, Any]]]) -> list[int]:
    barrier = threading.Barrier(len(calls))
    statuses: list[int] = []

    def run(path: str, body: dict[str, Any]) -> None:
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


def test_concurrent_payments_never_exceed_the_total(priced: World, app: Any) -> None:
    """Solde 100 000 ; deux encaissements simultanés de 70 000 : un seul est accepté."""
    sale = _sale(priced)
    path = f"/sales/{sale['id']}/payments"
    body = {"amount": "70000", "method": "CASH"}
    statuses = _run_concurrently(app, priced.owner.token, [(path, body), (path, body)])
    assert statuses == [201, 422]
    assert _summary(priced.owner, sale) == ("70000.00", "30000.00", "PARTIALLY_PAID")
    assert len(_history(priced.owner, sale)["items"]) == 1


def test_double_submission_with_idempotency_key(priced: World, app: Any) -> None:
    sale = _sale(priced)
    key = str(uuid.uuid4())
    first = _pay(priced.owner, sale, "25000", idempotency_key=key)
    assert first.status_code == 201
    replay = _pay(priced.owner, sale, "25000", idempotency_key=key)
    assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
    assert _summary(priced.owner, sale)[0] == "25000.00"
    # Même clé pour un autre paiement : refus explicite.
    reused = _pay(priced.owner, sale, "30000", idempotency_key=key)
    assert reused.status_code == 409 and reused.json()["code"] == "idempotency_key_reused"
    # Deux envois simultanés de la même saisie : un seul paiement.
    other = str(uuid.uuid4())
    path = f"/sales/{sale['id']}/payments"
    body = {"amount": "10000", "method": "CARD", "idempotency_key": other}
    assert _run_concurrently(app, priced.owner.token, [(path, body), (path, body)]) == [200, 201]
    assert _summary(priced.owner, sale)[0] == "35000.00"
    assert len(_history(priced.owner, sale)["items"]) == 2


def test_payment_and_sale_cancellation_are_serialized(priced: World, app: Any) -> None:
    """Paiement et annulation de la vente simultanés : jamais un paiement sur une vente
    annulée (verrou de la vente)."""
    sale = _sale(priced)
    statuses = _run_concurrently(
        app,
        priced.owner.token,
        [
            (f"/sales/{sale['id']}/payments", {"amount": "10000", "method": "CASH"}),
            (f"/sales/{sale['id']}/cancel", {"reason": "Erreur de saisie"}),
        ],
    )
    detail = priced.owner.get(f"/sales/{sale['id']}").json()
    history = _history(priced.owner, sale)["items"]
    if detail["status"] == "CANCELLED":  # annulation d'abord : paiement refusé
        assert statuses == [200, 409] and history == []
    else:  # paiement d'abord : annulation refusée (vente encaissée)
        assert statuses == [201, 409] and len(history) == 1


# --- Sécurité : RBAC, sites, abonnement, module ---------------------------------------------------


def test_permissions_by_base_role(priced: World, client: TestClient) -> None:
    admin = sh.member(priced, client, "admin@example.com", "administrator", all_sites=True)
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    viewer = sh.member(priced, client, "consultant@example.com", "viewer", all_sites=True)
    sale = _sale(priced)
    # Vendeur et Gestionnaire : consultation et encaissement, sans annulation.
    for api in (seller, manager):
        payment = _paid(api, sale, "10000")
        denied = _cancel(api, sale, payment, "Tentative")
        assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    # Consultant : consultation seule.
    assert len(_history(viewer, sale)["items"]) == 2
    refused = _pay(viewer, sale, "1000")
    assert refused.status_code == 403 and refused.json()["code"] == "permission_denied"
    # Administrateur : annulation.
    payment = _history(admin, sale)["items"][0]
    assert _cancel(admin, sale, payment, "Erreur de caisse").status_code == 200


def test_custom_role_permissions(priced: World, client: TestClient) -> None:
    cashier = _custom_member(
        priced, client, "caisse@example.com", ["sales.payment.view", "sales.payment.create"]
    )
    sale = _sale(priced)
    payment = _paid(cashier, sale, "5000")
    assert _cancel(cashier, sale, payment, "Tentative").status_code == 403
    reader = _custom_member(priced, client, "lecteur@example.com", ["sales.sale.view"])
    assert reader.get(f"/sales/{sale['id']}/payments").json()["code"] == "permission_denied"


def test_site_scope(priced: World, client: TestClient) -> None:
    sh.validated_entry(priced, [(0, "20", "6000")], site_id=priced.site2)
    depot_sale = _sale(priced, "2", site_id=priced.site2)
    sh.open_cash(priced, priced.site2, name="Caisse dépôt")
    shop = sh.member(priced, client, "boutique@example.com", "seller", site_ids=[priced.site])
    for response in (
        shop.get(f"/sales/{depot_sale['id']}/payments"),
        _pay(shop, depot_sale, "1000"),
    ):
        assert response.status_code == 404 and response.json()["code"] == "sale_not_found"
    # Site sélectionné (X-Site-Id) différent du site de la vente : refus explicite.
    priced.owner.site_id = uuid.UUID(priced.site)
    mismatch = _pay(priced.owner, depot_sale, "1000")
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "site_mismatch"
    priced.owner.site_id = uuid.UUID(priced.site2)
    assert _pay(priced.owner, depot_sale, "1000").json()["site_id"] == priced.site2


def test_expired_subscription_allows_read_only(priced: World, owner_db: Session) -> None:
    sale = _sale(priced)
    payment = _paid(priced.owner, sale, "10000")
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert _history(priced.owner, sale)["summary"]["paid_amount"] == "10000.00"
    for response in (
        _pay(priced.owner, sale, "1000"),
        _cancel(priced.owner, sale, payment, "Motif valable"),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "subscription_restricted"


def test_module_deactivation_blocks_payments(priced: World, owner_db: Session) -> None:
    sale = _sale(priced)
    owner_db.execute(text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'sales'"))
    owner_db.commit()
    denied = _pay(priced.owner, sale, "1000")
    assert denied.status_code == 403 and denied.json()["code"] == "module_unavailable"


# --- Audit ----------------------------------------------------------------------------------------


def test_audit_trail(priced: World) -> None:
    sale = _sale(priced)
    payment = _paid(priced.owner, sale, "30000", "MOBILE_MONEY")
    _cancel(priced.owner, sale, payment, "Mauvais montant")
    logs = priced.owner.get("/audit-logs", params={"action": "payment.", "limit": 50}).json()
    by_action = {log["action"]: log for log in logs["items"]}
    assert set(by_action) == {"payment.created", "payment.completed", "payment.cancelled"}
    for log in by_action.values():
        assert log["entity_type"] == "payment" and log["entity_id"] == payment["id"]
        assert log["site_id"] == priced.site and log["user_id"]
        data = log["data"]
        assert data["number"] == payment["number"] and data["sale_number"] == sale["number"]
        assert data["amount"] == "30000.00" and data["method"] == "MOBILE_MONEY"
        assert data["paid_at"]
    assert by_action["payment.completed"]["data"]["remaining_amount"] == "70000.00"
    cancelled = by_action["payment.cancelled"]["data"]
    assert cancelled["reason"] == "Mauvais montant"
    assert cancelled["payment_status"] == "UNPAID" and cancelled["paid_amount"] == "0.00"


# --- Multi-tenant ---------------------------------------------------------------------------------


def test_isolation_between_tenants_api(priced: World, provision: Any, api_for: Any) -> None:
    sale = _sale(priced)
    payment = _paid(priced.owner, sale, "10000")
    provision("beta", profile="quincaillerie", plan="ENTREPRISE")
    beta: Api = api_for("owner@beta.example.com")
    for response in (
        beta.get(f"/sales/{sale['id']}/payments"),
        beta.get(f"/sales/{sale['id']}/payments/{payment['id']}"),
        _pay(beta, sale, "1000"),
        _cancel(beta, sale, payment, "Piratage"),
    ):
        assert response.status_code == 404 and response.json()["code"] == "sale_not_found"
    assert _summary(priced.owner, sale)[0] == "10000.00"


def test_isolation_with_app_role_and_rls(
    priced: World, provision: Any, app_engine: Engine, owner_db: Session
) -> None:
    sale = _sale(priced)
    payment = _paid(priced.owner, sale, "10000")
    b = provision("beta")
    tenant_a = owner_db.execute(text("SELECT tenant_id FROM payments")).scalar_one()
    with create_session_factory(app_engine)() as db:
        assert db.execute(text("SELECT count(*) FROM payments")).scalar_one() == 0
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.execute(text("SELECT count(*) FROM payments")).scalar_one() == 0
        updated = db.execute(
            text("UPDATE payments SET amount = 1 WHERE id = :id"), {"id": payment["id"]}
        )
        assert updated.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO payments (id, tenant_id, number, sale_id, site_id, amount, "
                    "method, status, paid_at) VALUES (:id, :tenant, 'PAY-X', :sale, :site, 1, "
                    "'CASH', 'COMPLETED', now())"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_a, "sale": sale["id"], "site": priced.site},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM payments"))  # jamais de suppression physique
    # Contraintes en base (session propriétaire, hors application) : site différent de celui de
    # la vente (FK composite), montant nul, annulation sans motif.
    insert = (
        "INSERT INTO payments (id, tenant_id, number, sale_id, site_id, amount, method, status, "
        "paid_at, cancelled_at) VALUES (:id, :tenant, :number, :sale, :site, :amount, 'CASH', "
        ":status, now(), NULL)"
    )
    for site, amount, status in (
        (priced.site2, "10", "COMPLETED"),
        (priced.site, "0", "COMPLETED"),
        (priced.site, "10", "CANCELLED"),
    ):
        with pytest.raises(IntegrityError):
            owner_db.execute(
                text(insert),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "number": f"PAY-{uuid.uuid4().hex[:6]}",
                    "sale": sale["id"],
                    "site": site,
                    "amount": amount,
                    "status": status,
                },
            )
        owner_db.rollback()
