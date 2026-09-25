"""Créances / comptes clients (Phase 2.8, ADR-0021) : créances calculées (ventes validées dont le
reste dû est positif), paiements annulés exclus, ventes brouillon / annulées exclues, client
inactif, filtres, tri, pagination, indicateurs, détail, exposition crédit, limite de crédit à la
validation (NULL, 0, 100 000, 500 000, encaissement immédiat), concurrence, RBAC, sites,
abonnement, module, audit, isolation API et SQL (RLS)."""

import threading
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.modules.sales.api import customer_exposure, open_receivables_query
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

# --- Aides ---------------------------------------------------------------------------------------


@pytest.fixture
def priced(world: World, owner_db: Session) -> World:
    """Article 0 vendu 10 000 (10 unités = 100 000) ; 200 en stock sur le site principal, 100
    au dépôt."""
    owner_db.execute(
        text("UPDATE catalog_articles SET sale_price = 10000 WHERE id = :id"),
        {"id": world.articles[0]},
    )
    owner_db.commit()
    sh.validated_entry(world, [(0, "200", "6000")])
    sh.validated_entry(world, [(0, "100", "6000")], site_id=world.site2)
    # Paiements espèces : caisse ouverte sur chaque site (Phase 2.9).
    sh.open_cash(world)
    sh.open_cash(world, world.site2, name="Caisse dépôt")
    return world


def _customer(w: World, name: str = "Awa Traoré", limit: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"customer_type": "INDIVIDUAL", "name": name}
    if limit is not None:
        body["credit_limit"] = limit
    response = w.owner.post("/customers", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _draft(
    w: World, amount: int, customer: dict[str, Any] | None = None, api: Api | None = None, **extra
) -> dict[str, Any]:
    """Brouillon de ``amount`` F (article à 10 000)."""
    body: dict[str, Any] = {
        "site_id": w.site,
        "lines": [{"article_id": w.articles[0], "quantity": str(amount // 10000)}],
        **extra,
    }
    if customer is not None:
        body["customer_id"] = customer["id"]
    response = (api or w.owner).post("/sales", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _validate(w: World, sale: dict[str, Any], *payments: tuple[str, str], api: Api | None = None):
    body = {"payments": [{"amount": a, "method": m} for a, m in payments]} if payments else None
    return (api or w.owner).post(f"/sales/{sale['id']}/validate", json=body)


def _sale(
    w: World, amount: int, customer: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    sale = _draft(w, amount, customer, **extra)
    response = _validate(w, sale)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _pay(w: World, sale: dict[str, Any], amount: str, method: str = "CASH") -> dict[str, Any]:
    response = w.owner.post(
        f"/sales/{sale['id']}/payments", json={"amount": amount, "method": method}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _receivables(api: Api, **params: Any) -> dict[str, Any]:
    response = api.get("/receivables", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _open(api: Api, **params: Any) -> dict[str, str]:
    """Créances ouvertes visibles : numéro de vente → reste dû."""
    items = _receivables(api, limit=200, **params)["items"]
    return {r["sale_number"]: r["remaining_amount"] for r in items}


def _exposure(api: Api, customer: dict[str, Any]) -> dict[str, Any]:
    response = api.get(f"/customers/{customer['id']}/credit-exposure")
    assert response.status_code == 200, response.text
    return dict(response.json())


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


# --- Calcul ---------------------------------------------------------------------------------------


def test_receivable_equals_total_minus_completed_payments(priced: World) -> None:
    customer = _customer(priced)
    unpaid = _sale(priced, 100000, customer)
    partial = _sale(priced, 100000, customer)
    paid = _sale(priced, 100000, customer)
    _pay(priced, partial, "40000")
    _pay(priced, paid, "100000")
    # 100 000 sans paiement → 100 000 ; 40 000 payés → 60 000 ; payée → aucune créance.
    assert _open(priced.owner) == {
        unpaid["number"]: "100000.00",
        partial["number"]: "60000.00",
    }
    items = {r["sale_number"]: r for r in _receivables(priced.owner)["items"]}
    row = items[partial["number"]]
    assert (row["total"], row["paid_amount"], row["payment_status"]) == (
        "100000.00",
        "40000.00",
        "PARTIALLY_PAID",
    )
    assert row["customer_id"] == customer["id"] and row["customer_name"] == "Awa Traoré"
    assert row["customer_code"] == customer["code"] and row["customer_is_active"] is True
    assert row["site_id"] == priced.site and row["site_name"]
    assert row["sale_date"] and row["validated_at"]
    assert items[unpaid["number"]]["payment_status"] == "UNPAID"
    # Surpaiement impossible (règles de la Phase 2.7) : la créance reste cohérente.
    over = priced.owner.post(
        f"/sales/{unpaid['id']}/payments", json={"amount": "120000", "method": "CASH"}
    )
    assert over.status_code == 422 and over.json()["code"] == "payment_exceeds_balance"
    summary = priced.owner.get("/receivables/summary").json()
    assert summary == {
        "total_receivables": "160000.00",
        "receivables_count": 2,
        "debtor_customers_count": 1,
    }
    exposure = _exposure(priced.owner, customer)
    assert exposure["current_exposure"] == "160000.00"
    assert exposure["open_receivables_count"] == 2


def test_cancelled_payment_is_excluded_and_restores_the_receivable(priced: World) -> None:
    customer = _customer(priced)
    sale = _sale(priced, 100000, customer)
    payment = _pay(priced, sale, "60000")
    assert _open(priced.owner)[sale["number"]] == "40000.00"
    cancelled = priced.owner.post(
        f"/sales/{sale['id']}/payments/{payment['id']}/cancel", json={"reason": "Erreur de caisse"}
    )
    assert cancelled.status_code == 200
    assert _open(priced.owner)[sale["number"]] == "100000.00"
    # Détail : le paiement annulé reste dans l'historique mais ne réduit pas le solde.
    detail = priced.owner.get(f"/receivables/{sale['id']}").json()
    assert detail["is_open"] is True and detail["remaining_amount"] == "100000.00"
    assert detail["paid_amount"] == "0.00" and detail["payment_status"] == "UNPAID"
    assert [(p["number"], p["status"], p["amount"]) for p in detail["payments"]] == [
        (payment["number"], "CANCELLED", "60000.00")
    ]
    assert detail["payments"][0]["cancellation_reason"] == "Erreur de caisse"
    # Paiement final : la créance disparaît des créances ouvertes, le détail reste consultable.
    _pay(priced, sale, "100000", "MOBILE_MONEY")
    assert sale["number"] not in _open(priced.owner)
    closed = priced.owner.get(f"/receivables/{sale['id']}").json()
    assert closed["is_open"] is False and closed["remaining_amount"] == "0.00"
    assert [p["status"] for p in closed["payments"]] == ["CANCELLED", "COMPLETED"]


def test_draft_and_cancelled_sales_are_never_receivables(priced: World) -> None:
    customer = _customer(priced)
    draft = _draft(priced, 50000, customer)
    cancelled = _sale(priced, 30000, customer)
    response = priced.owner.post(f"/sales/{cancelled['id']}/cancel", json={"reason": "Erreur"})
    assert response.status_code == 200
    assert _open(priced.owner) == {}
    assert _exposure(priced.owner, customer)["current_exposure"] == "0.00"
    for sale in (draft, cancelled, {"id": str(uuid.uuid4())}):
        missing = priced.owner.get(f"/receivables/{sale['id']}")
        assert missing.status_code == 404 and missing.json()["code"] == "receivable_not_found"


def test_sale_without_customer_is_a_receivable_without_debtor(priced: World) -> None:
    counter = _sale(priced, 20000)
    customer = _customer(priced)
    _sale(priced, 30000, customer)
    items = {r["sale_number"]: r for r in _receivables(priced.owner)["items"]}
    assert items[counter["number"]]["customer_id"] is None
    assert items[counter["number"]]["customer_name"] is None
    summary = priced.owner.get("/receivables/summary").json()
    # Toujours un encaissement manquant, mais sans débiteur identifié.
    assert summary["total_receivables"] == "50000.00" and summary["receivables_count"] == 2
    assert summary["debtor_customers_count"] == 1


def test_inactive_customer_history_is_kept(priced: World) -> None:
    customer = _customer(priced, limit="500000")
    sale = _sale(priced, 80000, customer)
    _pay(priced, sale, "30000")
    assert priced.owner.post(f"/customers/{customer['id']}/deactivate").status_code == 200
    row = _receivables(priced.owner)["items"][0]
    assert row["sale_number"] == sale["number"] and row["remaining_amount"] == "50000.00"
    assert row["customer_is_active"] is False
    listed = priced.owner.get(f"/customers/{customer['id']}/receivables").json()
    assert [r["sale_number"] for r in listed["items"]] == [sale["number"]]
    exposure = _exposure(priced.owner, customer)
    assert exposure["customer_is_active"] is False and exposure["current_exposure"] == "50000.00"
    assert priced.owner.get(f"/receivables/{sale['id']}").status_code == 200
    # Règle existante : aucune nouvelle vente pour un client désactivé.
    refused = priced.owner.post(
        "/sales",
        json={
            "site_id": priced.site,
            "customer_id": customer["id"],
            "lines": [{"article_id": priced.articles[0], "quantity": "1"}],
        },
    )
    assert refused.status_code == 422 and refused.json()["code"] == "customer_inactive"


# --- Liste : filtres, tri, pagination, indicateurs ------------------------------------------------


def test_filters_sort_pagination_and_summary(priced: World) -> None:
    awa = _customer(priced, "Awa Traoré")
    moussa = _customer(priced, "Moussa Konaté")
    a1 = _sale(priced, 100000, awa, sale_date="2026-09-01")
    a2 = _sale(priced, 50000, awa)
    m1 = _sale(priced, 30000, moussa, site_id=priced.site2)
    _pay(priced, a2, "20000")
    everything = _receivables(priced.owner)
    assert everything["total"] == 3
    # Tri par défaut : les plus anciennes d'abord.
    assert everything["items"][0]["sale_number"] == a1["number"]
    by_remaining = _receivables(priced.owner, sort="-remaining_amount")["items"]
    assert [r["remaining_amount"] for r in by_remaining] == ["100000.00", "30000.00", "30000.00"]
    by_customer = _receivables(priced.owner, sort="customer_name")["items"]
    assert by_customer[-1]["customer_name"] == "Moussa Konaté"
    bad_sort = priced.owner.get("/receivables", params={"sort": "tenant_id"})
    assert bad_sort.status_code == 400 and bad_sort.json()["code"] == "invalid_sort"
    # Pagination serveur.
    page = _receivables(priced.owner, limit=2, offset=2)
    assert page["total"] == 3 and len(page["items"]) == 1 and page["offset"] == 2
    # Filtres.
    assert set(_open(priced.owner, customer_id=awa["id"])) == {a1["number"], a2["number"]}
    assert set(_open(priced.owner, site_id=priced.site2)) == {m1["number"]}
    assert set(_open(priced.owner, date_to="2026-09-01")) == {a1["number"]}
    assert set(_open(priced.owner, date_from="2026-09-02")) == {a2["number"], m1["number"]}
    assert set(_open(priced.owner, search="moussa")) == {m1["number"]}
    assert set(_open(priced.owner, search=a2["number"])) == {a2["number"]}
    assert set(_open(priced.owner, min_amount="50000")) == {a1["number"]}
    assert set(_open(priced.owner, max_amount="30000")) == {a2["number"], m1["number"]}
    assert set(_open(priced.owner, status="PARTIALLY_PAID")) == {a2["number"]}
    assert set(_open(priced.owner, status="UNPAID")) == {a1["number"], m1["number"]}
    invalid = priced.owner.get("/receivables", params={"status": "PAID"})
    assert invalid.status_code == 422
    assert priced.owner.get("/receivables", params={"min_amount": "-1"}).status_code == 422
    # Indicateurs : mêmes filtres que la liste ; aucun indicateur de retard (pas d'échéance).
    summary = priced.owner.get("/receivables/summary", params={"customer_id": awa["id"]}).json()
    assert summary == {
        "total_receivables": "130000.00",
        "receivables_count": 2,
        "debtor_customers_count": 1,
    }
    assert priced.owner.get("/receivables/summary").json()["debtor_customers_count"] == 2
    # Créances d'un client : même liste, restreinte au client.
    customer_list = priced.owner.get(f"/customers/{moussa['id']}/receivables").json()
    assert [r["sale_number"] for r in customer_list["items"]] == [m1["number"]]
    unknown = priced.owner.get(f"/customers/{uuid.uuid4()}/receivables")
    assert unknown.status_code == 404 and unknown.json()["code"] == "customer_not_found"


# --- Exposition et limite de crédit ---------------------------------------------------------------


def test_credit_exposure_representation(priced: World) -> None:
    no_limit = _customer(priced, "Sans limite")
    _sale(priced, 100000, no_limit)
    exposure = _exposure(priced.owner, no_limit)
    # Limite NULL = non configurée : aucun montant disponible fabriqué.
    assert exposure["credit_limit"] is None and exposure["limit_configured"] is False
    assert exposure["available_credit"] is None and exposure["over_limit"] is False
    assert exposure["current_exposure"] == "100000.00" and exposure["consolidated"] is True

    limited = _customer(priced, "Limité", limit="500000")
    sale = _sale(priced, 400000, limited)
    _pay(priced, sale, "80000")
    exposure = _exposure(priced.owner, limited)
    assert (exposure["credit_limit"], exposure["current_exposure"]) == ("500000.00", "320000.00")
    assert exposure["available_credit"] == "180000.00" and exposure["over_limit"] is False
    # Limite abaissée sous l'exposition (modification auditée) : dépassement signalé.
    assert (
        priced.owner.patch(f"/customers/{limited['id']}", json={"credit_limit": "300000"})
    ).status_code == 200
    exposure = _exposure(priced.owner, limited)
    assert exposure["available_credit"] == "0.00" and exposure["over_limit"] is True
    logs = priced.owner.get("/audit-logs", params={"action": "customer.updated"}).json()
    assert logs["items"][0]["data"]["credit_limit"] == {
        "before": "500000.00",
        "after": "300000.00",
    }
    missing = priced.owner.get(f"/customers/{uuid.uuid4()}/credit-exposure")
    assert missing.status_code == 404 and missing.json()["code"] == "customer_not_found"


def test_null_credit_limit_means_no_limit(priced: World) -> None:
    customer = _customer(priced)  # credit_limit NULL
    assert _validate(priced, _draft(priced, 1500000, customer)).status_code == 200
    assert _exposure(priced.owner, customer)["current_exposure"] == "1500000.00"


def test_zero_credit_limit_forbids_any_credit(priced: World, owner_db: Session) -> None:
    customer = _customer(priced, limit="0")
    sale = _draft(priced, 100000, customer)
    refused = _validate(priced, sale)
    assert refused.status_code == 422 and refused.json()["code"] == "credit_limit_exceeded"
    assert refused.json()["credit_limit"] == "0.00"
    assert refused.json()["sale_exposure"] == "100000.00"
    assert refused.json()["available_credit"] == "0.00"
    # Paiement partiel à la validation : exposition restante > 0 → toujours refusé.
    partial = _validate(priced, sale, ("90000", "CASH"))
    assert partial.status_code == 422 and partial.json()["code"] == "credit_limit_exceeded"
    # Tout refus annule tout : vente brouillon, stock, paiements, audit inchangés.
    assert priced.owner.get(f"/sales/{sale['id']}").json()["status"] == "DRAFT"
    assert sh.level(owner_db, priced, 0)[0] == "200.000"
    assert sh.count(owner_db, "SELECT count(*) FROM payments") == 0
    assert (
        sh.count(owner_db, "SELECT count(*) FROM audit_logs WHERE action = 'sale.validated'") == 0
    )
    # Payée intégralement à la validation (paiement mixte) : aucune exposition, acceptée.
    paid = _validate(priced, sale, ("60000", "CASH"), ("40000", "MOBILE_MONEY"))
    assert paid.status_code == 200, paid.text
    assert paid.json()["payment_status"] == "PAID" and paid.json()["remaining_amount"] == "0.00"
    assert sh.level(owner_db, priced, 0)[0] == "190.000"
    assert _exposure(priced.owner, customer)["current_exposure"] == "0.00"


def test_credit_limit_100000(priced: World) -> None:
    customer = _customer(priced, limit="100000")
    assert _validate(priced, _draft(priced, 70000, customer)).status_code == 200
    second = _validate(priced, _draft(priced, 40000, customer))
    assert second.status_code == 422 and second.json()["code"] == "credit_limit_exceeded"
    assert second.json()["current_exposure"] == "70000.00"
    assert second.json()["available_credit"] == "30000.00"
    # Exactement la limite : accepté (refus seulement au-delà).
    assert _validate(priced, _draft(priced, 30000, customer)).status_code == 200
    assert _exposure(priced.owner, customer)["available_credit"] == "0.00"
    # Vente sans client : jamais soumise à une limite client.
    assert _validate(priced, _draft(priced, 90000)).status_code == 200


def test_credit_limit_500000_examples(priced: World) -> None:
    customer = _customer(priced, limit="500000")
    _sale(priced, 300000, customer)
    # Limite 500 000, créances 300 000, vente 100 000 payée immédiatement : exposition 300 000.
    paid = _validate(priced, _draft(priced, 100000, customer), ("100000", "CASH"))
    assert paid.status_code == 200 and paid.json()["payment_status"] == "PAID"
    assert _exposure(priced.owner, customer)["current_exposure"] == "300000.00"
    _sale(priced, 150000, customer)  # exposition 450 000
    # Limite 500 000, créances 450 000, vente 100 000 sans paiement : 550 000 → refusée.
    refused = _validate(priced, _draft(priced, 100000, customer))
    assert refused.status_code == 422 and refused.json()["code"] == "credit_limit_exceeded"
    # Même vente avec 50 000 encaissés à la validation : 500 000 → acceptée.
    sale = _draft(priced, 100000, customer)
    accepted = _validate(priced, sale, ("50000", "CARD"))
    assert accepted.status_code == 200 and accepted.json()["remaining_amount"] == "50000.00"
    assert _exposure(priced.owner, customer)["current_exposure"] == "500000.00"
    # Un paiement réduit l'exposition et libère du crédit.
    _pay(priced, accepted.json(), "50000")
    assert _exposure(priced.owner, customer)["available_credit"] == "50000.00"


def test_immediate_payments_follow_payment_rules(priced: World, client: TestClient) -> None:
    sale = _draft(priced, 100000)
    over = _validate(priced, sale, ("60000", "CASH"), ("50000", "CARD"))
    assert over.status_code == 422 and over.json()["code"] == "payment_exceeds_balance"
    assert priced.owner.get(f"/sales/{sale['id']}").json()["status"] == "DRAFT"
    invalid = priced.owner.post(
        f"/sales/{sale['id']}/validate", json={"payments": [{"amount": "0", "method": "CASH"}]}
    )
    assert invalid.status_code == 422
    # Encaisser à la validation exige aussi sales.payment.create.
    validator = _custom_member(
        priced,
        client,
        "valideur@example.com",
        ["sales.sale.view", "sales.sale.create", "sales.sale.validate"],
    )
    denied = _validate(priced, sale, ("100000", "CASH"), api=validator)
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    assert _validate(priced, sale, api=validator).status_code == 200


# --- Concurrence ----------------------------------------------------------------------------------


def test_concurrent_credit_validations_same_customer(priced: World, app: Any) -> None:
    """Limite 100 000, exposition 0 ; ventes A et B de 70 000 validées simultanément : une
    seule crée l'exposition (verrou du client)."""
    customer = _customer(priced, limit="100000")
    a = _draft(priced, 70000, customer)
    b = _draft(priced, 70000, customer)
    statuses = _run_concurrently(
        app,
        priced.owner.token,
        [(f"/sales/{a['id']}/validate", None), (f"/sales/{b['id']}/validate", None)],
    )
    assert statuses == [200, 422]
    assert _exposure(priced.owner, customer)["current_exposure"] == "70000.00"
    assert len(_open(priced.owner)) == 1


def test_concurrent_credit_validations_on_different_sites(priced: World, app: Any) -> None:
    """L'exposition d'un client couvre tous les sites du tenant."""
    customer = _customer(priced, limit="100000")
    a = _draft(priced, 70000, customer)
    b = _draft(priced, 70000, customer, site_id=priced.site2)
    statuses = _run_concurrently(
        app,
        priced.owner.token,
        [(f"/sales/{a['id']}/validate", None), (f"/sales/{b['id']}/validate", None)],
    )
    assert statuses == [200, 422]
    assert _exposure(priced.owner, customer)["current_exposure"] == "70000.00"


def test_credit_validation_and_payment_concurrently(priced: World, app: Any) -> None:
    """Vente à crédit validée pendant un paiement du même client : jamais de dépassement ni
    d'interblocage ; la validation voit le paiement ou est refusée."""
    customer = _customer(priced, limit="100000")
    x = _sale(priced, 60000, customer)  # exposition 60 000
    y = _draft(priced, 60000, customer)
    statuses = _run_concurrently(
        app,
        priced.owner.token,
        [
            (f"/sales/{x['id']}/payments", {"amount": "60000", "method": "CASH"}),
            (f"/sales/{y['id']}/validate", None),
        ],
    )
    exposure = Decimal(_exposure(priced.owner, customer)["current_exposure"])
    assert exposure <= Decimal("100000")
    if statuses == [200, 201]:  # paiement d'abord : validation acceptée
        assert exposure == Decimal("60000")
    else:  # validation d'abord (exposition 120 000 refusée), puis paiement
        assert statuses == [201, 422] and exposure == Decimal("0")


# --- Sécurité : RBAC, sites, abonnement, module ---------------------------------------------------


def test_permissions_by_base_role(priced: World, client: TestClient) -> None:
    customer = _customer(priced)
    sale = _sale(priced, 50000, customer)
    for email, template in (
        ("admin@example.com", "administrator"),
        ("gestion@example.com", "manager"),
        ("vendeur@example.com", "seller"),
        ("consultant@example.com", "viewer"),
    ):
        api = sh.member(priced, client, email, template, all_sites=True)
        assert set(_open(api)) == {sale["number"]}
        assert api.get(f"/receivables/{sale['id']}").status_code == 200
        assert api.get("/receivables/summary").status_code == 200
        assert _exposure(api, customer)["current_exposure"] == "50000.00"


def test_custom_role_permissions(priced: World, client: TestClient) -> None:
    customer = _customer(priced)
    sale = _sale(priced, 50000, customer)
    collector = _custom_member(
        priced, client, "recouvrement@example.com", ["receivables.receivable.view"]
    )
    assert set(_open(collector)) == {sale["number"]}
    assert collector.get(f"/receivables/{sale['id']}").json()["payments"] == []
    reader = _custom_member(priced, client, "lecteur@example.com", ["sales.sale.view"])
    for path in (
        "/receivables",
        "/receivables/summary",
        f"/receivables/{sale['id']}",
        f"/customers/{customer['id']}/receivables",
        f"/customers/{customer['id']}/credit-exposure",
    ):
        denied = reader.get(path)
        assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"


def test_read_only_api(priced: World) -> None:
    sale = _sale(priced, 50000, _customer(priced))
    for method, path in (
        ("post", "/receivables"),
        ("put", f"/receivables/{sale['id']}"),
        ("delete", f"/receivables/{sale['id']}"),
        ("post", f"/receivables/{sale['id']}"),
    ):
        assert (
            getattr(priced.owner.client, method)(
                f"/api/v1{path}", headers=priced.owner._headers()
            ).status_code
            == 405
        )


def test_site_scope(priced: World, client: TestClient) -> None:
    customer = _customer(priced, limit="100000")
    shop_sale = _sale(priced, 30000, customer)
    depot_sale = _sale(priced, 50000, customer, site_id=priced.site2)
    shop = sh.member(priced, client, "boutique@example.com", "seller", site_ids=[priced.site])
    # Un membre limité à la boutique ne voit que les créances de la boutique.
    assert set(_open(shop)) == {shop_sale["number"]}
    assert _open(shop, site_id=priced.site2) == {}
    assert shop.get("/receivables/summary").json()["total_receivables"] == "30000.00"
    hidden = shop.get(f"/receivables/{depot_sale['id']}")
    assert hidden.status_code == 404 and hidden.json()["code"] == "receivable_not_found"
    listed = shop.get(f"/customers/{customer['id']}/receivables").json()
    assert [r["sale_number"] for r in listed["items"]] == [shop_sale["number"]]
    # Exposition non consolidée : ses sites seulement, aucun crédit disponible communiqué.
    exposure = _exposure(shop, customer)
    assert exposure["consolidated"] is False and exposure["current_exposure"] == "30000.00"
    assert exposure["available_credit"] is None and exposure["over_limit"] is None
    assert exposure["credit_limit"] == "100000.00"
    # Le contrôle de la limite, lui, porte sur tous les sites (80 000 + 30 000 > 100 000)…
    refused = _validate(priced, _draft(priced, 30000, customer, api=shop), api=shop)
    assert refused.status_code == 422 and refused.json()["code"] == "credit_limit_exceeded"
    # … sans révéler l'exposition consolidée à un membre limité à un site.
    assert "current_exposure" not in refused.json() and "available_credit" not in refused.json()
    # Site sélectionné (X-Site-Id) : vue limitée à ce site, même pour le propriétaire.
    priced.owner.site_id = uuid.UUID(priced.site2)
    assert set(_open(priced.owner)) == {depot_sale["number"]}
    assert _exposure(priced.owner, customer)["consolidated"] is False
    mismatch = priced.owner.get(f"/receivables/{shop_sale['id']}")
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "site_mismatch"


def test_expired_subscription_keeps_consultation(priced: World, owner_db: Session) -> None:
    customer = _customer(priced)
    sale = _sale(priced, 50000, customer)
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert set(_open(priced.owner)) == {sale["number"]}
    assert priced.owner.get("/receivables/summary").status_code == 200
    assert priced.owner.get(f"/receivables/{sale['id']}").status_code == 200
    assert _exposure(priced.owner, customer)["current_exposure"] == "50000.00"


def test_module_deactivation(priced: World, owner_db: Session) -> None:
    customer = _customer(priced, limit="10000")
    owner_db.execute(
        text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'receivables'")
    )
    owner_db.commit()
    for path in ("/receivables", f"/customers/{customer['id']}/credit-exposure"):
        denied = priced.owner.get(path)
        assert denied.status_code == 403 and denied.json()["code"] == "module_unavailable"
    # La limite de crédit est une règle de vente : appliquée même sans le module Créances.
    refused = _validate(priced, _draft(priced, 50000, customer))
    assert refused.status_code == 422 and refused.json()["code"] == "credit_limit_exceeded"
    # Le module Clients n'est pas affecté.
    assert priced.owner.get(f"/customers/{customer['id']}").status_code == 200


def test_consultation_writes_no_audit(priced: World) -> None:
    customer = _customer(priced)
    sale = _sale(priced, 50000, customer)
    before = priced.owner.get("/audit-logs", params={"limit": 1}).json()["total"]
    _receivables(priced.owner)
    priced.owner.get("/receivables/summary")
    priced.owner.get(f"/receivables/{sale['id']}")
    _exposure(priced.owner, customer)
    assert priced.owner.get("/audit-logs", params={"limit": 1}).json()["total"] == before


# --- Multi-tenant ---------------------------------------------------------------------------------


def test_isolation_between_tenants_api(priced: World, provision: Any, api_for: Any) -> None:
    customer = _customer(priced)
    sale = _sale(priced, 50000, customer)
    provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    beta: Api = api_for("owner@beta.example.com")
    assert _receivables(beta)["total"] == 0
    assert beta.get("/receivables/summary").json()["total_receivables"] == "0.00"
    assert beta.get(f"/receivables/{sale['id']}").status_code == 404
    for path in (
        f"/customers/{customer['id']}/receivables",
        f"/customers/{customer['id']}/credit-exposure",
    ):
        response = beta.get(path)
        assert response.status_code == 404 and response.json()["code"] == "customer_not_found"
    # Filtre sur un client d'un autre tenant : rien (tenant_id jamais pris du client).
    assert _receivables(beta, customer_id=customer["id"])["total"] == 0


def test_isolation_with_app_role_and_rls(
    priced: World, provision: Any, app_engine: Engine, owner_db: Session
) -> None:
    customer = _customer(priced)
    _sale(priced, 50000, customer)
    b = provision("beta")
    tenant_a = owner_db.execute(text("SELECT tenant_id FROM sales")).scalar_one()
    customer_id = uuid.UUID(customer["id"])
    # Rôle applicatif réel (sans BYPASSRLS) : sans contexte ou dans un autre tenant, les
    # requêtes de créances ne voient rien.
    with create_session_factory(app_engine)() as db:
        assert db.execute(open_receivables_query()).all() == []
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.execute(open_receivables_query()).all() == []
        assert customer_exposure(db, customer_id) == (Decimal("0.00"), 0)
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        assert len(db.execute(open_receivables_query()).all()) == 1
        assert customer_exposure(db, customer_id) == (Decimal("50000.00"), 1)
        bypass = db.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).scalar_one()
        assert bypass is False
