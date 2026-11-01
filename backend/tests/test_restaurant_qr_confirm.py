"""Palier R7-D (ADR-0052) — confirmation ou refus des commandes QR par le personnel : permission
``restaurant.orders.order.confirm``, refus motivé, rien avant la confirmation (prise en charge,
préparation, cuisine, règlement), notification des membres habilités, filtres, concurrence."""

import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.restaurant_orders.permissions import ORDER_CONFIRM
from app.platform.registry import AccessKind, get_registry
from tests.conftest import Api, add_site, set_site_module
from tests.test_restaurant_kitchen import _member
from tests.test_restaurant_orders import _api, _code, _menu, _ok
from tests.test_restaurant_qr_orders import _body, _send
from tests.test_restaurant_qr_points import POINTS, QR, _token

ORDERS = "/restaurant/orders"


@pytest.fixture
def resto(provision: Any, qrclient: TestClient) -> SimpleNamespace:
    t = provision("rqc-main", profile="restaurant.restaurant", plan="ENTREPRISE")
    owner = _api(qrclient, "owner@rqc-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER", "restaurant"), 201)["id"]
    for site in (a, b):
        assert set_site_module(owner, site, QR, True).status_code == 204
    menu = _menu(owner, a, "CONF")
    counter = _ok(
        owner.post(POINTS, json={"site_id": a, "kind": "COUNTER", "label": "Comptoir"}), 201
    )
    r = SimpleNamespace(
        tenant=t.tenant_id,
        owner=owner,
        client=qrclient,
        a=a,
        b=b,
        menu=menu,
        counter=_token(counter),
    )
    r.seller = _member(r, "seller@rqc-main.example.com", "seller")
    r.waiter = _member(r, "waiter@rqc-main.example.com", "waiter")
    r.preparer = _member(r, "preparer@rqc-main.example.com", "preparer")
    r.viewer = _member(r, "viewer@rqc-main.example.com", "viewer")
    r.seller_b = _member(r, "sellerb@rqc-main.example.com", "seller", all_sites=False, site_ids=[b])
    return r


def _qr_order(r: SimpleNamespace, quantity: int = 1) -> dict[str, Any]:
    _ok(_send(r.client, r.counter, _body((r.menu.unit, quantity))), 201)
    pending = _ok(r.owner.get(ORDERS, params={"state": "pending", "sort": "-created_at"}))
    return dict(pending["items"][0])


def _notifications(api: Api) -> list[dict[str, Any]]:
    items = _ok(api.get("/member-notifications", params={"limit": 50}))["items"]
    return [n for n in items if n["kind"] == "restaurant.order.qr_received"]


def test_confirm_permission_is_declared_and_granted_by_roles() -> None:
    registry = get_registry()
    definition = registry.permission(ORDER_CONFIRM)
    assert definition is not None and definition.access is AccessKind.WRITE


def test_nothing_happens_before_confirmation(resto: SimpleNamespace) -> None:
    r = resto
    order = _qr_order(r)
    base = f"{ORDERS}/{order['id']}"
    for path, body in (
        (f"{base}/claim", None),
        (f"{base}/start", None),
        (f"{base}/lines", {"lines": [{"menu_item_id": r.menu.unit, "quantity": "1"}]}),
        (
            f"{base}/settle",
            {"idempotency_key": str(uuid.uuid4()), "payments": []},
        ),
    ):
        response = r.owner.post(path, json=body) if body is not None else r.owner.post(path)
        assert _code(response) == (409, "order_pending_confirmation"), path
    # Listes : « à confirmer » à part ; les colonnes de suivi n'affichent que les ouvertes.
    assert [o["id"] for o in _ok(r.owner.get(ORDERS, params={"state": "pending"}))["items"]] == [
        order["id"]
    ]
    assert _ok(r.owner.get(ORDERS, params={"state": "open"}))["total"] == 0
    # Cuisine : aucune commande à confirmer n'y arrive.
    board = _ok(r.owner.get("/restaurant/kitchen/board", params={"site_id": r.a}))
    assert order["id"] not in str(board)


def test_confirmation_opens_the_regular_circuit(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    order = _qr_order(r)
    base = f"{ORDERS}/{order['id']}"
    confirmed = _ok(r.seller.post(f"{base}/confirm"))
    assert confirmed["status"] == "OPEN"
    assert _code(r.seller.post(f"{base}/confirm")) == (409, "order_not_pending")
    assert _code(r.seller.post(f"{base}/reject", json={"reason": "x"})) == (
        409,
        "order_not_pending",
    )
    row = owner_db.execute(
        text("SELECT confirmed_at, confirmed_by FROM restaurant_orders WHERE id = :id"),
        {"id": order["id"]},
    ).one()
    assert row[0] is not None and row[1] is not None
    events = [e["event_type"] for e in _ok(r.owner.get(f"{base}/events"))]
    assert events[:2] == ["CREATED", "CONFIRMED"]
    audit = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_order.confirmed"}))
    assert audit["total"] == 1
    # Circuit habituel : prise en charge, cuisine, préparation.
    assert _ok(r.waiter.post(f"{base}/claim"))["assigned_name"]
    board = _ok(r.owner.get("/restaurant/kitchen/board", params={"site_id": r.a}))
    assert order["id"] in str(board)
    _ok(r.preparer.post(f"{base}/start"))


def test_rejection_requires_a_reason_and_is_final(resto: SimpleNamespace) -> None:
    r = resto
    order = _qr_order(r, 2)
    base = f"{ORDERS}/{order['id']}"
    assert r.waiter.post(f"{base}/reject", json={}).status_code == 422
    assert r.waiter.post(f"{base}/reject", json={"reason": "   "}).status_code == 422
    rejected = _ok(r.waiter.post(f"{base}/reject", json={"reason": "Cuisine fermée"}))
    assert rejected["status"] == "REJECTED" and rejected["cancel_reason"] == "Cuisine fermée"
    assert all(line["status"] == "CANCELLED" for line in rejected["lines"])
    events = {e["event_type"]: e for e in _ok(r.owner.get(f"{base}/events"))}
    assert events["REJECTED"]["reason"] == "Cuisine fermée"
    audit = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_order.rejected"}))
    assert audit["items"][0]["data"]["reason"] == "Cuisine fermée"
    assert _code(r.waiter.post(f"{base}/confirm")) == (409, "order_not_pending")
    assert _code(r.owner.post(f"{base}/claim")) == (409, "order_rejected")
    listed = _ok(r.owner.get(ORDERS, params={"state": "cancelled"}))["items"]
    assert [o["id"] for o in listed] == [order["id"]]


def test_only_holders_of_the_permission_on_the_site_can_decide(resto: SimpleNamespace) -> None:
    r = resto
    order = _qr_order(r)
    base = f"{ORDERS}/{order['id']}"
    for api in (r.preparer, r.viewer):
        assert _code(api.post(f"{base}/confirm"))[0] == 403
        assert _code(api.post(f"{base}/reject", json={"reason": "x"}))[0] == 403
    # Autre site : la commande est introuvable.
    assert _code(r.seller_b.post(f"{base}/confirm")) == (404, "order_not_found")
    assert _ok(r.owner.get(f"{base}"))["status"] == "PENDING_CONFIRMATION"


def test_members_who_can_confirm_are_notified_until_it_is_handled(
    resto: SimpleNamespace,
) -> None:
    r = resto
    order = _qr_order(r)
    for api in (r.owner, r.seller, r.waiter):
        [notification] = _notifications(api)
        assert notification["entity_id"] == order["id"]
        assert notification["payload"]["point_label"] == "Comptoir"
        assert notification["resolved"] is False
    for api in (r.preparer, r.viewer, r.seller_b):
        assert _notifications(api) == []
    _ok(r.seller.post(f"{ORDERS}/{order['id']}/confirm"))
    assert _notifications(r.waiter)[0]["resolved"] is True
    # Acceptation automatique : aucune commande à confirmer, aucun avis.
    settings = _ok(r.owner.get(f"/restaurant/settings/{r.a}"))
    _ok(
        r.owner.put(
            f"/restaurant/settings/{r.a}",
            json={
                "payment_timing": settings["payment_timing"],
                "claim_protection_minutes": settings["claim_protection_minutes"],
                "claim_cooldown_minutes": settings["claim_cooldown_minutes"],
                "qr_auto_accept": True,
            },
        )
    )
    _ok(_send(r.client, r.counter, _body((r.menu.unit, 1))), 201)
    assert len(_notifications(r.waiter)) == 1


def test_concurrent_confirmations_succeed_once(resto: SimpleNamespace, settings: Settings) -> None:
    r = resto
    order = _qr_order(r)
    statuses: list[int] = []
    barrier = threading.Barrier(4)

    def confirm() -> None:
        with TestClient(create_app(settings)) as client:
            api = Api(client, r.seller.token)
            barrier.wait()
            statuses.append(api.post(f"{ORDERS}/{order['id']}/confirm").status_code)

    threads = [threading.Thread(target=confirm) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [200, 409, 409, 409]
    events = [e["event_type"] for e in _ok(r.owner.get(f"{ORDERS}/{order['id']}/events"))]
    assert events.count("CONFIRMED") == 1
