"""Palier R5-B, sous-étape B-3 (ADR-0049 ; conception R5 dans ``docs/DOC_BACKLOG.md``) — lecture
enrichie des commandes et plan de salle.

- ``OrderOut.table`` : libellé et zone COURANTS de la table (B6, aucune copie A7), lus par le port
  des tables, même quand le module n'est plus effectif (B4) ; ``table_id`` aussi.
- Filtre ``table_id`` de la liste des commandes (B7).
- ``GET /restaurant/tables/board`` (AR-3) : une commande à confirmer ou ouverte occupe sa table
  (plusieurs possibles, A1) ; Occupée (au moins une « en cours ») > À régler (toutes ouvertes,
  non réglées, tout servi) > Libre ; annulée, refusée ou close : table libérée ; réglée non
  servie : occupée. Détail des commandes seulement avec ``restaurant.orders.order.view`` sur le
  site ; isolation entre entreprises et entre sites.

Registre de test : ``tclient`` (commandes et tables disponibles).
"""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, add_site, set_site_module
from tests.test_restaurant_orders import _api, _code, _line, _menu, _ok, _order
from tests.test_restaurant_orders_settlement import _cash, _receive, _serve_all, _settle

RESTAURANT = "restaurant.restaurant"
TABLES = "/restaurant/tables"
BOARD = "/restaurant/tables/board"
ORDERS = "/restaurant/orders"


@pytest.fixture
def salle(provision: Any, tclient: TestClient) -> SimpleNamespace:
    """Restaurant ENTREPRISE, deux sites ; site A : zones « Salle » (ordre 1) et « Terrasse »
    (ordre 0), tables T1 / T2 (Salle), T3 (Terrasse), T4 (sans zone), T5 (inactive) ; site B :
    une table. Stock de l'article du menu sur le site A."""
    t = provision("rtb-main", profile=RESTAURANT, plan="ENTREPRISE")
    owner = _api(tclient, "owner@rtb-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Annexe", "ANX", "restaurant"), 201)["id"]
    salle = _ok(
        owner.post(f"{TABLES}/zones", json={"site_id": a, "name": "Salle", "sort_order": 1}), 201
    )["id"]
    terrasse = _ok(
        owner.post(f"{TABLES}/zones", json={"site_id": a, "name": "Terrasse", "sort_order": 0}),
        201,
    )["id"]

    def table(label: str, site: str = a, **extra: Any) -> str:
        body = {"site_id": site, "label": label, **extra}
        return str(_ok(owner.post(TABLES, json=body), 201)["id"])

    r = SimpleNamespace(
        tenant=t.tenant_id,
        owner=owner,
        client=tclient,
        a=a,
        b=b,
        t1=table("T1", zone_id=salle, sort_order=1, seats=4),
        t2=table("T2", zone_id=salle, sort_order=2),
        t3=table("T3", zone_id=terrasse),
        t4=table("T4"),
        t5=table("T5"),
        tb=table("B1", b),
        menu=_menu(owner, a, "S"),
    )
    _ok(owner.post(f"{TABLES}/{r.t5}/deactivate"))
    supplier = _ok(owner.post("/suppliers", json={"name": "Brasserie"}), 201)["id"]
    _receive(SimpleNamespace(owner=owner, site=a, supplier=supplier), r.menu.article, "50")
    return r


def _board(api: Api, site: str | None = None) -> dict[str, Any]:
    return dict(_ok(api.get(BOARD, params={"site_id": site} if site else {})))


def _states(board: dict[str, Any]) -> dict[str, str]:
    return {t["label"]: t["state"] for t in board["tables"]}


def _seat(r: SimpleNamespace, table: str, quantity: str = "1") -> str:
    return str(_ok(_order(r.owner, r.a, [_line(r.menu.unit, quantity)], table_id=table), 201)["id"])


def _member_with(r: SimpleNamespace, email: str, permissions: list[str], **access: Any) -> Api:
    role = _ok(
        r.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions}), 201
    )["id"]
    roles = access.pop("roles", None) or [{"role_id": role}]
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{**x, "role_id": role} for x in roles],
        **({"all_sites": True} | access),
    }
    _ok(r.owner.post("/members", json=body), 201)
    temporary = _api(r.client, email, "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return _api(r.client, email)


# --- Lecture enrichie : OrderOut.table ---------------------------------------------------------


def test_order_out_carries_the_current_table(salle: SimpleNamespace) -> None:
    r = salle
    order = _seat(r, r.t1)
    detail = _ok(r.owner.get(f"{ORDERS}/{order}"))
    assert detail["table_id"] == r.t1
    assert detail["table"] == {"id": r.t1, "label": "T1", "zone_name": "Salle", "is_active": True}
    listed = _ok(r.owner.get(ORDERS, params={"limit": 50}))["items"]
    assert [o["table"]["label"] for o in listed if o["id"] == order] == ["T1"]
    # Libellé COURANT après renommage et changement de zone (B6) ; l'historique garde « T1 ».
    _ok(r.owner.put(f"{TABLES}/{r.t1}", json={"label": "T1-bis"}))
    detail = _ok(r.owner.get(f"{ORDERS}/{order}"))
    assert detail["table"] == {"id": r.t1, "label": "T1-bis", "zone_name": None, "is_active": True}
    events = _ok(r.owner.get(f"{ORDERS}/{order}/events"))
    assert events[0]["data"]["table_label"] == "T1"
    # Commande close : toujours le libellé courant.
    _ok(_settle(r.owner, order, payments=_cash("700")), 201)
    assert _serve_all(r.owner, order)["status"] == "CLOSED"
    _ok(r.owner.put(f"{TABLES}/{r.t1}", json={"label": "T1-ter"}))
    assert _ok(r.owner.get(f"{ORDERS}/{order}"))["table"]["label"] == "T1-ter"


def test_orders_without_table_are_unchanged(salle: SimpleNamespace) -> None:
    """Non-régression : commandes sans table dans les trois modes ; liste complète sans filtre."""
    r = salle
    ids = [
        _ok(_order(r.owner, r.a, [_line(r.menu.unit)], service_mode=mode), 201)["id"]
        for mode in ("ON_SITE", "COUNTER", "TAKEAWAY")
    ]
    seated = _seat(r, r.t2)
    for order in ids:
        detail = _ok(r.owner.get(f"{ORDERS}/{order}"))
        assert (detail["table_id"], detail["table"]) == (None, None)
    listed = {o["id"] for o in _ok(r.owner.get(ORDERS, params={"limit": 50}))["items"]}
    assert listed == {*ids, seated}
    # Le plan de salle les ignore.
    assert _states(_board(r.owner, r.a))["T2"] == "OCCUPIED"
    assert sum(t["order_count"] for t in _board(r.owner, r.a)["tables"]) == 1


def test_table_shown_even_when_the_module_is_not_effective(
    salle: SimpleNamespace, owner_db: Session
) -> None:
    """B4 : une commande garde sa table jusqu'à son retrait ; sa lecture ne dépend pas de
    l'effectivité du module des tables (état simulé en base, la désactivation par l'API étant
    refusée avec des commandes rattachées, A6)."""
    r = salle
    order = _seat(r, r.t3)
    owner_db.execute(
        text(
            "UPDATE site_modules SET enabled = false WHERE site_id = :s "
            "AND module_code = 'restaurant.tables'"
        ),
        {"s": r.a},
    )
    owner_db.commit()
    detail = _ok(r.owner.get(f"{ORDERS}/{order}"))
    assert detail["table"]["label"] == "T3" and detail["table"]["zone_name"] == "Terrasse"
    # Le plan de salle, lui, n'est plus accessible sur ce site.
    assert _code(r.owner.get(BOARD, params={"site_id": r.a})) == (403, "site_access_denied")


# --- Filtre table_id ---------------------------------------------------------------------------


def test_list_filter_by_table(salle: SimpleNamespace) -> None:
    r = salle
    first, second = _seat(r, r.t1), _seat(r, r.t1)
    other = _seat(r, r.t2)
    _ok(_order(r.owner, r.a, [_line(r.menu.unit)]), 201)
    on_t1 = _ok(r.owner.get(ORDERS, params={"table_id": r.t1, "limit": 50}))
    assert {o["id"] for o in on_t1["items"]} == {first, second} and on_t1["total"] == 2
    assert {o["id"] for o in _ok(r.owner.get(ORDERS, params={"table_id": r.t2}))["items"]} == {
        other
    }
    # Combiné à l'état : la commande close n'est plus « en cours » mais reste filtrable.
    _ok(_settle(r.owner, first, payments=_cash("700")), 201)
    _serve_all(r.owner, first)
    assert [o["id"] for o in _ok(r.owner.get(ORDERS, params={"table_id": r.t1}))["items"]] == [
        second
    ]
    closed = _ok(r.owner.get(ORDERS, params={"table_id": r.t1, "state": "closed"}))["items"]
    assert [o["id"] for o in closed] == [first]
    # Table inconnue, d'un autre site ou sans commande : liste vide, jamais une erreur.
    for table in (r.tb, r.t4, str(uuid.uuid4())):
        assert _ok(r.owner.get(ORDERS, params={"table_id": table}))["total"] == 0


# --- Plan de salle : états ---------------------------------------------------------------------


def test_board_lists_active_tables_in_floor_order(salle: SimpleNamespace) -> None:
    r = salle
    board = _board(r.owner, r.a)
    assert board["site_id"] == r.a and board["orders_visible"] is True
    # Zones dans leur ordre (Terrasse 0, Salle 1), sans zone en dernier ; T5 inactive absente.
    assert [t["label"] for t in board["tables"]] == ["T3", "T1", "T2", "T4"]
    t1 = board["tables"][1]
    assert (t1["zone_name"], t1["seats"], t1["state"], t1["order_count"]) == ("Salle", 4, "FREE", 0)
    assert (t1["occupied_since"], t1["orders"]) == (None, [])
    assert board["counts"] == {"free": 4, "occupied": 0, "to_settle": 0}
    # Site sélectionné : même résultat sans paramètre ; site B : sa seule table.
    assert _board(Api(r.owner.client, r.owner.token, uuid.UUID(r.a)))["tables"] == board["tables"]
    assert [t["label"] for t in _board(r.owner, r.b)["tables"]] == ["B1"]


def test_board_states_and_priority(salle: SimpleNamespace, owner_db: Session) -> None:
    r = salle
    # En cours (à préparer) : Occupée.
    preparing = _seat(r, r.t1)
    # Tout servi, non réglé : À régler.
    served = _seat(r, r.t2)
    _serve_all(r.owner, served)
    # Réglée non servie : Occupée (elle reste ouverte).
    settled = _seat(r, r.t3)
    _ok(_settle(r.owner, settled, payments=_cash("700")), 201)
    board = _board(r.owner, r.a)
    assert _states(board) == {"T3": "OCCUPIED", "T1": "OCCUPIED", "T2": "TO_SETTLE", "T4": "FREE"}
    assert board["counts"] == {"free": 1, "occupied": 2, "to_settle": 1}
    t2 = next(t for t in board["tables"] if t["label"] == "T2")
    [only] = t2["orders"]
    assert (only["id"], only["to_settle"], only["prep_status"]) == (served, True, "SERVED")
    t3 = next(t for t in board["tables"] if t["label"] == "T3")
    assert t3["orders"][0]["settlement_status"] == "SETTLED"
    assert t3["orders"][0]["to_settle"] is False
    # Priorité : une commande « en cours » de plus sur T2 la rend Occupée (A1 : plusieurs).
    extra = _seat(r, r.t2)
    t2 = next(t for t in _board(r.owner, r.a)["tables"] if t["label"] == "T2")
    assert (t2["state"], t2["order_count"]) == ("OCCUPIED", 2)
    assert [o["id"] for o in t2["orders"]] == [served, extra]
    assert t2["occupied_since"] == t2["orders"][0]["created_at"]
    # Toutes servies et non réglées : À régler.
    _serve_all(r.owner, extra)
    assert _states(_board(r.owner, r.a))["T2"] == "TO_SETTLE"
    # Fin de vie : close (réglée + servie) et annulée libèrent la table.
    _serve_all(r.owner, settled)
    _ok(r.owner.post(f"{ORDERS}/{preparing}/cancel", json={"reason": "Parti"}))
    assert _states(_board(r.owner, r.a)) == {
        "T3": "FREE",
        "T1": "FREE",
        "T2": "TO_SETTLE",
        "T4": "FREE",
    }
    # Règlement des deux commandes de T2 : closes, la table se libère.
    for order in (served, extra):
        _ok(_settle(r.owner, order, payments=_cash("700")), 201)
    assert _states(_board(r.owner, r.a))["T2"] == "FREE"


def test_board_pending_confirmation_rejected_and_empty_orders(
    salle: SimpleNamespace, owner_db: Session
) -> None:
    """Commande à confirmer (QR, R7 : état simulé en base) : occupe sa table ; refusée : la
    libère. Commande ouverte sans ligne active (toutes annulées) : « en cours »."""
    r = salle
    pending = _seat(r, r.t1)
    rejected = _seat(r, r.t2)
    owner_db.execute(
        text("UPDATE restaurant_orders SET status = 'PENDING_CONFIRMATION' WHERE id = :o"),
        {"o": pending},
    )
    owner_db.execute(
        text(
            "UPDATE restaurant_orders SET status = 'REJECTED', cancel_reason = 'Refusée', "
            "cancelled_at = now(), cancelled_by = created_by WHERE id = :o"
        ),
        {"o": rejected},
    )
    owner_db.commit()
    empty = _seat(r, r.t3)
    lines = [line["id"] for line in _ok(r.owner.get(f"{ORDERS}/{empty}"))["lines"]]
    cancelled = _ok(
        r.owner.post(f"{ORDERS}/{empty}/cancel-lines", json={"line_ids": lines, "reason": "Erreur"})
    )
    assert (cancelled["status"], cancelled["prep_status"]) == ("OPEN", "NONE")
    board = _board(r.owner, r.a)
    assert _states(board) == {"T3": "OCCUPIED", "T1": "OCCUPIED", "T2": "FREE", "T4": "FREE"}
    t1 = next(t for t in board["tables"] if t["label"] == "T1")
    assert t1["orders"][0]["status"] == "PENDING_CONFIRMATION"


def test_inactive_table_still_occupied_is_listed(salle: SimpleNamespace, owner_db: Session) -> None:
    """Défense : une table inactive encore occupée (impossible par l'API, A6 et contrôles de
    rattachement ; état simulé en base) reste visible, pour qu'aucune commande ne disparaisse
    du plan."""
    r = salle
    order = _seat(r, r.t4)
    owner_db.execute(
        text("UPDATE restaurant_tables SET is_active = false WHERE id = :t"), {"t": r.t4}
    )
    owner_db.commit()
    t4 = next(t for t in _board(r.owner, r.a)["tables"] if t["label"] == "T4")
    assert (t4["is_active"], t4["state"], t4["orders"][0]["id"]) == (False, "OCCUPIED", order)


# --- Plan de salle : droits et isolation --------------------------------------------------------


def test_order_detail_needs_order_view(salle: SimpleNamespace) -> None:
    r = salle
    order = _seat(r, r.t1)
    tables_only = _member_with(r, "plan@rtb-main.example.com", ["restaurant.tables.table.view"])
    board = _board(tables_only, r.a)
    t1 = next(t for t in board["tables"] if t["label"] == "T1")
    assert board["orders_visible"] is False
    assert (t1["state"], t1["order_count"], t1["orders"]) == ("OCCUPIED", 1, None)
    assert t1["occupied_since"] is not None
    assert all(t["orders"] is None for t in board["tables"])
    assert _code(tables_only.get(f"{ORDERS}/{order}")) == (403, "permission_denied")
    # Lecture des tables partout, lecture des commandes sur le site A seulement : détail sur A,
    # pas sur B (permission résolue pour le site du plan).
    tables_role = _ok(
        r.owner.post(
            "/roles", json={"name": "Plan", "permissions": ["restaurant.tables.table.view"]}
        ),
        201,
    )["id"]
    orders_role = _ok(
        r.owner.post(
            "/roles", json={"name": "Commandes A", "permissions": ["restaurant.orders.order.view"]}
        ),
        201,
    )["id"]
    _ok(
        r.owner.post(
            "/members",
            json={
                "email": "split@rtb-main.example.com",
                "full_name": "Split",
                "password": "Provisoire-123",
                "roles": [{"role_id": tables_role}, {"role_id": orders_role, "site_id": r.a}],
                "all_sites": True,
            },
        ),
        201,
    )
    _api(r.client, "split@rtb-main.example.com", "Provisoire-123").post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    split = _api(r.client, "split@rtb-main.example.com")
    on_a = _board(split, r.a)
    assert on_a["orders_visible"] is True
    assert next(t for t in on_a["tables"] if t["label"] == "T1")["orders"][0]["id"] == order
    assert _board(split, r.b)["orders_visible"] is False
    # Sans la lecture des tables : refusé, même avec la lecture des commandes.
    orders_only = _member_with(r, "orders@rtb-main.example.com", ["restaurant.orders.order.view"])
    assert _code(orders_only.get(BOARD, params={"site_id": r.a})) == (403, "permission_denied")


def test_board_site_scope(salle: SimpleNamespace) -> None:
    r = salle
    assert _code(r.owner.get(BOARD)) == (422, "site_required")
    on_a = Api(r.owner.client, r.owner.token, uuid.UUID(r.a))
    assert _code(on_a.get(BOARD, params={"site_id": r.b})) == (403, "site_mismatch")
    assert _code(r.owner.get(BOARD, params={"site_id": str(uuid.uuid4())})) == (
        403,
        "site_access_denied",
    )
    limited = _member_with(
        r,
        "b@rtb-main.example.com",
        ["restaurant.tables.table.view", "restaurant.orders.order.view"],
        all_sites=False,
        site_ids=[r.b],
    )
    assert _code(limited.get(BOARD, params={"site_id": r.a})) == (403, "site_access_denied")
    assert [t["label"] for t in _board(limited, r.b)["tables"]] == ["B1"]
    # Module des tables désactivé sur le site B (aucune commande rattachée) : site sélectionné
    # → module indisponible ; sans sélection → site non lisible.
    assert set_site_module(r.owner, r.b, "restaurant.tables", False).status_code == 204
    on_b = Api(r.owner.client, r.owner.token, uuid.UUID(r.b))
    assert _code(on_b.get(BOARD)) == (403, "module_unavailable")
    assert _code(r.owner.get(BOARD, params={"site_id": r.b})) == (403, "site_access_denied")


def test_board_is_isolated_between_companies(
    salle: SimpleNamespace, provision: Any, tclient: TestClient
) -> None:
    r = salle
    _seat(r, r.t1)
    provision("rtb-other", profile=RESTAURANT, plan="ENTREPRISE")
    other = _api(tclient, "owner@rtb-other.example.com")
    other_site = str(other.get("/sites").json()[0]["id"])
    _ok(other.post(TABLES, json={"site_id": other_site, "label": "X1"}), 201)
    # L'autre entreprise ne voit que ses tables ; le site A lui est inconnu.
    assert [t["label"] for t in _board(other, other_site)["tables"]] == ["X1"]
    assert _states(_board(other, other_site)) == {"X1": "FREE"}
    assert _code(other.get(BOARD, params={"site_id": r.a})) == (403, "site_access_denied")
    assert _ok(other.get(ORDERS, params={"table_id": r.t1}))["total"] == 0
    # Et inversement.
    assert _code(r.owner.get(BOARD, params={"site_id": other_site})) == (403, "site_access_denied")


def test_board_is_not_exposed_in_production(provision: Any, api_for: Any) -> None:
    """Registre de production : tables (et commandes) planifiées, route non montée."""
    provision("rtb-prod", profile=RESTAURANT, plan="ENTREPRISE")
    owner = api_for("owner@rtb-prod.example.com")
    assert owner.get(BOARD).status_code == 404
