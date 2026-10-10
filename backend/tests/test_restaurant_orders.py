"""Palier R2-B (ADR-0049 D14) — moteur de commandes : T1, lignes, préparation, annulations,
réglages, ticket, ports, empreinte, sécurité.

``restaurant.orders`` reste PLANIFIÉ dans le registre de production jusqu'à R2-E (N1) : ces tests
utilisent un registre de test où le module est disponible (fixture ``orders_api``) ; un test
vérifie explicitement que la production ne l'expose pas.
"""

import threading
import uuid
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import create_session_factory, set_db_context
from app.modules.restaurant_orders.settings import next_order_number, order_sequence_key
from app.platform.catalog.loader import load_catalog
from app.platform.registry import ModuleStatus, get_registry
from app.platform.sequences.service import site_has_numbers
from tests.conftest import PASSWORD, Api, add_site, login, set_site_module

ORDERS = "restaurant.orders"
MAQUIS = "restaurant.maquis"
P = "restaurant.orders.order"


def _api(client: TestClient, email: str, password: str = PASSWORD) -> Api:
    response = login(client, email, password)
    assert response.status_code == 200, response.text
    return Api(client=client, token=response.json()["access_token"])


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def _menu(owner: Api, site: str, suffix: str) -> SimpleNamespace:
    """Article (bouteille, prix 700) au menu en unité de base et en casier de 12 (8 000)."""
    category = _ok(owner.post("/catalog/categories", json={"name": f"Boissons {suffix}"}), 201)
    article = _ok(
        owner.post(
            "/catalog/articles",
            json={
                "reference": f"BIERE-{suffix}",
                "designation": f"Bière {suffix}",
                "category_id": category["id"],
                "unit": "bouteille",
                "purchase_price": "400",
                "sale_price": "700",
                "site_ids": [site],
            },
        ),
        201,
    )
    crate = _ok(
        owner.post(
            f"/catalog/articles/{article['id']}/packagings",
            json={"name": "Casier 12", "conversion": "12", "sale_price": "8000"},
        ),
        201,
    )
    section = _ok(
        owner.post("/restaurant/menu/sections", json={"site_id": site, "name": "Boissons"}), 201
    )
    unit_item = _ok(
        owner.post(
            "/restaurant/menu/items",
            json={"site_id": site, "section_id": section["id"], "article_id": article["id"]},
        ),
        201,
    )
    crate_item = _ok(
        owner.post(
            "/restaurant/menu/items",
            json={
                "site_id": site,
                "section_id": section["id"],
                "article_id": article["id"],
                "packaging_id": crate["id"],
            },
        ),
        201,
    )
    return SimpleNamespace(
        article=article["id"], crate=crate["id"], unit=unit_item["id"], crate_item=crate_item["id"]
    )


@pytest.fixture
def resto(provision: Any, oclient: TestClient) -> SimpleNamespace:
    t = provision("ro-main", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-main.example.com")
    site = str(t.site_id)
    return SimpleNamespace(
        tenant=t.tenant_id, site=site, owner=owner, client=oclient, menu=_menu(owner, site, "A")
    )


def _order(api: Api, site: str, lines: list[dict[str, Any]], **extra: Any) -> Any:
    return api.post(
        "/restaurant/orders",
        json={
            "site_id": site,
            "service_mode": "ON_SITE",
            "lines": lines,
            "idempotency_key": str(uuid.uuid4()),
            **extra,
        },
    )


def _line(item: str, quantity: str = "1", **extra: Any) -> dict[str, Any]:
    return {"menu_item_id": item, "quantity": quantity, **extra}


def _member(r: SimpleNamespace, email: str, template: str, **access: Any) -> Api:
    roles = {x["template_code"]: x["id"] for x in r.owner.get("/roles").json()}
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{"role_id": roles[template]}],
        **({"all_sites": True} | access),
    }
    _ok(r.owner.post("/members", json=body), 201)
    temporary = _api(r.client, email, "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return _api(r.client, email)


# --- Production : le module reste planifié (N1) -------------------------------------------------


def test_production_registry_keeps_orders_planned(provision: Any, api_for: Any) -> None:
    assert get_registry().get(ORDERS).status is ModuleStatus.PLANNED
    t = provision("ro-prod", profile=MAQUIS)
    owner = api_for("owner@ro-prod.example.com")
    assert owner.get("/restaurant/orders").status_code == 404  # routes non montées
    assert owner.get(f"/restaurant/settings/{t.site_id}").status_code == 404
    refused = set_site_module(owner, t.site_id, ORDERS, True)
    assert _code(refused) == (422, "module_not_implemented")
    modules = {m["code"]: m for m in owner.get(f"/sites/{t.site_id}/modules").json()}
    assert modules[ORDERS]["status"] == "planned"
    # Activation inerte (défaut du profil) : aucune permission accordée par un module planifié,
    # ni à l'Administrateur (``*``), ni dans les rôles délégables.
    caps = owner.get("/me/capabilities").json()
    assert not [c for c in caps["permissions"] if c.startswith("restaurant.orders.")]
    delegable = {p["code"] for p in owner.get("/permissions/delegable").json()}
    assert not [c for c in delegable if c.startswith("restaurant.orders.")]


# --- Clé du compteur (D14) ----------------------------------------------------------------------


def test_order_key_fits_the_counter_column() -> None:
    for _ in range(50):
        site = uuid.uuid4()
        key = order_sequence_key(site, date(2026, 12, 31))
        assert len(key) == 48 <= 50
        assert key == f"ro:{site}:20261231"
        # Jamais préfixée par ``{site_id}:`` : ne fige jamais le code du site.
        assert not key.startswith(f"{site}:")


def test_numbers_are_isolated_per_site_and_per_day(
    resto: SimpleNamespace, app_engine: Engine, owner_db: Session
) -> None:
    r = resto
    first = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    second = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    assert (first["daily_number"], second["daily_number"]) == (1, 2)
    other = _ok(
        add_site(r.owner, "Maquis 2", "MAQ2", "restaurant", business_profile_code=MAQUIS), 201
    )
    menu_b = _menu(r.owner, other["id"], "B")
    assert _ok(_order(r.owner, other["id"], [_line(menu_b.unit)]), 201)["daily_number"] == 1
    # Autre jour de l'entreprise : nouveau compteur (même site).
    today = date.fromisoformat(first["business_date"])
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=r.tenant)
        assert next_order_number(db, r.tenant, uuid.UUID(r.site), today + timedelta(days=1)) == 1
        assert next_order_number(db, r.tenant, uuid.UUID(r.site), today) == 3
        db.rollback()
    keys = set(
        owner_db.execute(
            text("SELECT sequence_key FROM document_sequences WHERE tenant_id = :t"),
            {"t": r.tenant},
        ).scalars()
    )
    assert order_sequence_key(uuid.UUID(r.site), today) in keys
    assert order_sequence_key(uuid.UUID(other["id"]), today) in keys


def test_key_is_stable_when_the_site_code_or_name_changes(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    assert _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)["daily_number"] == 1
    # Les commandes ne figent pas le code du site (clé hors préfixe ``{site_id}:``).
    assert not site_has_numbers(owner_db, uuid.UUID(r.site))
    _ok(r.owner.patch(f"/sites/{r.site}", json={"code": "NOUVEAU", "name": "Autre nom"}))
    assert _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)["daily_number"] == 2


# --- Réglages du site (site_setup, N5) ----------------------------------------------------------


def test_settings_follow_the_profile_and_survive_deactivation(
    provision: Any, oclient: TestClient, owner_db: Session
) -> None:
    t = provision("ro-bakery", profile="restaurant.boulangerie", plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-bakery.example.com")
    site = str(t.site_id)
    settings = _ok(owner.get(f"/restaurant/settings/{site}"))
    assert settings["payment_timing"] == "AT_ORDER"  # défaut du profil (D11)
    assert (settings["claim_protection_minutes"], settings["claim_cooldown_minutes"]) == (5, 0)
    changed = _ok(
        owner.put(
            f"/restaurant/settings/{site}",
            json={
                "payment_timing": "AT_END",
                "claim_protection_minutes": 10,
                "claim_cooldown_minutes": 2,
            },
        )
    )
    assert changed["payment_timing"] == "AT_END"
    # Désactiver ne supprime rien ; réactiver retrouve les réglages (jamais écrasés).
    assert set_site_module(owner, site, ORDERS, False).status_code == 204
    assert owner_db.execute(
        text("SELECT payment_timing FROM restaurant_site_settings WHERE site_id = :s"), {"s": site}
    ).scalar_one() == ("AT_END")
    assert set_site_module(owner, site, ORDERS, True).status_code == 204
    again = _ok(owner.get(f"/restaurant/settings/{site}"))
    assert (again["payment_timing"], again["claim_protection_minutes"]) == ("AT_END", 10)
    audit = owner_db.execute(
        text(
            "SELECT data FROM audit_logs WHERE action = 'restaurant_order.settings_updated' "
            "AND tenant_id = :t"
        ),
        {"t": t.tenant_id},
    ).scalar_one()
    assert audit["before"]["payment_timing"] == "AT_ORDER"
    assert audit["after"]["payment_timing"] == "AT_END"


def test_profile_payment_defaults_are_valid() -> None:
    from app.modules.restaurant_orders.models import PaymentTiming

    for profile in load_catalog(get_registry()).profiles.values():
        timing = profile.module_settings.get(ORDERS, {}).get("payment_timing")
        if timing is not None:
            PaymentTiming(timing)


def test_settings_require_their_permission(resto: SimpleNamespace) -> None:
    r = resto
    seller = _member(r, "vendeur@ro-main.example.com", "seller")
    manager = _member(r, "gerant@ro-main.example.com", "manager")
    body = {"payment_timing": "AT_ORDER"}
    assert _code(seller.put(f"/restaurant/settings/{r.site}", json=body)) == (
        403,
        "permission_denied",
    )
    assert _ok(manager.put(f"/restaurant/settings/{r.site}", json=body))["payment_timing"] == (
        "AT_ORDER"
    )
    assert _ok(seller.get(f"/restaurant/settings/{r.site}"))["payment_timing"] == "AT_ORDER"


# --- T1 : création ------------------------------------------------------------------------------


def test_create_freezes_prices_and_copies_the_payment_mode(resto: SimpleNamespace) -> None:
    r = resto
    created = _ok(
        _order(
            r.owner,
            r.site,
            [_line(r.menu.unit, "2", note="Bien fraîche"), _line(r.menu.crate_item, "1")],
            call_name="  Awa  ",
            service_mode="TAKEAWAY",
        ),
        201,
    )
    assert created["status"] == "OPEN" and created["prep_status"] == "RECEIVED"
    assert created["settlement_status"] == "UNSETTLED"
    assert (created["payment_timing"], created["channel"]) == ("AT_END", "STAFF")
    assert (created["call_name"], created["service_mode"]) == ("Awa", "TAKEAWAY")
    lines = {li["line_no"]: li for li in created["lines"]}
    assert (lines[1]["unit_price"], lines[1]["line_total"], lines[1]["note"]) == (
        "700.00",
        "1400.00",
        "Bien fraîche",
    )
    assert (lines[2]["packaging_name"], lines[2]["base_quantity"], lines[2]["unit_price"]) == (
        "Casier 12",
        "12.000",
        "8000.00",
    )
    assert created["total"] == "9400.00"
    # Prix du catalogue modifié ensuite : la commande garde ses prix figés.
    _ok(r.owner.patch(f"/catalog/articles/{r.menu.article}", json={"sale_price": "900"}))
    # Réglage du site modifié ensuite : la commande garde son mode recopié.
    _ok(r.owner.put(f"/restaurant/settings/{r.site}", json={"payment_timing": "AT_ORDER"}))
    again = _ok(r.owner.get(f"/restaurant/orders/{created['id']}"))
    assert again["lines"][0]["unit_price"] == "700.00" and again["total"] == "9400.00"
    assert again["payment_timing"] == "AT_END"
    later = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    assert later["lines"][0]["unit_price"] == "900.00" and later["payment_timing"] == "AT_ORDER"
    # Ni vente, ni stock : la création n'écrit rien d'autre.
    assert r.owner.get("/sales").json()["total"] == 0


def test_creation_is_idempotent_and_numbers_are_unique_under_concurrency(
    resto: SimpleNamespace,
) -> None:
    r = resto
    key = str(uuid.uuid4())
    body = {
        "site_id": r.site,
        "service_mode": "COUNTER",
        "lines": [_line(r.menu.unit)],
        "idempotency_key": key,
    }
    first = r.owner.post("/restaurant/orders", json=body)
    replay = r.owner.post("/restaurant/orders", json=body)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert first.json()["id"] == replay.json()["id"]
    numbers: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def create() -> None:
        barrier.wait()
        response = _order(r.owner, r.site, [_line(r.menu.unit)])
        with lock:
            numbers.append(response.json()["daily_number"])

    threads = [threading.Thread(target=create) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(numbers) == list(range(2, 8))
    assert _ok(r.owner.get("/restaurant/orders", params={"limit": 50}))["total"] == 7


def test_only_orderable_menu_items_of_the_site_are_accepted(resto: SimpleNamespace) -> None:
    r = resto
    _ok(
        r.owner.put(
            f"/restaurant/menu/items/{r.menu.crate_item}/availability",
            json={"available": False, "reason": "Rupture"},
        )
    )
    refused = _order(r.owner, r.site, [_line(r.menu.unit), _line(r.menu.crate_item)])
    assert _code(refused) == (422, "menu_item_not_orderable")
    assert refused.json()["items"][0]["blockers"] == ["unavailable"]
    other = _ok(
        add_site(r.owner, "Maquis 2", "MAQ2", "restaurant", business_profile_code=MAQUIS), 201
    )
    foreign = _menu(r.owner, other["id"], "B")
    assert _code(_order(r.owner, r.site, [_line(foreign.unit)])) == (404, "menu_item_not_found")
    assert _code(_order(r.owner, r.site, [_line(r.menu.unit, "1.5")])) == (
        422,
        "quantity_not_whole",
    )
    missing_customer = _order(r.owner, r.site, [_line(r.menu.unit)], customer_id=str(uuid.uuid4()))
    assert _code(missing_customer) == (422, "customer_not_found")
    customer = _ok(
        r.owner.post("/customers", json={"customer_type": "INDIVIDUAL", "name": "Koffi"}), 201
    )
    with_customer = _ok(
        _order(r.owner, r.site, [_line(r.menu.unit)], customer_id=customer["id"]), 201
    )
    assert with_customer["customer_name"] == "Koffi"


# --- Préparation et service ---------------------------------------------------------------------


def test_preparation_flow_by_order_and_by_line(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit), _line(r.menu.crate_item)]), 201)
    oid = order["id"]
    base = f"/restaurant/orders/{oid}"
    assert _code(r.owner.post(f"{base}/serve")) == (409, "order_lines_not_in_state")
    started = _ok(r.owner.post(f"{base}/start"))
    assert started["prep_status"] == "IN_PREPARATION"
    assert started["line_counts"]["in_preparation"] == 2
    ready = _ok(r.owner.post(f"{base}/ready"))
    assert ready["prep_status"] == "READY"
    # Correction : prête → en préparation, puis prête de nouveau.
    first_line = ready["lines"][0]["id"]
    reverted = _ok(r.owner.post(f"{base}/revert", json={"line_ids": [first_line]}))
    assert reverted["prep_status"] == "IN_PREPARATION"
    _ok(r.owner.post(f"{base}/ready", json={"line_ids": [first_line]}))
    # Une boisson servie avant l'autre (P-2) : action par ligne.
    served = _ok(r.owner.post(f"{base}/serve", json={"line_ids": [first_line]}))
    assert served["prep_status"] == "READY" and served["line_counts"]["served"] == 1
    assert served["last_served_at"] is not None
    wrong = r.owner.post(f"{base}/serve", json={"line_ids": [first_line]})
    assert _code(wrong) == (409, "order_line_state_invalid")
    done = _ok(r.owner.post(f"{base}/serve"))
    assert done["prep_status"] == "SERVED"
    # Servie mais non réglée : la commande reste ouverte (aucune clôture sans règlement).
    assert (done["status"], done["settlement_status"]) == ("OPEN", "UNSETTLED")
    assert done["version"] > order["version"]
    events = [e["event_type"] for e in _ok(r.owner.get(f"{base}/events"))]
    assert events == [
        "CREATED",
        "PREP_STARTED",
        "READY",
        "READY_REVERTED",
        "READY",
        "SERVED",
        "SERVED",
    ]
    backlog = _ok(r.owner.get("/restaurant/orders", params={"unsettled_served": True}))
    assert [o["id"] for o in backlog["items"]] == [oid]
    actions = set(
        owner_db.execute(
            text("SELECT action FROM audit_logs WHERE entity_id = :o"), {"o": oid}
        ).scalars()
    )
    assert {
        "restaurant_order.created",
        "restaurant_order.prep_started",
        "restaurant_order.ready",
        "restaurant_order.ready_reverted",
        "restaurant_order.served",
    } <= actions


def test_payment_at_order_blocks_preparation_until_settled(resto: SimpleNamespace) -> None:
    r = resto
    _ok(r.owner.put(f"/restaurant/settings/{r.site}", json={"payment_timing": "AT_ORDER"}))
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    assert order["payment_timing"] == "AT_ORDER"
    refused = r.owner.post(f"/restaurant/orders/{order['id']}/start")
    assert _code(refused) == (409, "order_not_settled")


def test_final_states_are_protected_in_the_database(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit), _line(r.menu.crate_item)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    served_line, other_line = (li["id"] for li in order["lines"])
    for action in ("start", "ready"):
        _ok(r.owner.post(f"{base}/{action}", json={"line_ids": [served_line]}))
    _ok(r.owner.post(f"{base}/serve", json={"line_ids": [served_line]}))
    statements = [
        ("UPDATE restaurant_order_lines SET status = 'READY' WHERE id = :i", served_line),
        ("UPDATE restaurant_order_lines SET unit_price = 1 WHERE id = :i", other_line),
        ("UPDATE restaurant_order_lines SET quantity = 9 WHERE id = :i", other_line),
    ]
    for sql, line_id in statements:
        with pytest.raises(DBAPIError, match="restaurant_final_state"):
            owner_db.execute(text(sql), {"i": line_id})
        owner_db.rollback()
    cancelled = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(r.owner.post(f"/restaurant/orders/{cancelled['id']}/cancel", json={"reason": "Erreur"}))
    with pytest.raises(DBAPIError, match="restaurant_final_state"):
        owner_db.execute(
            text("UPDATE restaurant_orders SET status = 'OPEN' WHERE id = :i"),
            {"i": cancelled["id"]},
        )
    owner_db.rollback()


# --- Annulations --------------------------------------------------------------------------------


def test_cancellation_rules_and_permissions(resto: SimpleNamespace) -> None:
    r = resto
    seller = _member(r, "vendeur@ro-main.example.com", "seller")
    manager = _member(r, "gerant@ro-main.example.com", "manager")
    order = _ok(
        _order(
            seller,
            r.site,
            [_line(r.menu.unit), _line(r.menu.crate_item), _line(r.menu.unit, "3")],
        ),
        201,
    )
    base = f"/restaurant/orders/{order['id']}"
    first, second, third = (li["id"] for li in order["lines"])
    blank = seller.post(f"{base}/cancel-lines", json={"line_ids": [first], "reason": "  "})
    assert blank.status_code == 422
    # Ligne reçue : le Vendeur annule (``order.cancel``).
    cancelled = _ok(
        seller.post(f"{base}/cancel-lines", json={"line_ids": [first], "reason": "Erreur"})
    )
    assert cancelled["lines"][0]["status"] == "CANCELLED"
    assert cancelled["lines"][0]["cancel_reason"] == "Erreur"
    # Ligne en préparation : ``order.cancel_prepared`` (Gestionnaire), jamais le Vendeur.
    _ok(seller.post(f"{base}/start", json={"line_ids": [second]}))
    refused = seller.post(f"{base}/cancel-lines", json={"line_ids": [second], "reason": "Casse"})
    assert refused.json()["code"] == "permission_denied"
    _ok(manager.post(f"{base}/cancel-lines", json={"line_ids": [second], "reason": "Casse"}))
    again = seller.post(f"{base}/cancel-lines", json={"line_ids": [first], "reason": "Encore"})
    assert _code(again) == (409, "order_line_final")
    # Servie : définitive, et la commande ne s'annule plus.
    for action in ("start", "ready", "serve"):
        _ok(seller.post(f"{base}/{action}", json={"line_ids": [third]}))
    assert _code(seller.post(f"{base}/cancel", json={"reason": "Parti"})) == (
        409,
        "order_has_served_lines",
    )


def test_empty_order_is_cancelled_with_a_reason(resto: SimpleNamespace) -> None:
    r = resto
    seller = _member(r, "vendeur@ro-main.example.com", "seller")
    order = _ok(_order(seller, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    emptied = _ok(
        seller.post(
            f"{base}/cancel-lines", json={"line_ids": [order["lines"][0]["id"]], "reason": "Erreur"}
        )
    )
    # Toutes les lignes annulées : la commande reste ouverte, sans ligne active (N4).
    assert (emptied["status"], emptied["prep_status"], emptied["total"]) == ("OPEN", "NONE", "0.00")
    assert seller.post(f"{base}/cancel", json={}).status_code == 422  # motif obligatoire
    done = _ok(seller.post(f"{base}/cancel", json={"reason": "Client parti"}))
    assert (done["status"], done["cancel_reason"]) == ("CANCELLED", "Client parti")
    assert _code(seller.post(f"{base}/start")) == (409, "order_cancelled")
    assert _code(seller.post(f"{base}/lines", json={"lines": [_line(r.menu.unit)]})) == (
        409,
        "order_cancelled",
    )
    active = _ok(seller.get("/restaurant/orders"))
    assert order["id"] not in {o["id"] for o in active["items"]}
    gone = _ok(seller.get("/restaurant/orders", params={"state": "cancelled"}))
    assert [o["id"] for o in gone["items"]] == [order["id"]]


def test_add_lines_is_idempotent(resto: SimpleNamespace) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    _ok(r.owner.post(f"{base}/start"))
    body = {"lines": [_line(r.menu.crate_item, "2")], "idempotency_key": str(uuid.uuid4())}
    added = _ok(r.owner.post(f"{base}/lines", json=body))
    replay = _ok(r.owner.post(f"{base}/lines", json=body))
    assert [li["line_no"] for li in replay["lines"]] == [1, 2]
    assert added["prep_status"] == "RECEIVED"  # nouvelle ligne reçue : la moins avancée
    assert added["total"] == "16700.00"


# --- Lecture, ticket, sécurité ------------------------------------------------------------------


def test_list_search_and_read_only_access(resto: SimpleNamespace) -> None:
    r = resto
    viewer = _member(r, "consultant@ro-main.example.com", "viewer")
    first = _ok(_order(r.owner, r.site, [_line(r.menu.unit)], call_name="Moussa"), 201)
    _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    listed = _ok(viewer.get("/restaurant/orders"))
    assert [o["daily_number"] for o in listed["items"]] == [1, 2]  # ancienneté
    assert _ok(viewer.get("/restaurant/orders", params={"search": "#1"}))["total"] == 1
    by_name = _ok(viewer.get("/restaurant/orders", params={"search": "mous"}))
    assert [o["id"] for o in by_name["items"]] == [first["id"]]
    assert _ok(viewer.get(f"/restaurant/orders/{first['id']}"))["lines"][0]["label"]
    assert _code(_order(viewer, r.site, [_line(r.menu.unit)])) == (403, "permission_denied")
    assert viewer.post(f"/restaurant/orders/{first['id']}/start").status_code == 403


def test_ticket_has_no_price_cost_or_stock(resto: SimpleNamespace) -> None:
    r = resto
    order = _ok(
        _order(
            r.owner,
            r.site,
            [_line(r.menu.unit, "2", note="Sans glace"), _line(r.menu.crate_item)],
            call_name="Awa",
            service_mode="TAKEAWAY",
        ),
        201,
    )
    _ok(
        r.owner.post(
            f"/restaurant/orders/{order['id']}/cancel-lines",
            json={"line_ids": [order["lines"][1]["id"]], "reason": "Erreur"},
        )
    )
    ticket = _ok(r.owner.get(f"/restaurant/orders/{order['id']}/ticket"))
    assert (ticket["daily_number"], ticket["call_name"], ticket["service_mode"]) == (
        1,
        "Awa",
        "TAKEAWAY",
    )
    assert ticket["company_name"] and ticket["site_name"]
    assert [li["label"] for li in ticket["lines"]] == ["Bière A"]  # ligne annulée absente
    assert ticket["lines"][0]["note"] == "Sans glace"
    flat = repr(ticket).lower()
    for forbidden in ("price", "total", "cost", "stock", "8000", "700"):
        assert forbidden not in flat
    # Réimpression libre, non journalisée.
    assert r.owner.get(f"/restaurant/orders/{order['id']}/ticket").status_code == 200


def test_isolation_between_sites_and_companies(
    resto: SimpleNamespace, provision: Any, oclient: TestClient, app_engine: Engine
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    other = _ok(
        add_site(r.owner, "Maquis 2", "MAQ2", "restaurant", business_profile_code=MAQUIS), 201
    )
    limited = _member(
        r, "site2@ro-main.example.com", "seller", all_sites=False, site_ids=[other["id"]]
    )
    assert _code(limited.get(f"/restaurant/orders/{order['id']}")) == (404, "order_not_found")
    assert _ok(limited.get("/restaurant/orders"))["total"] == 0
    # Site sélectionné sans commandes effectives : refus du module.
    assert set_site_module(r.owner, other["id"], ORDERS, False).status_code == 204
    selected = Api(limited.client, limited.token, uuid.UUID(other["id"]))
    assert _code(selected.get("/restaurant/orders")) == (403, "module_unavailable")
    # Autre entreprise : introuvable (API) et invisible (RLS).
    t2 = provision("ro-other", profile=MAQUIS, plan="ENTREPRISE")
    stranger = _api(oclient, "owner@ro-other.example.com")
    assert _code(stranger.get(f"/restaurant/orders/{order['id']}")) == (404, "order_not_found")
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=t2.tenant_id)
        # Seules les données de l'autre entreprise (ses propres réglages) sont visibles.
        expected = {
            "restaurant_orders": 0,
            "restaurant_order_lines": 0,
            "restaurant_order_events": 0,
            "restaurant_site_settings": 1,
        }
        for table, count in expected.items():
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == count
        db.rollback()


def test_app_role_privileges(owner_db: Session, settings: Settings) -> None:
    role = settings.db_app_role
    rows = owner_db.execute(
        text(
            "SELECT table_name, privilege_type FROM information_schema.table_privileges "
            "WHERE grantee = :r AND table_name LIKE 'restaurant_order%' "
            "OR grantee = :r AND table_name = 'restaurant_site_settings'"
        ),
        {"r": role},
    ).all()
    granted: dict[str, set[str]] = {}
    for table, privilege in rows:
        granted.setdefault(table, set()).add(privilege)
    for table in ("restaurant_orders", "restaurant_order_lines", "restaurant_site_settings"):
        assert granted[table] == {"SELECT", "INSERT"}  # UPDATE par colonne seulement
    assert granted["restaurant_order_events"] == {"SELECT", "INSERT"}
    updatable = set(
        owner_db.execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges "
                "WHERE grantee = :r AND table_name = 'restaurant_order_lines' "
                "AND privilege_type = 'UPDATE'"
            ),
            {"r": role},
        ).scalars()
    )
    assert "unit_price" not in updatable and "status" in updatable
    events_update = owner_db.execute(
        text(
            "SELECT count(*) FROM information_schema.column_privileges WHERE grantee = :r "
            "AND table_name = 'restaurant_order_events' AND privilege_type = 'UPDATE'"
        ),
        {"r": role},
    ).scalar_one()
    assert events_update == 0
    for table in (
        "restaurant_orders",
        "restaurant_order_lines",
        "restaurant_order_events",
        "restaurant_site_settings",
    ):
        flags = owner_db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
        assert tuple(flags) == (True, True)


# --- Ports du catalogue et empreinte ------------------------------------------------------------


def test_open_orders_block_catalog_changes(resto: SimpleNamespace) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.crate_item)]), 201)
    removal = r.owner.post(
        f"/catalog/sites/{r.site}/articles/remove", json={"article_ids": [r.menu.article]}
    )
    assert _code(removal) == (409, "article_in_open_documents")
    lot = r.owner.patch(f"/catalog/articles/{r.menu.article}", json={"lot_tracked": True})
    assert _code(lot) == (409, "article_in_open_documents")
    conversion = r.owner.patch(f"/catalog/packagings/{r.menu.crate}", json={"conversion": "24"})
    assert _code(conversion) == (409, "packaging_in_use")
    _ok(r.owner.post(f"/restaurant/orders/{order['id']}/cancel", json={"reason": "Erreur"}))
    # Commande finale : le retrait de l'assortiment redevient possible ; la conversion reste
    # figée (une ligne l'a utilisée).
    _ok(
        r.owner.post(
            f"/catalog/sites/{r.site}/articles/remove", json={"article_ids": [r.menu.article]}
        )
    )
    assert _code(
        r.owner.patch(f"/catalog/packagings/{r.menu.crate}", json={"conversion": "24"})
    ) == (409, "packaging_in_use")


def test_open_orders_are_work_in_progress(resto: SimpleNamespace) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    refused = set_site_module(r.owner, r.site, ORDERS, False)
    assert _code(refused) == (409, "module_has_open_operations")
    assert refused.json()["operations"] == {"restaurant_orders": 1}
    preview = _ok(
        r.owner.get(
            f"/sites/{r.site}/business-profile/preview",
            params={"profile_code": "retail.alimentation"},
        )
    )
    assert preview["level"] == "BLOCKED"
    _ok(r.owner.post(f"/restaurant/orders/{order['id']}/cancel", json={"reason": "Erreur"}))
    assert set_site_module(r.owner, r.site, ORDERS, False).status_code == 204


def test_single_engine_without_pos_coupling() -> None:
    """Un seul moteur de commandes (D14, Q2) : le canal est posé par la route ; le module des
    commandes n'importe rien du POS et le POS n'importe rien des commandes."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "modules"
    orders = "\n".join(p.read_text() for p in (root / "restaurant_orders").glob("*.py"))
    pos = "\n".join(p.read_text() for p in (root / "pos").glob("*.py"))
    assert "app.modules.pos" not in orders
    assert "restaurant_orders" not in pos and "checkout" not in orders
