"""Palier R5-B, sous-étapes B-1 et B-2 (ADR-0049 ; conception R5 dans ``docs/DOC_BACKLOG.md``) —
table facultative à la création d'une commande (B-1), changement et retrait de la table d'une
commande non finale (B-2, ``PUT /restaurant/orders/{id}/table``).

Une commande n'exige jamais de table (D2) ; une table ne s'associe qu'à une commande « sur
place » (A3, B5), du MÊME site, active, et seulement si ``restaurant.tables`` est EFFECTIF sur le
site (recalculé après les verrous ; module planifié : jamais). Plusieurs commandes peuvent
partager une table (A1). La table est tracée dans l'évènement ``CREATED`` et l'audit, sans copie
du libellé dans la commande (A7). Les commandes n'importent jamais le module des tables (port).

Registre de test : ``tclient`` (commandes et tables disponibles) ; ``oclient`` (commandes
disponibles, tables PLANIFIÉES comme en production).
"""

import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import add_site, set_site_module
from tests.test_restaurant_orders import _api, _code, _line, _member, _menu, _ok, _order
from tests.test_restaurant_orders_settlement import _cash, _receive, _serve_all, _settle

RESTAURANT = "restaurant.restaurant"
TABLES = "/restaurant/tables"


@pytest.fixture
def resto(provision: Any, tclient: TestClient) -> SimpleNamespace:
    """Restaurant ENTREPRISE (tables activées par défaut, D11), deux sites ; une zone et deux
    tables sur le site A, une table sur le site B."""
    t = provision("rot-main", profile=RESTAURANT, plan="ENTREPRISE")
    owner = _api(tclient, "owner@rot-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER", "restaurant"), 201)["id"]
    zone = _ok(owner.post(f"{TABLES}/zones", json={"site_id": a, "name": "Salle"}), 201)["id"]
    t1 = _ok(owner.post(TABLES, json={"site_id": a, "label": "T1", "zone_id": zone}), 201)["id"]
    t2 = _ok(owner.post(TABLES, json={"site_id": a, "label": "T2"}), 201)["id"]
    tb = _ok(owner.post(TABLES, json={"site_id": b, "label": "B1"}), 201)["id"]
    return SimpleNamespace(
        tenant=t.tenant_id,
        owner=owner,
        client=tclient,
        a=a,
        b=b,
        menu=_menu(owner, a, "T"),
        t1=t1,
        t2=t2,
        tb=tb,
    )


def _table_of(owner_db: Session, order: str) -> str | None:
    owner_db.expire_all()
    value = owner_db.execute(
        text("SELECT table_id FROM restaurant_orders WHERE id = :o"), {"o": order}
    ).scalar_one()
    return None if value is None else str(value)


def _created(r: SimpleNamespace, order: str) -> dict[str, Any]:
    events = _ok(r.owner.get(f"/restaurant/orders/{order}/events"))
    created = [e for e in events if e["event_type"] == "CREATED"]
    assert len(created) == 1
    return dict(created[0]["data"])


def _orders_count(owner_db: Session, site: str) -> int:
    owner_db.expire_all()
    return int(
        owner_db.execute(
            text("SELECT count(*) FROM restaurant_orders WHERE site_id = :s"), {"s": site}
        ).scalar_one()
    )


def test_orders_without_table_are_unchanged_in_every_mode(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    for mode in ("ON_SITE", "COUNTER", "TAKEAWAY"):
        order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], service_mode=mode), 201)["id"]
        assert _table_of(owner_db, order) is None
        assert _created(r, order) == {"channel": "STAFF"}


def test_order_on_site_with_a_table_is_traced(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    assert _table_of(owner_db, order) == r.t1
    assert _created(r, order) == {
        "channel": "STAFF",
        "table_id": r.t1,
        "table_label": "T1",
        "zone_name": "Salle",
    }
    audit = _ok(
        r.owner.get("/audit-logs", params={"action": "restaurant_order.created", "limit": 5})
    )["items"][0]
    assert audit["entity_id"] == order
    assert audit["data"]["table"] == {"table_id": r.t1, "table_label": "T1", "zone_name": "Salle"}
    # Table sans zone.
    other = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t2), 201)["id"]
    assert _created(r, other)["zone_name"] is None


def test_a_table_only_goes_with_an_on_site_order(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    before = _orders_count(owner_db, r.a)
    for mode in ("COUNTER", "TAKEAWAY"):
        refused = _order(r.owner, r.a, [_line(r.menu.unit)], service_mode=mode, table_id=r.t1)
        assert _code(refused) == (422, "table_not_allowed_for_mode")
    assert _orders_count(owner_db, r.a) == before


def test_the_table_must_be_of_the_site_and_active(
    resto: SimpleNamespace, provision: Any, owner_db: Session
) -> None:
    r = resto
    before = _orders_count(owner_db, r.a)
    assert _code(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.tb)) == (
        404,
        "table_not_found",
    )
    assert _code(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=str(uuid.uuid4()))) == (
        404,
        "table_not_found",
    )
    # Table d'une autre entreprise : introuvable, jamais révélée.
    provision("rot-other", profile=RESTAURANT, plan="ENTREPRISE")
    other = _api(r.client, "owner@rot-other.example.com")
    other_site = str(other.get("/sites").json()[0]["id"])
    foreign = _ok(other.post(TABLES, json={"site_id": other_site, "label": "X1"}), 201)["id"]
    assert _code(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=foreign)) == (
        404,
        "table_not_found",
    )
    _ok(r.owner.post(f"{TABLES}/{r.t2}/deactivate"))
    assert _code(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t2)) == (
        422,
        "table_inactive",
    )
    assert _orders_count(owner_db, r.a) == before


def test_tables_unavailable_when_the_module_is_not_effective_on_the_site(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    assert set_site_module(r.owner, r.a, "restaurant.tables", False).status_code == 204
    refused = _order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1)
    assert _code(refused) == (422, "tables_unavailable")
    # Sans table : la commande reste possible (D2).
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)]), 201)["id"]
    assert _table_of(owner_db, order) is None


def test_planned_tables_module_is_never_effective(
    provision: Any, oclient: TestClient, owner_db: Session
) -> None:
    """Registre où seules les commandes sont disponibles (tables PLANIFIÉES, comme en
    production) : l'activation inerte du profil Restaurant (E.1) ne rend jamais une table
    rattachable."""
    t = provision("rot-planned", profile=RESTAURANT, plan="ENTREPRISE")
    owner = _api(oclient, "owner@rot-planned.example.com")
    site = str(t.site_id)
    assert (
        owner_db.execute(
            text(
                "SELECT enabled FROM site_modules WHERE site_id = :s "
                "AND module_code = 'restaurant.tables'"
            ),
            {"s": site},
        ).scalar_one()
        is True
    )
    table = uuid.uuid4()
    owner_db.execute(
        text(
            "INSERT INTO restaurant_tables (id, tenant_id, site_id, label, sort_order, is_active) "
            "VALUES (:id, :t, :s, 'T1', 0, true)"
        ),
        {"id": table, "t": t.tenant_id, "s": site},
    )
    owner_db.commit()
    menu = _menu(owner, site, "P")
    refused = _order(owner, site, [_line(menu.unit)], table_id=str(table))
    assert _code(refused) == (422, "tables_unavailable")
    _ok(_order(owner, site, [_line(menu.unit)]), 201)


def test_idempotency_and_several_orders_on_one_table(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    key = str(uuid.uuid4())
    first = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1, idempotency_key=key), 201)
    replay = r.owner.post(
        "/restaurant/orders",
        json={
            "site_id": r.a,
            "service_mode": "ON_SITE",
            "lines": [_line(r.menu.unit)],
            "table_id": r.t1,
            "idempotency_key": key,
        },
    )
    assert replay.status_code == 200 and replay.json()["id"] == first["id"]
    assert (
        owner_db.execute(
            text("SELECT count(*) FROM restaurant_orders WHERE idempotency_key = :k"), {"k": key}
        ).scalar_one()
        == 1
    )
    # A1 : plusieurs commandes ouvertes sur la même table.
    second = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    assert _table_of(owner_db, second) == r.t1
    assert _table_of(owner_db, first["id"]) == r.t1


def test_orders_never_import_the_tables_module() -> None:
    """Indépendance (D1, A2) : les commandes déclarent un port ; elles n'importent jamais le
    paquet des tables, et ne dépendent pas du module dans le registre."""
    from app.platform.registry import get_registry

    package = Path(__file__).resolve().parents[1] / "app" / "modules" / "restaurant_orders"
    importing = re.compile(r"^\s*(from|import)\s+app\.modules\.restaurant_tables\b", re.M)
    offenders = [
        path.name for path in package.glob("*.py") if importing.search(path.read_text("utf-8"))
    ]
    assert offenders == []
    assert "restaurant.tables" not in get_registry().get("restaurant.orders").depends_on


# --- B-2 : changement et retrait de la table ----------------------------------------------------


def _set_table(api: Any, order: str, table: str | None, reason: str | None = None) -> Any:
    body: dict[str, Any] = {"table_id": table}
    if reason is not None:
        body["reason"] = reason
    return api.put(f"/restaurant/orders/{order}/table", json=body)


def _table_events(r: SimpleNamespace, order: str) -> list[dict[str, Any]]:
    events = _ok(r.owner.get(f"/restaurant/orders/{order}/events"))
    return [e for e in events if e["event_type"] == "TABLE_CHANGED"]


def _version(r: SimpleNamespace, order: str) -> int:
    return int(_ok(r.owner.get(f"/restaurant/orders/{order}"))["version"])


def test_associate_change_and_remove_the_table(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)]), 201)
    # Association (commande sans table) : motif facultatif (A5).
    _ok(_set_table(r.owner, order["id"], r.t1))
    assert _table_of(owner_db, order["id"]) == r.t1
    assert _version(r, order["id"]) == order["version"] + 1
    [associated] = _table_events(r, order["id"])
    assert associated["reason"] is None
    assert associated["data"] == {
        "table_id": r.t1,
        "table_label": "T1",
        "zone_name": "Salle",
        "previous_table_id": None,
        "previous_table_label": None,
        "previous_zone_name": None,
    }
    # Changement motivé : ancienne table tracée (libellé et zone).
    _ok(_set_table(r.owner, order["id"], r.t2, "Client déplacé"))
    assert _table_of(owner_db, order["id"]) == r.t2
    changed = _table_events(r, order["id"])[-1]
    assert changed["reason"] == "Client déplacé"
    assert changed["data"] == {
        "table_id": r.t2,
        "table_label": "T2",
        "zone_name": None,
        "previous_table_id": r.t1,
        "previous_table_label": "T1",
        "previous_zone_name": "Salle",
    }
    # Retrait (``null`` explicite).
    _ok(_set_table(r.owner, order["id"], None))
    assert _table_of(owner_db, order["id"]) is None
    removed = _table_events(r, order["id"])[-1]
    assert removed["data"] == {
        "table_id": None,
        "table_label": None,
        "zone_name": None,
        "previous_table_id": r.t2,
        "previous_table_label": "T2",
        "previous_zone_name": None,
    }
    assert len(_table_events(r, order["id"])) == 3
    audits = _ok(
        r.owner.get("/audit-logs", params={"action": "restaurant_order.table_changed", "limit": 10})
    )["items"]
    assert [a["data"]["table_id"] for a in audits] == [None, r.t2, r.t1]
    assert all(a["entity_id"] == order["id"] for a in audits)
    assert audits[1]["data"]["reason"] == "Client déplacé"
    assert audits[1]["data"]["previous_table_label"] == "T1"
    # Le champ ``table_id`` est obligatoire (``null`` pour retirer) : jamais deviné.
    missing = r.owner.put(f"/restaurant/orders/{order['id']}/table", json={})
    assert missing.status_code == 422


def test_same_table_writes_nothing(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)
    assert _ok(_set_table(r.owner, order["id"], r.t1, "Sans effet"))["version"] == order["version"]
    bare = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], service_mode="COUNTER"), 201)
    assert _ok(_set_table(r.owner, bare["id"], None))["version"] == bare["version"]
    assert _table_events(r, order["id"]) == [] and _table_events(r, bare["id"]) == []
    audits = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_order.table_changed"}))
    assert audits["items"] == []
    assert _table_of(owner_db, order["id"]) == r.t1


def test_table_change_checks_mode_site_and_table(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    for mode in ("COUNTER", "TAKEAWAY"):
        order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], service_mode=mode), 201)["id"]
        assert _code(_set_table(r.owner, order, r.t1)) == (422, "table_not_allowed_for_mode")
        assert _table_of(owner_db, order) is None
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    assert _code(_set_table(r.owner, order, r.tb)) == (404, "table_not_found")
    assert _code(_set_table(r.owner, order, str(uuid.uuid4()))) == (404, "table_not_found")
    _ok(r.owner.post(f"{TABLES}/{r.t2}/deactivate"))
    assert _code(_set_table(r.owner, order, r.t2)) == (422, "table_inactive")
    assert (
        _code(r.owner.put(f"/restaurant/orders/{uuid.uuid4()}/table", json={"table_id": None}))[0]
        == 404
    )
    assert _table_of(owner_db, order) == r.t1
    assert _table_events(r, order) == []


def test_final_orders_are_refused(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    cancelled = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    _ok(r.owner.post(f"/restaurant/orders/{cancelled}/cancel", json={"reason": "Parti"}))
    assert _code(_set_table(r.owner, cancelled, r.t2)) == (409, "order_cancelled")
    assert _code(_set_table(r.owner, cancelled, None)) == (409, "order_cancelled")
    assert _table_of(owner_db, cancelled) == r.t1
    # Réglée mais non servie : commande encore ouverte, la table peut changer ; close : refus.
    supplier = _ok(r.owner.post("/suppliers", json={"name": "Brasserie"}), 201)["id"]
    _receive(SimpleNamespace(owner=r.owner, site=r.a, supplier=supplier), r.menu.article, "10")
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    _ok(_settle(r.owner, order, payments=_cash("700")), 201)
    _ok(_set_table(r.owner, order, r.t2))
    assert _serve_all(r.owner, order)["status"] == "CLOSED"
    assert _code(_set_table(r.owner, order, r.t1)) == (409, "order_closed")
    assert _code(_set_table(r.owner, order, None)) == (409, "order_closed")
    assert _table_of(owner_db, order) == r.t2


def test_association_needs_the_tables_module_but_removal_does_not(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    """B4 : le retrait reste permis quand ``restaurant.tables`` n'est plus effectif. La
    désactivation par l'API est refusée tant qu'une commande en cours porte une table (A6) et
    tous les profils de restauration proposent les tables : l'état est donc simulé en base
    (activation du site à ``false``), comme pourrait le produire un changement d'offre."""
    r = resto
    kept = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    bare = _ok(_order(r.owner, r.a, [_line(r.menu.unit)]), 201)["id"]
    assert _code(set_site_module(r.owner, r.a, "restaurant.tables", False)) == (
        409,
        "module_has_open_operations",
    )
    owner_db.execute(
        text(
            "UPDATE site_modules SET enabled = false WHERE site_id = :s "
            "AND module_code = 'restaurant.tables'"
        ),
        {"s": r.a},
    )
    owner_db.commit()
    modules = {m["code"] for m in _ok(r.owner.get(f"/sites/{r.a}/modules")) if m["effective"]}
    assert "restaurant.tables" not in modules and "restaurant.orders" in modules
    assert _code(_set_table(r.owner, bare, r.t1)) == (422, "tables_unavailable")
    assert _code(_set_table(r.owner, kept, r.t2)) == (422, "tables_unavailable")
    assert _table_of(owner_db, kept) == r.t1
    _ok(_set_table(r.owner, kept, None, "Tables retirées"))
    assert _table_of(owner_db, kept) is None
    [removed] = _table_events(r, kept)
    assert removed["reason"] == "Tables retirées"
    assert (removed["data"]["previous_table_label"], removed["data"]["table_id"]) == ("T1", None)


def test_removal_with_a_planned_tables_module(
    provision: Any, oclient: TestClient, owner_db: Session
) -> None:
    """Registre de production pour les tables (PLANIFIÉES) : une table rattachée (données
    simulées) se retire ; aucune ne s'associe."""
    t = provision("rot-plan2", profile=RESTAURANT, plan="ENTREPRISE")
    owner = _api(oclient, "owner@rot-plan2.example.com")
    site = str(t.site_id)
    table = uuid.uuid4()
    owner_db.execute(
        text(
            "INSERT INTO restaurant_tables (id, tenant_id, site_id, label, sort_order, is_active) "
            "VALUES (:id, :t, :s, 'P1', 0, true)"
        ),
        {"id": table, "t": t.tenant_id, "s": site},
    )
    owner_db.commit()
    menu = _menu(owner, site, "Q")
    order = _ok(_order(owner, site, [_line(menu.unit)]), 201)["id"]
    assert _code(_set_table(owner, order, str(table))) == (422, "tables_unavailable")
    owner_db.execute(
        text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"), {"t": table, "o": order}
    )
    owner_db.commit()
    _ok(_set_table(owner, order, None))
    assert _table_of(owner_db, order) is None
    events = _ok(owner.get(f"/restaurant/orders/{order}/events"))
    [removed] = [e for e in events if e["event_type"] == "TABLE_CHANGED"]
    assert removed["data"]["previous_table_label"] == "P1"


def test_table_change_permission_and_site_scope(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    viewer = _member(r, "viewer@rot-main.example.com", "viewer")
    assert _ok(viewer.get(f"/restaurant/orders/{order}"))["id"] == order
    assert _code(_set_table(viewer, order, r.t2)) == (403, "permission_denied")
    assert _code(_set_table(viewer, order, None)) == (403, "permission_denied")
    # Vendeur limité au site B : la commande du site A lui reste inaccessible.
    other = _member(r, "b@rot-main.example.com", "seller", all_sites=False, site_ids=[r.b])
    assert _code(_set_table(other, order, r.t2)) == (404, "order_not_found")
    seller = _member(r, "a@rot-main.example.com", "seller")
    _ok(_set_table(seller, order, r.t2))
    assert _table_of(owner_db, order) == r.t2


def test_history_keeps_the_labels_after_a_rename(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _ok(_order(r.owner, r.a, [_line(r.menu.unit)], table_id=r.t1), 201)["id"]
    _ok(_set_table(r.owner, order, r.t2))
    _ok(r.owner.put(f"{TABLES}/{r.t1}", json={"label": "T1-bis"}))
    _ok(r.owner.put(f"{TABLES}/{r.t2}", json={"label": "T2-bis"}))
    [changed] = _table_events(r, order)
    assert (changed["data"]["previous_table_label"], changed["data"]["table_label"]) == (
        "T1",
        "T2",
    )
    assert (changed["data"]["previous_zone_name"], changed["data"]["zone_name"]) == ("Salle", None)
    assert _created(r, order)["table_label"] == "T1"
