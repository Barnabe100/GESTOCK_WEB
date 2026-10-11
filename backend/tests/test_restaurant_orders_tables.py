"""Palier R5-B, sous-étape B-1 (ADR-0049 ; conception R5 dans ``docs/DOC_BACKLOG.md``) — table
facultative à la création d'une commande.

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
from tests.test_restaurant_orders import _api, _code, _line, _menu, _ok, _order

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
