"""Palier R6 (ADR-0050) — cuisine : postes de préparation, affectations du menu, routage des
lignes, priorités, écran cuisine, avis par poste.

Exercé par l'API avec le registre de test où ``restaurant.kitchen`` est disponible (fixture
``kclient``) ; le registre de production le garde planifié pendant le développement.
"""

import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.platform.registry import ModuleStatus, get_registry
from tests.conftest import PASSWORD, Api, add_site, set_site_module
from tests.test_restaurant_orders import _api, _code, _line, _menu, _ok, _order

KITCHEN = "restaurant.kitchen"
RESTAURANT = "restaurant.restaurant"
STATIONS = "/restaurant/kitchen/stations"
ROUTES = "/restaurant/kitchen/routes"
BOARD = "/restaurant/kitchen/board"


@pytest.fixture
def resto(provision: Any, kclient: TestClient) -> SimpleNamespace:
    """Restaurant ENTREPRISE, deux sites (cuisine activée par défaut : profil Restaurant) ; menu
    du site A : boissons (bouteille, casier) et plats (une section par famille)."""
    t = provision("rk-main", profile=RESTAURANT, plan="ENTREPRISE")
    owner = _api(kclient, "owner@rk-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER", "restaurant"), 201)["id"]
    drinks = _menu(owner, a, "K")
    dish = _dish(owner, a, "PLAT")
    return SimpleNamespace(
        tenant=t.tenant_id, owner=owner, client=kclient, a=a, b=b, drinks=drinks, dish=dish
    )


def _dish(owner: Api, site: str, suffix: str) -> SimpleNamespace:
    """Plat (portion, 2 500) dans sa propre section « Plats » du site."""
    category = _ok(owner.post("/catalog/categories", json={"name": f"Plats {suffix}"}), 201)
    article = _ok(
        owner.post(
            "/catalog/articles",
            json={
                "reference": f"PLAT-{suffix}",
                "designation": f"Poulet {suffix}",
                "category_id": category["id"],
                "unit": "portion",
                "purchase_price": "1500",
                "sale_price": "2500",
                "stock_managed": False,
                "site_ids": [site],
            },
        ),
        201,
    )
    section = _ok(
        owner.post("/restaurant/menu/sections", json={"site_id": site, "name": "Plats"}), 201
    )
    item = _ok(
        owner.post(
            "/restaurant/menu/items",
            json={"site_id": site, "section_id": section["id"], "article_id": article["id"]},
        ),
        201,
    )
    return SimpleNamespace(section=section["id"], item=item["id"])


def _station(r: SimpleNamespace, name: str, site: str | None = None, **extra: Any) -> Any:
    return r.owner.post(STATIONS, json={"site_id": site or r.a, "name": name, **extra})


def _route(r: SimpleNamespace, station: str | None, api: Api | None = None, **target: Any) -> Any:
    return (api or r.owner).put(
        ROUTES, json={"site_id": target.pop("site", r.a), "station_id": station, **target}
    )


def _section_of_drinks(r: SimpleNamespace) -> str:
    item = _ok(r.owner.get(f"/restaurant/menu/items/{r.drinks.unit}"))
    return str(item["section_id"])


def _detail(api: Api, order_id: str) -> dict[str, Any]:
    return dict(_ok(api.get(f"/restaurant/orders/{order_id}")))


def _events(api: Api, order_id: str, kind: str) -> list[dict[str, Any]]:
    return [
        e for e in _ok(api.get(f"/restaurant/orders/{order_id}/events")) if e["event_type"] == kind
    ]


def _audit(r: SimpleNamespace, action: str) -> list[dict[str, Any]]:
    items = _ok(r.owner.get("/audit-logs", params={"action": action, "limit": 50}))["items"]
    return list(reversed(items))


def _member(r: SimpleNamespace, email: str, template: str, **access: Any) -> Api:
    roles = {x["template_code"]: x["id"] for x in r.owner.get("/roles").json()}
    if template not in roles:
        roles[template] = _ok(
            r.owner.post("/roles/from-template", json={"template_code": template}), 201
        )["id"]
    body = {
        "email": email,
        "full_name": email.split("@")[0],
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


def _board(api: Api, site: str, **params: Any) -> dict[str, Any]:
    return dict(_ok(api.get(BOARD, params={"site_id": site, **params})))


def _stations_setup(r: SimpleNamespace) -> SimpleNamespace:
    """Postes « Bar » (section des boissons) et « Grill » (élément du plat)."""
    bar = _ok(_station(r, "Bar", sort_order=1), 201)["id"]
    grill = _ok(_station(r, "Grill", sort_order=0), 201)["id"]
    _ok(_route(r, bar, section_id=_section_of_drinks(r)))
    _ok(_route(r, grill, menu_item_id=r.dish.item))
    return SimpleNamespace(bar=bar, grill=grill)


# --- Postes ---------------------------------------------------------------------------------------


def test_stations_per_site_unique_ordered_and_audited(resto: SimpleNamespace) -> None:
    r = resto
    grill = _ok(_station(r, "  Grillades  ", sort_order=2), 201)
    assert (grill["name"], grill["site_id"], grill["is_active"], grill["route_count"]) == (
        "Grillades",
        r.a,
        True,
        0,
    )
    assert _code(_station(r, "GRILLADES")) == (409, "station_name_taken")
    _ok(_station(r, "Grillades", r.b), 201)  # même nom sur un autre site : permis
    _ok(_station(r, "Boissons", sort_order=1), 201)
    names = [s["name"] for s in _ok(r.owner.get(STATIONS, params={"site_id": r.a}))["items"]]
    assert names == ["Boissons", "Grillades"]  # ordre d'affichage
    updated = _ok(r.owner.put(f"{STATIONS}/{grill['id']}", json={"name": "Grill", "sort_order": 0}))
    assert (updated["name"], updated["sort_order"]) == ("Grill", 0)
    assert _code(r.owner.put(f"{STATIONS}/{grill['id']}", json={"name": "boissons"})) == (
        409,
        "station_name_taken",
    )
    off = _ok(r.owner.post(f"{STATIONS}/{grill['id']}/deactivate"))
    assert off["is_active"] is False
    assert _ok(r.owner.post(f"{STATIONS}/{grill['id']}/activate"))["is_active"] is True
    actions = [a["action"] for a in _audit(r, "restaurant_prep_station.updated")]
    assert actions == ["restaurant_prep_station.updated"]
    audit = _audit(r, "restaurant_prep_station.updated")[0]
    assert (
        audit["data"]["before"]["name"] == "Grillades" and audit["data"]["after"]["name"] == "Grill"
    )
    assert len(_audit(r, "restaurant_prep_station.deactivated")) == 1


def test_station_rights_and_isolation(
    resto: SimpleNamespace, provision: Any, kclient: TestClient
) -> None:
    r = resto
    station = _ok(_station(r, "Bar"), 201)["id"]
    seller = _member(r, "vendeur@rk-main.example.com", "seller")
    # Le Vendeur consulte (écran) sans configurer.
    assert _ok(seller.get(STATIONS))["total"] == 1
    assert _code(seller.post(STATIONS, json={"site_id": r.a, "name": "X"})) == (
        403,
        "permission_denied",
    )
    assert _code(
        seller.put(
            ROUTES, json={"site_id": r.a, "menu_item_id": r.dish.item, "station_id": station}
        )
    ) == (
        403,
        "permission_denied",
    )
    # Membre limité au site B : le poste du site A est introuvable.
    local = _member(r, "local@rk-main.example.com", "manager", all_sites=False, site_ids=[r.b])
    assert _ok(local.get(STATIONS))["items"] == []
    assert _code(local.get(f"{STATIONS}/{station}")) == (404, "station_not_found")
    assert _code(local.put(f"{STATIONS}/{station}", json={"name": "Pirate"})) == (
        404,
        "station_not_found",
    )
    # Autre entreprise : rien n'est visible.
    provision("rk-other", profile=RESTAURANT, plan="ENTREPRISE")
    stranger = _api(kclient, "owner@rk-other.example.com")
    assert _ok(stranger.get(STATIONS))["items"] == []
    assert _code(stranger.get(f"{STATIONS}/{station}")) == (404, "station_not_found")


# --- Affectations du menu -----------------------------------------------------------------------


def test_routes_section_or_item_same_site_active_station(resto: SimpleNamespace) -> None:
    r = resto
    bar = _ok(_station(r, "Bar"), 201)["id"]
    grill = _ok(_station(r, "Grill"), 201)["id"]
    other_site = _ok(_station(r, "Bar B", r.b), 201)["id"]
    section = _section_of_drinks(r)
    by_section = _ok(_route(r, bar, section_id=section))
    assert (by_section["station_name"], by_section["section_id"]) == ("Bar", section)
    by_item = _ok(_route(r, grill, menu_item_id=r.dish.item))
    assert by_item["menu_item_id"] == r.dish.item
    # Changement de poste : même affectation, mise à jour.
    moved = _ok(_route(r, grill, section_id=section))
    assert moved["id"] == by_section["id"] and moved["station_name"] == "Grill"
    assert {x["station_name"] for x in _ok(r.owner.get(ROUTES, params={"site_id": r.a}))} == {
        "Grill"
    }
    # Poste d'un autre site, poste désactivé : refusés.
    assert _code(_route(r, other_site, section_id=section)) == (404, "station_not_found")
    _ok(r.owner.post(f"{STATIONS}/{bar}/deactivate"))
    assert _code(_route(r, bar, menu_item_id=r.drinks.unit)) == (422, "station_inactive")
    # Cible d'un autre site ou inconnue : introuvable ; section ET élément : refus du schéma.
    assert _code(_route(r, grill, section_id=str(uuid.uuid4()))) == (404, "menu_section_not_found")
    assert _code(_route(r, grill, menu_item_id=r.dish.item, site=r.b)) == (
        404,
        "menu_item_not_found",
    )
    both = r.owner.put(
        ROUTES,
        json={
            "site_id": r.a,
            "station_id": grill,
            "section_id": section,
            "menu_item_id": r.dish.item,
        },
    )
    assert both.status_code == 422
    # Retrait : 204, l'affectation disparaît (ancienne valeur dans l'audit).
    removed = _route(r, None, section_id=section)
    assert removed.status_code == 204
    assert [x["menu_item_id"] for x in _ok(r.owner.get(ROUTES, params={"site_id": r.a}))] == [
        r.dish.item
    ]
    assert _audit(r, "restaurant_station_route.removed")[0]["data"]["previous_station_id"] == grill
    assert _route(r, None, section_id=section).status_code == 204  # déjà retirée : rien


# --- Routage des lignes ---------------------------------------------------------------------------


def test_lines_are_routed_item_then_section_then_none(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    order = _ok(
        _order(
            r.owner,
            r.a,
            [_line(r.drinks.unit), _line(r.dish.item), _line(r.drinks.crate_item)],
        ),
        201,
    )
    lines = {li["line_no"]: li for li in _detail(r.owner, order["id"])["lines"]}
    assert [lines[n]["station_id"] for n in (1, 2, 3)] == [s.bar, s.grill, s.bar]
    assert lines[2]["station_name"] == "Grill"
    created = _events(r.owner, order["id"], "CREATED")[0]
    assert created["data"]["stations"] == {"1": "Bar", "2": "Grill", "3": "Bar"}
    # Une ligne n'est que dans UNE file : un ticket par poste, aucune ligne en double.
    tickets = _board(r.owner, r.a)["tickets"]
    seen = [line["id"] for t in tickets for line in t["lines"]]
    assert sorted(seen) == sorted(li["id"] for li in lines.values())


def test_routing_fallbacks_and_idempotent_additions(resto: SimpleNamespace) -> None:
    r = resto
    bar = _ok(_station(r, "Bar"), 201)["id"]
    grill = _ok(_station(r, "Grill"), 201)["id"]
    _ok(_route(r, bar, section_id=_section_of_drinks(r)))
    _ok(_route(r, grill, menu_item_id=r.drinks.unit))
    _ok(r.owner.post(f"{STATIONS}/{grill}/deactivate"))
    order = _ok(_order(r.owner, r.a, [_line(r.drinks.unit), _line(r.dish.item)]), 201)
    lines = {li["line_no"]: li for li in _detail(r.owner, order["id"])["lines"]}
    # Poste de l'élément désactivé → poste de la section ; plat sans affectation → sans poste.
    assert (lines[1]["station_id"], lines[2]["station_id"]) == (bar, None)
    # Ajout rejoué (même clé) : aucune ligne ni routage en double.
    key = str(uuid.uuid4())
    body = {"lines": [_line(r.drinks.crate_item)], "idempotency_key": key}
    _ok(r.owner.post(f"/restaurant/orders/{order['id']}/lines", json=body))
    _ok(r.owner.post(f"/restaurant/orders/{order['id']}/lines", json=body))
    after = _detail(r.owner, order["id"])["lines"]
    assert [li["station_id"] for li in after] == [bar, None, bar]
    added = _events(r.owner, order["id"], "LINES_ADDED")
    assert len(added) == 1 and added[0]["data"]["stations"] == {"3": "Bar"}


def test_no_routing_when_the_kitchen_is_not_effective(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    assert set_site_module(r.owner, r.a, KITCHEN, False).status_code == 204
    order = _ok(_order(r.owner, r.a, [_line(r.drinks.unit), _line(r.dish.item)]), 201)
    assert [li["station_id"] for li in _detail(r.owner, order["id"])["lines"]] == [None, None]
    assert "stations" not in _events(r.owner, order["id"], "CREATED")[0]["data"]
    # Réactivée : les postes et affectations conservés reprennent le routage.
    assert set_site_module(r.owner, r.a, KITCHEN, True).status_code == 204
    again = _ok(_order(r.owner, r.a, [_line(r.dish.item)]), 201)
    assert _detail(r.owner, again["id"])["lines"][0]["station_id"] == s.grill


# --- Écran cuisine, statuts et priorités ----------------------------------------------------------


def test_board_tickets_per_station_sorted_by_priority_then_age(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    first = _ok(_order(r.owner, r.a, [_line(r.drinks.unit), _line(r.dish.item)]), 201)
    second = _ok(
        _order(r.owner, r.a, [_line(r.dish.item, "2", note="Bien cuit")], service_mode="TAKEAWAY"),
        201,
    )
    # Section des boissons désaffectée : la commande suivante n'a aucun poste.
    removed = r.owner.put(
        ROUTES, json={"site_id": r.a, "section_id": _section_of_drinks(r), "station_id": None}
    )
    assert removed.status_code == 204
    unrouted = _ok(_order(r.owner, r.a, [_line(r.drinks.crate_item)], service_mode="COUNTER"), 201)
    board = _board(r.owner, r.a)
    keys = [(t["daily_number"], t["station_name"]) for t in board["tickets"]]
    # Ancienneté (même priorité) : la première commande d'abord, ses deux postes ensemble.
    assert keys == [
        (first["daily_number"], "Bar"),
        (first["daily_number"], "Grill"),
        (second["daily_number"], "Grill"),
        (unrouted["daily_number"], None),
    ]
    assert board["counts"] == {"received": 4, "in_preparation": 0, "ready": 0}
    assert {st["name"] for st in board["stations"]} == {"Bar", "Grill"}
    takeaway = board["tickets"][2]
    assert takeaway["service_mode"] == "TAKEAWAY" and takeaway["lines"][0]["note"] == "Bien cuit"
    assert takeaway["lines"][0]["quantity"] == "2.000" and takeaway["state"] == "RECEIVED"
    # Priorité URGENTE : la deuxième commande passe devant, l'ancienneté reste affichée.
    _ok(
        r.owner.put(
            f"/restaurant/kitchen/orders/{second['id']}/priority", json={"priority": "URGENT"}
        )
    )
    board = _board(r.owner, r.a)
    assert board["tickets"][0]["daily_number"] == second["daily_number"]
    assert board["tickets"][0]["priority"] == "URGENT"
    assert board["tickets"][0]["received_at"] > board["tickets"][1]["received_at"]
    # Filtres : un poste, « Sans poste ».
    grill_only = _board(r.owner, r.a, station_id=s.grill)["tickets"]
    assert {t["station_id"] for t in grill_only} == {s.grill} and len(grill_only) == 2
    loose = _board(r.owner, r.a, unrouted="true")["tickets"]
    assert [t["daily_number"] for t in loose] == [unrouted["daily_number"]]


def test_multi_station_order_ready_only_when_every_line_is(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    order = _ok(_order(r.owner, r.a, [_line(r.drinks.unit), _line(r.dish.item)]), 201)
    lines = {li["station_id"]: li["id"] for li in _detail(r.owner, order["id"])["lines"]}
    base = f"/restaurant/orders/{order['id']}"
    # Le bar commence et termine ses lignes : la commande n'est PAS prête (grill en attente).
    _ok(r.owner.post(f"{base}/start", json={"line_ids": [lines[s.bar]]}))
    _ok(r.owner.post(f"{base}/ready", json={"line_ids": [lines[s.bar]]}))
    detail = _detail(r.owner, order["id"])
    assert detail["prep_status"] == "RECEIVED"
    states = {t["station_name"]: t["state"] for t in _board(r.owner, r.a)["tickets"]}
    assert states == {"Bar": "READY", "Grill": "RECEIVED"}
    # Action groupée sur toute la commande : un évènement PAR poste.
    _ok(r.owner.post(f"{base}/start"))
    started = _events(r.owner, order["id"], "PREP_STARTED")
    assert [e["data"]["station_name"] for e in started] == ["Bar", "Grill"]
    _ok(r.owner.post(f"{base}/ready"))
    assert _detail(r.owner, order["id"])["prep_status"] == "READY"
    # Servie : le ticket quitte l'écran.
    _ok(r.owner.post(f"{base}/serve"))
    assert _board(r.owner, r.a)["tickets"] == []


def test_ready_notice_per_station(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    waiter = _member(r, "serveur@rk-main.example.com", "waiter")
    order = _ok(_order(r.owner, r.a, [_line(r.drinks.unit), _line(r.dish.item)]), 201)
    lines = {li["station_id"]: li["id"] for li in _detail(r.owner, order["id"])["lines"]}
    base = f"/restaurant/orders/{order['id']}"
    _ok(r.owner.post(f"{base}/start"))
    _ok(r.owner.post(f"{base}/ready", json={"line_ids": [lines[s.bar]]}))
    notices = _ok(waiter.get("/member-notifications"))["items"]
    assert len(notices) == 1
    payload = notices[0]["payload"]
    assert (payload["part"], payload["station_name"], payload["line_count"]) == (True, "Bar", 1)
    _ok(r.owner.post(f"{base}/ready", json={"line_ids": [lines[s.grill]]}))
    names = sorted(
        n["payload"]["station_name"] for n in _ok(waiter.get("/member-notifications"))["items"]
    )
    assert names == ["Bar", "Grill"]


def test_priority_rights_values_and_trace(resto: SimpleNamespace) -> None:
    r = resto
    order = _ok(_order(r.owner, r.a, [_line(r.dish.item)]), 201)
    url = f"/restaurant/kitchen/orders/{order['id']}/priority"
    waiter = _member(r, "serveur@rk-main.example.com", "waiter")
    preparer = _member(r, "prep@rk-main.example.com", "preparer")
    assert _code(preparer.put(url, json={"priority": "HIGH"})) == (403, "permission_denied")
    assert waiter.put(url, json={"priority": "CRITICAL"}).status_code == 422
    changed = _ok(waiter.put(url, json={"priority": "HIGH", "reason": "Client pressé"}))
    assert changed["priority"] == "HIGH" and changed["version"] == order["version"] + 1
    same = _ok(waiter.put(url, json={"priority": "HIGH"}))
    assert same["version"] == changed["version"]  # rien ne change
    [event] = _events(r.owner, order["id"], "PRIORITY_CHANGED")
    assert (event["reason"], event["data"]) == (
        "Client pressé",
        {"previous": "NORMAL", "priority": "HIGH"},
    )
    assert _audit(r, "restaurant_order.priority_changed")[0]["data"]["priority"] == "HIGH"
    _ok(r.owner.post(f"/restaurant/orders/{order['id']}/cancel", json={"reason": "Erreur"}))
    assert _code(waiter.put(url, json={"priority": "URGENT"})) == (409, "order_cancelled")
    # Autre site sélectionné, autre entreprise : refus.
    selected = Api(r.owner.client, r.owner.token, uuid.UUID(r.b))
    other = _ok(_order(r.owner, r.a, [_line(r.dish.item)]), 201)
    assert _code(
        selected.put(
            f"/restaurant/kitchen/orders/{other['id']}/priority", json={"priority": "HIGH"}
        )
    ) == (403, "site_mismatch")


def test_manual_rerouting_rules_and_trace(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    other_site = _ok(_station(r, "Bar B", r.b), 201)["id"]
    order = _ok(_order(r.owner, r.a, [_line(r.drinks.unit), _line(r.dish.item)]), 201)
    lines = {li["station_id"]: li["id"] for li in _detail(r.owner, order["id"])["lines"]}
    url = f"/restaurant/kitchen/orders/{order['id']}/route"
    preparer = _member(r, "prep@rk-main.example.com", "preparer")
    waiter = _member(r, "serveur@rk-main.example.com", "waiter")
    body = {"line_ids": [lines[s.bar]], "station_id": s.grill, "reason": "Bar fermé"}
    assert _code(waiter.put(url, json=body)) == (403, "permission_denied")
    moved = _ok(preparer.put(url, json=body))
    assert [li["station_name"] for li in moved["lines"]] == ["Grill", "Grill"]
    [event] = _events(r.owner, order["id"], "LINES_ROUTED")
    assert event["reason"] == "Bar fermé"
    assert event["data"] == {
        "station_id": s.grill,
        "station_name": "Grill",
        "previous": {"1": "Bar"},
    }
    # Vers « Sans poste », puis contrôles : poste d'un autre site, poste désactivé, ligne finale.
    loose = _ok(preparer.put(url, json={"line_ids": [lines[s.bar]], "station_id": None}))
    assert loose["lines"][0]["station_id"] is None
    assert _code(
        preparer.put(url, json={"line_ids": [lines[s.bar]], "station_id": other_site})
    ) == (
        404,
        "station_not_found",
    )
    _ok(r.owner.post(f"{STATIONS}/{s.bar}/deactivate"))
    assert _code(preparer.put(url, json={"line_ids": [lines[s.bar]], "station_id": s.bar})) == (
        422,
        "station_inactive",
    )
    base = f"/restaurant/orders/{order['id']}"
    for action in ("start", "ready", "serve"):
        _ok(r.owner.post(f"{base}/{action}", json={"line_ids": [lines[s.grill]]}))
    final = preparer.put(url, json={"line_ids": [lines[s.grill]], "station_id": None})
    assert _code(final) == (409, "order_line_final")
    assert _audit(r, "restaurant_order.lines_routed")[0]["data"]["reason"] == "Bar fermé"


def test_board_rights_and_site_isolation(
    resto: SimpleNamespace, provision: Any, kclient: TestClient
) -> None:
    r = resto
    _stations_setup(r)
    _ok(_order(r.owner, r.a, [_line(r.dish.item)]), 201)
    custom = _ok(
        r.owner.post(
            "/roles",
            json={"name": "Écran seul", "permissions": ["restaurant.kitchen.station.view"]},
        ),
        201,
    )
    roles = {"roles": [{"role_id": custom["id"]}], "all_sites": True}
    _ok(
        r.owner.post(
            "/members",
            json={
                "email": "ecran@rk-main.example.com",
                "full_name": "Écran",
                "password": "Provisoire-123",
                **roles,
            },
        ),
        201,
    )
    temporary = _api(r.client, "ecran@rk-main.example.com", "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    screen_only = _api(r.client, "ecran@rk-main.example.com")
    # Sans la consultation des commandes : aucun ticket lisible.
    assert _code(screen_only.get(BOARD, params={"site_id": r.a})) == (403, "permission_denied")
    local = _member(r, "local@rk-main.example.com", "manager", all_sites=False, site_ids=[r.b])
    assert _code(local.get(BOARD, params={"site_id": r.a})) == (403, "site_access_denied")
    assert _board(local, r.b)["tickets"] == []
    provision("rk-other", profile=RESTAURANT, plan="ENTREPRISE")
    stranger = _api(kclient, "owner@rk-other.example.com")
    assert _code(stranger.get(BOARD, params={"site_id": r.a})) == (403, "site_access_denied")
    assert r.owner.get(BOARD).status_code == 422  # site obligatoire


# --- Désactivations protégées ---------------------------------------------------------------------


def test_station_and_module_deactivation_refused_with_open_lines(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    order = _ok(_order(r.owner, r.a, [_line(r.dish.item)]), 201)
    refused = r.owner.post(f"{STATIONS}/{s.grill}/deactivate")
    assert _code(refused) == (409, "station_has_open_lines")
    assert refused.json()["open_lines"] == 1
    module = set_site_module(r.owner, r.a, KITCHEN, False)
    assert _code(module) == (409, "module_has_open_operations")
    # Ligne annulée : le poste et le module se désactivent ; la configuration est conservée.
    _ok(r.owner.post(f"/restaurant/orders/{order['id']}/cancel", json={"reason": "Erreur"}))
    _ok(r.owner.post(f"{STATIONS}/{s.grill}/deactivate"))
    assert set_site_module(r.owner, r.a, KITCHEN, False).status_code == 204
    assert set_site_module(r.owner, r.a, KITCHEN, True).status_code == 204
    assert _ok(r.owner.get(STATIONS, params={"site_id": r.a}))["total"] == 2


# --- Concurrence ----------------------------------------------------------------------------------


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


def test_concurrent_preparers_and_deactivation_stay_consistent(resto: SimpleNamespace) -> None:
    r = resto
    s = _stations_setup(r)
    order = _ok(_order(r.owner, r.a, [_line(r.dish.item)]), 201)
    preparers = [_member(r, f"prep{i}@rk-main.example.com", "preparer") for i in range(3)]
    base = f"/restaurant/orders/{order['id']}/start"
    results = _race([lambda p=p: p.post(base) for p in preparers])
    assert sorted(res.status_code for res in results) == [200, 409, 409]
    assert len(_events(r.owner, order["id"], "PREP_STARTED")) == 1
    # Désactivation du poste contre création d'une commande routée vers lui : jamais une ligne
    # ouverte sur un poste désactivé.
    for _ in range(3):
        results = _race(
            [
                lambda: r.owner.post(f"{STATIONS}/{s.bar}/deactivate"),
                lambda: _order(r.owner, r.a, [_line(r.drinks.unit)]),
            ]
        )
        station = _ok(r.owner.get(f"{STATIONS}/{s.bar}"))
        board = _board(r.owner, r.a, station_id=s.bar)["tickets"]
        if not station["is_active"]:
            assert board == []
        _ok(r.owner.post(f"{STATIONS}/{s.bar}/activate"))
        for ticket in _board(r.owner, r.a, station_id=s.bar)["tickets"]:
            _ok(
                r.owner.post(
                    f"/restaurant/orders/{ticket['order_id']}/cancel", json={"reason": "Test"}
                )
            )


# --- Base : RLS, clés étrangères, droits SQL ----------------------------------------------------


def test_rls_composite_fks_and_sql_privileges(
    resto: SimpleNamespace, provision: Any, db: Session
) -> None:
    r = resto
    s = _stations_setup(r)
    other_site = _ok(_station(r, "Bar B", r.b), 201)["id"]
    order = _ok(_order(r.owner, r.a, [_line(r.dish.item)]), 201)
    other = provision("rk-rls", profile=RESTAURANT, plan="ENTREPRISE")
    tables = ("restaurant_prep_stations", "restaurant_station_routes")
    for table in tables:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    set_db_context(db, tenant_id=other.tenant_id)
    for table in tables:
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    with pytest.raises(DBAPIError):
        db.execute(
            text(
                "INSERT INTO restaurant_prep_stations (id, tenant_id, site_id, name, sort_order, "
                "is_active) VALUES (gen_random_uuid(), :t, :s, 'Pirate', 0, true)"
            ),
            {"t": r.tenant, "s": r.a},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("DELETE FROM restaurant_prep_stations"))
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    # FK composite : une ligne ne peut pas viser le poste d'un autre site.
    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE restaurant_order_lines SET station_id = :s WHERE order_id = :o"),
            {"s": other_site, "o": order["id"]},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    with pytest.raises(IntegrityError, match="uq_restaurant_prep_stations_site_name"):
        db.execute(
            text(
                "INSERT INTO restaurant_prep_stations (id, tenant_id, site_id, name, sort_order, "
                "is_active) VALUES (gen_random_uuid(), :t, :s, 'bar', 0, true)"
            ),
            {"t": r.tenant, "s": r.a},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    with pytest.raises(IntegrityError, match="ck_restaurant_orders_restaurant_order_priority"):
        db.execute(
            text("UPDATE restaurant_orders SET priority = 'CRITICAL' WHERE id = :o"),
            {"o": order["id"]},
        )
    db.rollback()
    assert s.grill


# --- Production : module planifié pendant le développement --------------------------------------


def test_production_registry_keeps_the_kitchen_planned(provision: Any, api_for: Any) -> None:
    manifest = get_registry().get(KITCHEN)
    assert manifest.status is ModuleStatus.PLANNED
    assert manifest.depends_on == ("restaurant.orders",)
    assert KITCHEN not in get_registry().get("restaurant.orders").depends_on
    t = provision("rk-prod", profile=RESTAURANT, plan="ENTREPRISE")
    owner = api_for("owner@rk-prod.example.com")
    assert owner.get(STATIONS).status_code == 404  # routes non montées
    assert _code(set_site_module(owner, t.site_id, KITCHEN, True)) == (
        422,
        "module_not_implemented",
    )
    caps = Api(owner.client, owner.token, t.site_id).get("/me/capabilities").json()
    assert not [p for p in caps["permissions"] if p.startswith("restaurant.kitchen.")]


def test_migration_0049_downgrade_refuses_to_lose_kitchen_data(
    resto: SimpleNamespace, owner_db: Session, owner_engine: Engine
) -> None:
    from alembic import command

    from tests.test_restaurant_menu_migration import _alembic

    _ok(_station(resto, "Bar"), 201)
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="cuisine seraient perdus"):
        command.downgrade(_alembic(), "0048")
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE restaurant_prep_stations NO FORCE ROW LEVEL SECURITY"))
        kept = conn.execute(text("SELECT count(*) FROM restaurant_prep_stations")).scalar_one()
        conn.execute(text("ALTER TABLE restaurant_prep_stations FORCE ROW LEVEL SECURITY"))
        assert kept == 1
