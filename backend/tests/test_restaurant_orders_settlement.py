"""Palier R2-D (ADR-0049 D4, D5, D6, D13, D14) — règlement T2, origine des ventes, procédure Z3,
commande close.

- T2 : vente ``RESTAURANT`` aux prix FIGÉS de la commande, validée par le moteur commun (stock
  FEFO, lots, CMUP, crédit, caisse), paiements immédiats ; tout ou rien ; idempotent ; une seule
  vente active par commande (verrou de la commande + index ``uq_sales_active_origin``).
- Z3 : annulation des paiements puis de la vente (port d'origine : commande verrouillée AVANT la
  vente) ; commande « à régler » ; nouvelle vente aux prix figés. Commande close : définitive.
- POS ordinaire et ventes sans origine inchangés.

Registre de test (``restaurant.orders`` disponible, N1) ; appels directs à l'API.
"""

import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.modules.sales import origin_port
from tests.conftest import Api
from tests.test_restaurant_orders import MAQUIS, ORDERS, _api, _code, _line, _menu, _ok, _order
from tests.test_restaurant_orders_claims import _join, _roles

ORIGIN = "restaurant_order"


# --- Aides ----------------------------------------------------------------------------------------


def _receive(
    r: SimpleNamespace,
    article: str,
    quantity: str,
    cost: str = "400",
    lot: str | None = None,
    expiry: str | None = None,
) -> None:
    line: dict[str, Any] = {"article_id": article, "quantity": quantity, "unit_cost": cost}
    if lot is not None:
        line["lot_number"] = lot
    if expiry is not None:
        line["lot_expiry_date"] = expiry
    entry = _ok(
        r.owner.post(
            "/stock/entries",
            json={"site_id": r.site, "supplier_id": r.supplier, "lines": [line]},
        ),
        201,
    )
    _ok(r.owner.post(f"/stock/entries/{entry['id']}/validate"))


@pytest.fixture
def resto(provision: Any, oclient: TestClient) -> SimpleNamespace:
    """Maquis : bière 700 (casier de 12 à 8 000), 100 bouteilles en stock au coût 400."""
    t = provision("ro-pay", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-pay.example.com")
    site = str(t.site_id)
    supplier = _ok(owner.post("/suppliers", json={"name": "Brasserie"}), 201)["id"]
    r = SimpleNamespace(
        tenant=t.tenant_id,
        site=site,
        owner=owner,
        client=oclient,
        menu=_menu(owner, site, "P"),
        supplier=supplier,
    )
    _receive(r, r.menu.article, "100")
    return r


def _lot_menu(r: SimpleNamespace) -> SimpleNamespace:
    """Article suivi par lot (péremption), au menu du site, sans stock."""
    category = _ok(r.owner.post("/catalog/categories", json={"name": "Frais"}), 201)
    article = _ok(
        r.owner.post(
            "/catalog/articles",
            json={
                "reference": "YAOURT",
                "designation": "Yaourt",
                "category_id": category["id"],
                "unit": "pot",
                "purchase_price": "300",
                "sale_price": "700",
                "site_ids": [r.site],
            },
        ),
        201,
    )
    section = _ok(
        r.owner.post("/restaurant/menu/sections", json={"site_id": r.site, "name": "Desserts"}),
        201,
    )
    item = _ok(
        r.owner.post(
            "/restaurant/menu/items",
            json={"site_id": r.site, "section_id": section["id"], "article_id": article["id"]},
        ),
        201,
    )
    return SimpleNamespace(article=article["id"], unit=item["id"])


def _settle(api: Api, order_id: str, key: str | None = None, **body: Any) -> Any:
    return api.post(
        f"/restaurant/orders/{order_id}/settle",
        json={"idempotency_key": key or str(uuid.uuid4()), **body},
    )


def _cash(amount: str) -> list[dict[str, str]]:
    return [{"amount": amount, "method": "CASH"}]


def _level(owner_db: Session, r: SimpleNamespace, article: str | None = None) -> tuple[str, str]:
    owner_db.expire_all()
    row = owner_db.execute(
        text(
            "SELECT quantity::text, average_cost::text FROM stock_levels "
            "WHERE site_id = :s AND article_id = :a"
        ),
        {"s": r.site, "a": article or r.menu.article},
    ).one()
    return row[0], row[1]


def _counts(owner_db: Session) -> dict[str, int]:
    owner_db.expire_all()
    return {
        table: int(owner_db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
        for table in ("sales", "sale_lines", "payments", "cash_movements", "stock_movements")
    }


def _events(api: Api, order_id: str) -> list[str]:
    return [e["event_type"] for e in _ok(api.get(f"/restaurant/orders/{order_id}/events"))]


def _customer(r: SimpleNamespace, limit: str | None = None) -> str:
    body: dict[str, Any] = {"customer_type": "INDIVIDUAL", "name": f"Client {uuid.uuid4().hex[:6]}"}
    if limit is not None:
        body["credit_limit"] = limit
    return str(_ok(r.owner.post("/customers", json=body), 201)["id"])


def _open_cash(r: SimpleNamespace) -> dict[str, Any]:
    _ok(r.owner.put(f"/cash/sites/{r.site}", json={"enabled": True}))
    register = _ok(
        r.owner.post("/cash/registers", json={"site_id": r.site, "name": "Comptoir"}), 201
    )
    return dict(
        _ok(
            r.owner.post(
                "/cash/sessions",
                json={"cash_register_id": register["id"], "opening_float": "0"},
            ),
            201,
        )
    )


def _serve_all(api: Api, order_id: str) -> dict[str, Any]:
    base = f"/restaurant/orders/{order_id}"
    for action in ("start", "ready"):
        api.post(f"{base}/{action}")
    return dict(_ok(api.post(f"{base}/serve")))


def _race(calls: list[Any]) -> list[Any]:
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def run(index: int) -> None:
        barrier.wait()
        results[index] = calls[index]()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


# --- T2 : vente aux prix figés ------------------------------------------------------------------


def test_settlement_sells_at_frozen_prices_through_the_common_engine(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    order = _ok(
        _order(r.owner, r.site, [_line(r.menu.unit, "2"), _line(r.menu.crate_item, "1")]), 201
    )
    assert order["total"] == "9400.00"
    # Prix du catalogue modifiés APRÈS la commande : la vente reprend les prix figés.
    _ok(r.owner.patch(f"/catalog/articles/{r.menu.article}", json={"sale_price": "900"}))
    _ok(
        r.owner.patch(
            f"/catalog/packagings/{r.menu.crate}",
            json={"sale_price": "9000"},
        )
    )
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("9400")), 201)
    assert settled["replayed"] is False
    detail = settled["order"]
    assert detail["settlement_status"] == "SETTLED" and detail["status"] == "OPEN"
    assert detail["payment_status"] == "PAID" and detail["amount_due"] == "0.00"
    sale = _ok(r.owner.get(f"/sales/{settled['sale_id']}"))
    assert detail["sale_number"] == sale["number"] and sale["number"].startswith("VENT-")
    assert (sale["channel"], sale["status"], sale["total"]) == (
        "RESTAURANT",
        "VALIDATED",
        "9400.00",
    )
    assert (sale["origin_type"], sale["origin_id"]) == (ORIGIN, order["id"])
    assert sorted((li["quantity"], li["unit_price"]) for li in sale["lines"]) == [
        ("1.000", "8000.00"),
        ("2.000", "700.00"),
    ]
    # Stock : 2 bouteilles + 1 casier de 12 = 14 en unité de base, CMUP inchangé.
    assert _level(owner_db, r) == ("86.000", "400.0000")
    assert _events(r.owner, order["id"])[-1] == "SETTLED"
    audits = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_order.settled"}))
    assert audits["items"][0]["data"]["sale_number"] == sale["number"]
    # Service de toutes les lignes : clôture définitive.
    closed = _serve_all(r.owner, order["id"])
    assert closed["status"] == "CLOSED" and closed["closed_at"] is not None
    assert _events(r.owner, order["id"])[-1] == "CLOSED"


def test_ordinary_sales_and_pos_are_unchanged(resto: SimpleNamespace) -> None:
    r = resto
    # Vente ordinaire : le contrôle ``sale_prices_changed`` s'applique toujours ; une origine
    # envoyée par le client est ignorée.
    draft = _ok(
        r.owner.post(
            "/sales",
            json={
                "site_id": r.site,
                "lines": [{"article_id": r.menu.article, "quantity": "1"}],
                "origin_type": ORIGIN,
                "origin_id": str(uuid.uuid4()),
            },
        ),
        201,
    )
    assert (draft["origin_type"], draft["origin_id"], draft["channel"]) == (
        None,
        None,
        "BACKOFFICE",
    )
    _ok(r.owner.patch(f"/catalog/articles/{r.menu.article}", json={"sale_price": "750"}))
    changed = r.owner.post(f"/sales/{draft['id']}/validate", json={})
    assert _code(changed) == (409, "sale_prices_changed")
    pos = _ok(
        r.owner.post(
            "/pos/checkout",
            json={
                "site_id": r.site,
                "lines": [{"article_id": r.menu.article, "quantity": "1"}],
                "payments": _cash("750"),
                "idempotency_key": str(uuid.uuid4()),
                "origin_type": ORIGIN,
            },
        ),
        201,
    )
    assert (pos["sale"]["channel"], pos["sale"]["origin_type"]) == ("POS", None)
    # Le canal Restauration n'est jamais saisi directement.
    assert r.owner.get("/sales", params={"channel": "RESTAURANT"}).json()["total"] == 0


def test_same_presentation_at_two_frozen_prices_keeps_the_order_total(
    resto: SimpleNamespace,
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "2")]), 201)
    _ok(r.owner.patch(f"/catalog/articles/{r.menu.article}", json={"sale_price": "800"}))
    added = _ok(
        r.owner.post(
            f"/restaurant/orders/{order['id']}/lines", json={"lines": [_line(r.menu.unit)]}
        )
    )
    assert added["total"] == "2200.00"
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("2200")), 201)
    sale = _ok(r.owner.get(f"/sales/{settled['sale_id']}"))
    # Une ligne par présentation (règle des ventes) : quantités et totaux figés additionnés.
    assert [(li["quantity"], li["line_total"]) for li in sale["lines"]] == [("3.000", "2200.00")]
    assert sale["total"] == "2200.00"
    # Prix unitaire MOYEN (733,33) signalé : la vente et le reçu ne laissent pas croire que
    # 3 × 733,33 = 2 200 ; le total de ligne fait foi.
    line = sale["lines"][0]
    assert (line["unit_price"], line["average_unit_price"]) == ("733.33", True)
    receipt = _ok(r.owner.get(f"/sales/{settled['sale_id']}/receipt"))
    assert [
        (li["unit_price"], li["line_total"], li["average_unit_price"]) for li in receipt["lines"]
    ] == [("733.33", "2200.00", True)]
    # Prix unique : aucun signalement (ventes ordinaires et POS inchangés).
    single = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "2")]), 201)
    plain = _ok(_settle(r.owner, single["id"], payments=_cash("1600")), 201)
    assert _ok(r.owner.get(f"/sales/{plain['sale_id']}"))["lines"][0]["average_unit_price"] is False


def test_lists_by_settlement_and_eligible_assignees(resto: SimpleNamespace) -> None:
    r = resto
    roles = _roles(r)
    manager = _join(r, "gerant@ro-pay.example.com", [roles["manager"]])
    seller = _join(r, "vendeur@ro-pay.example.com", [roles["seller"]])
    _join(r, "lecteur@ro-pay.example.com", [roles["viewer"]])
    paid = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    unpaid = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(_settle(r.owner, paid["id"], payments=_cash("700")), 201)
    listed = _ok(r.owner.get("/restaurant/orders", params={"settlement_status": "UNSETTLED"}))
    assert [o["id"] for o in listed["items"]] == [unpaid["id"]]
    # Membres éligibles (``order.claim`` effective sur le site) : calcul du serveur.
    eligible = _ok(manager.api.get(f"/restaurant/orders/{unpaid['id']}/assignees"))
    names = {a["full_name"] for a in eligible}
    assert {"gerant", "vendeur"} <= names and "lecteur" not in names
    assert _code(seller.api.get(f"/restaurant/orders/{unpaid['id']}/assignees")) == (
        403,
        "permission_denied",
    )


def test_payment_at_order_requires_settlement_before_preparation(resto: SimpleNamespace) -> None:
    r = resto
    _ok(
        r.owner.put(
            f"/restaurant/settings/{r.site}",
            json={"payment_timing": "AT_ORDER", "claim_protection_minutes": 5},
        )
    )
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    assert _code(r.owner.post(f"{base}/start")) == (409, "order_not_settled")
    _ok(_settle(r.owner, order["id"], payments=_cash("700")), 201)
    assert _ok(r.owner.post(f"{base}/start"))["prep_status"] == "IN_PREPARATION"


# --- Échecs : tout ou rien ----------------------------------------------------------------------


def test_failed_settlements_leave_no_sale_payment_or_movement(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    plain = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "2")]), 201)
    big = _ok(_order(r.owner, r.site, [_line(r.menu.crate_item, "9")]), 201)  # 108 > 100
    limited = _customer(r, limit="1000")
    on_credit = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "2")], customer_id=limited), 201)
    before = _counts(owner_db)
    key = str(uuid.uuid4())
    failures = [
        # Stock insuffisant : articles nommés.
        (_settle(r.owner, big["id"], payments=_cash("72000")), (422, "insufficient_stock")),
        # Reste dû sans client : crédit impossible (la commande reste ouverte, Z1).
        (_settle(r.owner, plain["id"], key), (422, "credit_customer_required")),
        # Paiement supérieur au total.
        (_settle(r.owner, plain["id"], payments=_cash("1500.50"), key=key), None),
        # Limite de crédit dépassée (2 × 700 > 1 000).
        (_settle(r.owner, on_credit["id"]), (422, "credit_limit_exceeded")),
    ]
    for response, expected in failures:
        assert response.status_code >= 400, response.text
        if expected is not None:
            assert _code(response) == expected
    # Caisse activée sans session de l'encaisseur : espèces refusées, tout est annulé.
    _ok(r.owner.put(f"/cash/sites/{r.site}", json={"enabled": True}))
    assert _code(_settle(r.owner, plain["id"], payments=_cash("1400"))) == (
        422,
        "cash_session_required",
    )
    # Article désactivé depuis la commande.
    _ok(r.owner.post(f"/catalog/articles/{r.menu.article}/deactivate"))
    assert _code(_settle(r.owner, plain["id"], payments=_cash("1400")))[1] == "article_inactive"
    _ok(r.owner.post(f"/catalog/articles/{r.menu.article}/activate"))
    # Rien n'a été écrit : ni vente (même brouillon), ni ligne, ni paiement, ni mouvement.
    assert _counts(owner_db) == before
    assert _level(owner_db, r) == ("100.000", "400.0000")
    for order in (plain, big, on_credit):
        state = _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))
        assert (state["settlement_status"], state["sale_id"], state["status"]) == (
            "UNSETTLED",
            None,
            "OPEN",
        )
        assert "SETTLED" not in _events(r.owner, order["id"])
    # Après correction, nouvelle tentative avec la MÊME clé : rien n'avait été enregistré.
    _open_cash(r)
    retried = _ok(_settle(r.owner, plain["id"], key, payments=_cash("1400")), 201)
    assert retried["replayed"] is False and retried["order"]["payment_status"] == "PAID"


def test_settlement_permissions_are_those_of_sales(resto: SimpleNamespace) -> None:
    r = resto
    roles = _roles(r)
    waiter_role = _ok(r.owner.post("/roles/from-template", json={"template_code": "waiter"}), 201)
    waiter = _join(r, "serveur@ro-pay.example.com", [waiter_role["id"]])
    seller = _join(r, "vendeur@ro-pay.example.com", [roles["seller"]])
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)], customer_id=_customer(r)), 201)
    # Le Serveur n'encaisse jamais (aucune permission de vente).
    refused = _settle(waiter.api, order["id"], payments=_cash("700"))
    assert _code(refused) == (403, "permission_denied")
    assert refused.json()["permission"] == "sales.sale.create"
    # Le Vendeur vend comptant, jamais à crédit.
    assert _code(_settle(seller.api, order["id"])) == (403, "credit_not_allowed")
    paid = _ok(_settle(seller.api, order["id"], payments=_cash("700")), 201)
    assert paid["order"]["payment_status"] == "PAID"


# --- Idempotence et concurrence -----------------------------------------------------------------


def test_settlement_is_idempotent_and_never_settled_twice(resto: SimpleNamespace) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    other = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    key = str(uuid.uuid4())
    first = _ok(_settle(r.owner, order["id"], key, payments=_cash("700")), 201)
    replay = _settle(r.owner, order["id"], key, payments=_cash("700"))
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["sale_id"] == first["sale_id"]
    assert _code(_settle(r.owner, order["id"], payments=_cash("700"))) == (409, "order_settled")
    assert _code(_settle(r.owner, other["id"], key, payments=_cash("700"))) == (
        409,
        "idempotency_key_reused",
    )
    assert r.owner.get("/sales", params={"channel": "RESTAURANT"}).json()["total"] == 1


def test_concurrent_settlements_produce_one_active_sale(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "3")]), 201)
    results = _race([lambda: _settle(r.owner, order["id"], payments=_cash("2100"))] * 6)
    assert sorted(res.status_code for res in results) == [201, 409, 409, 409, 409, 409]
    assert {_code(res) for res in results if res.status_code == 409} == {(409, "order_settled")}
    # Même clé envoyée simultanément : une vente, les autres réponses rejouées.
    again = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    key = str(uuid.uuid4())
    same = _race([lambda: _settle(r.owner, again["id"], key, payments=_cash("700"))] * 4)
    assert sorted(res.status_code for res in same) == [200, 200, 200, 201]
    assert len({res.json()["sale_id"] for res in same}) == 1
    owner_db.expire_all()
    active = owner_db.execute(
        text(
            "SELECT origin_id::text, count(*) FROM sales WHERE origin_type = :t "
            "AND status <> 'CANCELLED' GROUP BY origin_id"
        ),
        {"t": ORIGIN},
    ).all()
    assert sorted(count for _, count in active) == [1, 1]
    # Stock sorti une seule fois : 3 + 1 bouteilles.
    assert _level(owner_db, r)[0] == "96.000"


def test_database_guards_origin_and_single_active_sale(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("700")), 201)
    sale_id = settled["sale_id"]
    # Origine immuable, même par une écriture SQL directe.
    for sql in (
        "UPDATE sales SET origin_id = gen_random_uuid() WHERE id = :i",
        "UPDATE sales SET origin_type = NULL, origin_id = NULL WHERE id = :i",
    ):
        with pytest.raises(DBAPIError, match="sales_origin_immutable"):
            owner_db.execute(text(sql), {"i": sale_id})
        owner_db.rollback()
    ordinary = _ok(
        r.owner.post(
            "/sales",
            json={"site_id": r.site, "lines": [{"article_id": r.menu.article, "quantity": "1"}]},
        ),
        201,
    )
    with pytest.raises(DBAPIError, match="sales_origin_immutable"):
        owner_db.execute(
            text("UPDATE sales SET origin_type = :t, origin_id = :o WHERE id = :i"),
            {"t": ORIGIN, "o": order["id"], "i": ordinary["id"]},
        )
    owner_db.rollback()
    # Deuxième vente ACTIVE pour la même commande : refusée par l'index partiel.
    with pytest.raises(DBAPIError, match="uq_sales_active_origin"):
        owner_db.execute(
            text(
                "INSERT INTO sales (id, tenant_id, site_id, status, sale_date, subtotal, total, "
                "channel, is_credit, origin_type, origin_id, created_at, updated_at) "
                "SELECT gen_random_uuid(), tenant_id, site_id, 'DRAFT', sale_date, 0, 0, "
                "'RESTAURANT', false, origin_type, origin_id, now(), now() "
                "FROM sales WHERE id = :i"
            ),
            {"i": sale_id},
        )
    owner_db.rollback()
    # Canal Restauration sans origine : refusé.
    with pytest.raises(DBAPIError, match="restaurant_has_origin"):
        owner_db.execute(
            text("UPDATE sales SET channel = 'RESTAURANT' WHERE id = :i"), {"i": ordinary["id"]}
        )
    owner_db.rollback()


# --- Procédure Z3 -------------------------------------------------------------------------------


def test_z3_cancellation_then_new_settlement_at_frozen_prices(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    session = _open_cash(r)
    roles = _roles(r)
    seller = _join(r, "vendeur@ro-pay.example.com", [roles["seller"]])
    order = _ok(
        _order(r.owner, r.site, [_line(r.menu.unit, "2"), _line(r.menu.crate_item, "1")]), 201
    )
    base = f"/restaurant/orders/{order['id']}"
    bottle_line = order["lines"][0]["id"]
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("9400")), 201)
    sale_id = settled["sale_id"]
    sale_number = settled["order"]["sale_number"]
    assert _level(owner_db, r) == ("86.000", "400.0000")
    # Réglée : ni annulation de ligne, ni annulation de commande.
    cancel_bottle = {"line_ids": [bottle_line], "reason": "Erreur"}
    blocked = r.owner.post(f"{base}/cancel-lines", json=cancel_bottle)
    assert _code(blocked) == (409, "order_settled")
    # Vente encaissée : annuler d'abord les paiements ; le refus n'a rien changé à la commande.
    refused = r.owner.post(f"/sales/{sale_id}/cancel", json={"reason": "Erreur de commande"})
    assert _code(refused) == (409, "sale_has_payments")
    assert _ok(r.owner.get(base))["settlement_status"] == "SETTLED"
    # Permissions des ventes : le Vendeur n'annule ni paiement ni vente.
    payments = _ok(r.owner.get(f"/sales/{sale_id}/payments"))
    payment_id = payments[0]["id"] if isinstance(payments, list) else payments["items"][0]["id"]
    assert seller.api.post(f"/sales/{sale_id}/cancel", json={"reason": "Erreur"}).status_code in (
        403,
        404,
    )
    _ok(
        r.owner.post(
            f"/sales/{sale_id}/payments/{payment_id}/cancel", json={"reason": "Erreur de saisie"}
        )
    )
    journal = _ok(r.owner.get(f"/cash/sessions/{session['id']}/movements"))["items"]
    assert sorted(m["movement_type"] for m in journal) == ["SALE_CASH_IN", "SALE_CASH_REVERSAL"]
    cancelled = _ok(r.owner.post(f"/sales/{sale_id}/cancel", json={"reason": "Erreur de commande"}))
    assert cancelled["status"] == "CANCELLED"
    # Stock restauré au coût de la sortie, CMUP inchangé ; commande « à régler ».
    assert _level(owner_db, r) == ("100.000", "400.0000")
    state = _ok(r.owner.get(base))
    assert (state["settlement_status"], state["sale_id"], state["status"]) == (
        "UNSETTLED",
        None,
        "OPEN",
    )
    event = _ok(r.owner.get(f"{base}/events"))[-1]
    assert event["event_type"] == "SALE_CANCELLED" and event["data"]["sale_number"] == sale_number
    # Annulation de la ligne voulue, puis nouveau règlement aux prix FIGÉS (prix modifié).
    _ok(r.owner.post(f"{base}/cancel-lines", json=cancel_bottle))
    _ok(r.owner.patch(f"/catalog/articles/{r.menu.article}", json={"sale_price": "5"}))
    second = _ok(_settle(r.owner, order["id"], payments=_cash("8000")), 201)
    assert second["sale_id"] != sale_id
    new_sale = _ok(r.owner.get(f"/sales/{second['sale_id']}"))
    assert new_sale["total"] == "8000.00" and new_sale["number"] != sale_number
    assert _level(owner_db, r) == ("88.000", "400.0000")
    # Ancienne vente : déjà annulée, la commande n'est pas touchée.
    again = r.owner.post(f"/sales/{sale_id}/cancel", json={"reason": "Encore une fois"})
    assert _code(again) == (409, "sale_already_cancelled")
    assert _ok(r.owner.get(base))["sale_id"] == second["sale_id"]
    audit = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_order.sale_cancelled"}))
    assert audit["items"][0]["data"]["sale_number"] == sale_number


def test_sale_without_origin_handler_cannot_be_cancelled(
    resto: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = resto
    customer = _customer(r)
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)], customer_id=customer), 201)
    settled = _ok(_settle(r.owner, order["id"]), 201)  # à crédit, aucun paiement
    monkeypatch.setattr(origin_port, "_hooks", {})
    refused = r.owner.post(f"/sales/{settled['sale_id']}/cancel", json={"reason": "Sans port"})
    assert _code(refused) == (409, "sale_origin_unavailable")


# --- Commande close -----------------------------------------------------------------------------


def test_closed_order_stays_closed(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "2")]), 201)
    base = f"/restaurant/orders/{order['id']}"
    _serve_all(r.owner, order["id"])  # servie AVANT le règlement (« à la fin »)
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("1400")), 201)
    assert settled["order"]["status"] == "CLOSED"
    sale_id = settled["sale_id"]
    # La vente d'une commande close ne s'annule pas.
    payment = _ok(r.owner.get(f"/sales/{sale_id}/payments"))
    payment_id = payment[0]["id"] if isinstance(payment, list) else payment["items"][0]["id"]
    _ok(r.owner.post(f"/sales/{sale_id}/payments/{payment_id}/cancel", json={"reason": "Erreur"}))
    assert _code(r.owner.post(f"/sales/{sale_id}/cancel", json={"reason": "Annuler"})) == (
        409,
        "order_closed",
    )
    # Paiement annulé : vente toujours validée, reste dû visible, commande close ; un nouveau
    # paiement s'enregistre sur la même vente (sans surpaiement).
    closed = _ok(r.owner.get(base))
    assert (closed["status"], closed["payment_status"], closed["amount_due"]) == (
        "CLOSED",
        "UNPAID",
        "1400.00",
    )
    _ok(r.owner.post(f"/sales/{sale_id}/payments", json={"amount": "1400", "method": "CASH"}), 201)
    assert _ok(r.owner.get(base))["payment_status"] == "PAID"
    # Aucune voie de réouverture.
    for path, body in (
        (f"{base}/lines", {"lines": [_line(r.menu.unit)]}),
        (f"{base}/cancel-lines", {"line_ids": [order["lines"][0]["id"]], "reason": "Erreur"}),
        (f"{base}/cancel", {"reason": "Erreur"}),
        (f"{base}/claim", None),
    ):
        assert _code(r.owner.post(path, json=body)) == (409, "order_closed")
    with pytest.raises(DBAPIError, match="restaurant_final_state"):
        owner_db.execute(
            text("UPDATE restaurant_orders SET settlement_status = 'UNSETTLED' WHERE id = :i"),
            {"i": order["id"]},
        )
    owner_db.rollback()


def test_race_between_last_service_and_sale_cancellation(resto: SimpleNamespace) -> None:
    """Annulation de la vente concurrente du dernier « servi » : soit la commande se clôt et
    l'annulation est refusée (``order_closed``), soit la vente est annulée et la commande reste
    ouverte « à régler » — jamais une commande close sans vente active, aucun interblocage."""
    r = resto
    outcomes = set()
    for _ in range(4):
        customer = _customer(r)
        order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)], customer_id=customer), 201)
        base = f"/restaurant/orders/{order['id']}"
        settled = _ok(_settle(r.owner, order["id"]), 201)  # à crédit : annulable sans paiement
        for action in ("start", "ready"):
            _ok(r.owner.post(f"{base}/{action}"))
        serve, cancel = _race(
            [
                lambda b=base: r.owner.post(f"{b}/serve"),
                lambda s=settled["sale_id"]: r.owner.post(
                    f"/sales/{s}/cancel", json={"reason": "Course concurrente"}
                ),
            ]
        )
        assert serve.status_code == 200, serve.text
        state = _ok(r.owner.get(base))
        sale = _ok(r.owner.get(f"/sales/{settled['sale_id']}"))
        if cancel.status_code == 200:
            assert sale["status"] == "CANCELLED"
            assert (state["status"], state["settlement_status"], state["sale_id"]) == (
                "OPEN",
                "UNSETTLED",
                None,
            )
            outcomes.add("cancelled")
        else:
            assert _code(cancel) == (409, "order_closed")
            assert sale["status"] == "VALIDATED" and state["status"] == "CLOSED"
            outcomes.add("closed")
    assert outcomes  # au moins un scénario observé, chacun cohérent


def test_race_between_settlement_and_order_cancellation(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    for _ in range(3):
        order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
        settle, cancel = _race(
            [
                lambda o=order: _settle(r.owner, o["id"], payments=_cash("700")),
                lambda o=order: r.owner.post(
                    f"/restaurant/orders/{o['id']}/cancel", json={"reason": "Client parti"}
                ),
            ]
        )
        state = _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))
        if settle.status_code == 201:
            assert _code(cancel) == (409, "order_settled")
            assert state["settlement_status"] == "SETTLED" and state["status"] == "OPEN"
        else:
            assert cancel.status_code == 200 and _code(settle) == (409, "order_cancelled")
            assert state["status"] == "CANCELLED" and state["sale_id"] is None
    owner_db.expire_all()
    orphan = owner_db.execute(
        text(
            "SELECT count(*) FROM sales s JOIN restaurant_orders o ON o.id = s.origin_id "
            "WHERE s.status <> 'CANCELLED' AND o.status = 'CANCELLED'"
        )
    ).scalar_one()
    assert orphan == 0


# --- Crédit, lots, état financier ---------------------------------------------------------------


def test_served_unpaid_order_settled_on_credit_closes(resto: SimpleNamespace) -> None:
    r = resto
    customer = _customer(r)
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "3")], customer_id=customer), 201)
    _serve_all(r.owner, order["id"])
    settled = _ok(_settle(r.owner, order["id"]), 201)
    assert settled["order"]["status"] == "CLOSED"
    assert (settled["order"]["payment_status"], settled["order"]["amount_due"]) == (
        "UNPAID",
        "2100.00",
    )
    sale = _ok(r.owner.get(f"/sales/{settled['sale_id']}"))
    assert sale["is_credit"] is True and sale["customer_id"] == customer
    # Sans client à créditer (Z1) : la commande servie reste ouverte, identifiable.
    anonymous = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _serve_all(r.owner, anonymous["id"])
    assert _code(_settle(r.owner, anonymous["id"]))[1] == "credit_customer_required"
    pending = _ok(r.owner.get("/restaurant/orders", params={"unsettled_served": "true"}))
    assert [o["id"] for o in pending["items"]] == [anonymous["id"]]


def test_lots_are_consumed_fefo_and_expired_lots_need_an_override(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    menu = _lot_menu(r)
    _ok(
        r.owner.patch(
            f"/catalog/articles/{menu.article}", json={"lot_tracked": True, "expiry_tracked": True}
        )
    )
    _receive(r, menu.article, "5", lot="L-LATE", expiry="2099-12-31")
    _receive(r, menu.article, "3", lot="L-SOON", expiry="2098-01-31")
    _receive(r, menu.article, "4", lot="L-OLD", expiry="2020-01-31")  # périmé
    order = _ok(_order(r.owner, r.site, [_line(menu.unit, "4")]), 201)
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("2800")), 201)
    owner_db.expire_all()
    moves = owner_db.execute(
        text(
            "SELECT l.number, m.quantity::text FROM stock_movements m JOIN stock_lots l "
            "ON l.id = m.lot_id WHERE m.source_id = :s AND m.movement_type = 'SALE' "
            "ORDER BY l.number"
        ),
        {"s": settled["sale_id"]},
    ).all()
    # FEFO : le lot périmé n'est jamais choisi automatiquement.
    assert [tuple(m) for m in moves] == [("L-LATE", "-1.000"), ("L-SOON", "-3.000")]
    # Plus que le stock non périmé (4 restants) : refus, puis dérogation explicite.
    big = _ok(_order(r.owner, r.site, [_line(menu.unit, "8")]), 201)
    assert _code(_settle(r.owner, big["id"], payments=_cash("5600")))[1] == (
        "insufficient_unexpired_stock"
    )
    old_lot = [
        lot
        for lot in _ok(r.owner.get("/stock/lots", params={"limit": 100}))["items"]
        if lot["number"] == "L-OLD"
    ][0]
    override = {
        "reason": "Déstockage autorisé",
        "lots": [{"article_id": menu.article, "lot_id": old_lot["id"], "quantity": "4"}],
    }
    done = _ok(
        _settle(r.owner, big["id"], payments=_cash("5600"), expired_lot_override=override), 201
    )
    sale = _ok(r.owner.get(f"/sales/{done['sale_id']}"))
    assert sale["expired_lot_override_reason"] == "Déstockage autorisé"
    audits = _ok(r.owner.get("/audit-logs", params={"action": "sale.expired_lot_overridden"}))
    assert audits["total"] == 1


def test_financial_state_is_visible_without_payment_details(resto: SimpleNamespace) -> None:
    r = resto
    preparer_role = _ok(
        r.owner.post("/roles/from-template", json={"template_code": "preparer"}), 201
    )
    preparer = _join(r, "cuisine@ro-pay.example.com", [preparer_role["id"]])
    customer = _customer(r)
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "2")], customer_id=customer), 201)
    settled = _ok(
        _settle(r.owner, order["id"], payments=[{"amount": "500", "method": "CASH"}]), 201
    )
    seen = _ok(preparer.api.get(f"/restaurant/orders/{order['id']}"))
    assert (seen["sale_number"], seen["payment_status"], seen["amount_due"]) == (
        settled["order"]["sale_number"],
        "PARTIALLY_PAID",
        "900.00",
    )
    assert preparer.api.get(f"/sales/{settled['sale_id']}/payments").status_code == 403
    assert preparer.api.get(f"/sales/{settled['sale_id']}").status_code == 403


def test_settlement_respects_sites_and_companies(
    resto: SimpleNamespace, provision: Any, oclient: TestClient, app_engine: Any
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    t2 = provision("ro-pay-2", profile=MAQUIS, plan="ENTREPRISE")
    stranger = _api(oclient, "owner@ro-pay-2.example.com")
    assert _code(_settle(stranger, order["id"], payments=_cash("700"))) == (404, "order_not_found")
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("700")), 201)
    assert stranger.get(f"/sales/{settled['sale_id']}").status_code == 404
    assert stranger.get("/sales", params={"channel": "RESTAURANT"}).json()["total"] == 0
    # RLS : l'autre entreprise ne voit aucune vente d'origine, même en SQL (rôle applicatif).
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=t2.tenant_id)
        assert (
            db.execute(text("SELECT count(*) FROM sales WHERE origin_id IS NOT NULL")).scalar_one()
            == 0
        )
        db.rollback()
        set_db_context(db, tenant_id=r.tenant)
        assert (
            db.execute(text("SELECT count(*) FROM sales WHERE origin_id IS NOT NULL")).scalar_one()
            == 1
        )
        db.rollback()


def test_sales_never_depend_on_restaurant() -> None:
    """Port d'origine : le module Ventes ne connaît pas la restauration (aucun import)."""
    root = Path(__file__).resolve().parents[1] / "app" / "modules"
    for path in (root / "sales").glob("*.py"):
        assert "restaurant_orders" not in path.read_text(encoding="utf-8"), path.name
    for path in (root / "pos").glob("*.py"):
        assert "restaurant" not in path.read_text(encoding="utf-8"), path.name


def test_production_registry_still_hides_settlement(provision: Any, api_for: Any) -> None:
    provision("ro-pay-prod", profile=MAQUIS)
    owner = api_for("owner@ro-pay-prod.example.com")
    response = owner.post(
        f"/restaurant/orders/{uuid.uuid4()}/settle", json={"idempotency_key": str(uuid.uuid4())}
    )
    assert response.status_code == 404  # routes non montées (N1)
    caps = owner.get("/me/capabilities").json()
    assert not [c for c in caps["permissions"] if c.startswith(f"{ORDERS}.")]
