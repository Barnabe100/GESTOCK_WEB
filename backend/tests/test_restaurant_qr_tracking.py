"""Palier R7-E (ADR-0052) — suivi client temporaire : secret distinct du jeton du point, haché,
vérifié à chaque appel, renouvelé par un rejeu, expiré 12 h après la fin, informations limitées
(état, lignes, total, reste à régler, motif d'un refus), aucun paiement."""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.modules.restaurant_qr.tokens import new_secret, secret_hash
from tests.conftest import set_site_module
from tests.test_restaurant_kitchen import _dish
from tests.test_restaurant_orders import _api, _code, _menu, _ok
from tests.test_restaurant_orders_settlement import _cash, _settle
from tests.test_restaurant_qr_orders import _body, _send, _tracking
from tests.test_restaurant_qr_points import POINTS, QR, _table, _token
from tests.test_restaurant_qr_public import MENU

TRACK = "/api/v1/public/restaurant/qr/track"
ORDERS = "/restaurant/orders"
PUBLIC_KEYS = {
    "establishment",
    "site_name",
    "point",
    "currency",
    "daily_number",
    "status",
    "prep_status",
    "lines",
    "total",
    "remaining_due",
    "rejection_reason",
    "available_until",
}


@pytest.fixture
def resto(provision: Any, qrclient: TestClient) -> SimpleNamespace:
    t = provision("rqt-main", profile="restaurant.restaurant", plan="ENTREPRISE")
    owner = _api(qrclient, "owner@rqt-main.example.com")
    a = str(t.site_id)
    assert set_site_module(owner, a, QR, True).status_code == 204
    menu = _menu(owner, a, "TRK")
    dish = _dish(owner, a, "TRK")
    t1 = _table(owner, a, "T1")
    table = _ok(owner.post(POINTS, json={"site_id": a, "kind": "TABLE", "table_id": t1}), 201)
    return SimpleNamespace(
        tenant=t.tenant_id,
        owner=owner,
        client=qrclient,
        a=a,
        menu=menu,
        dish=dish,
        token=_token(table),
        point=table["point"]["id"],
    )


def _track(client: TestClient, secret: str | None) -> Any:
    return client.get(TRACK, headers={"X-QR-Tracking": secret} if secret is not None else {})


def _placed(r: SimpleNamespace, *lines: tuple[str, int]) -> tuple[str, dict[str, Any]]:
    placed = _ok(_send(r.client, r.token, _body(*(lines or ((r.menu.unit, 2),)))), 201)
    order = _ok(r.owner.get(ORDERS, params={"state": "pending", "sort": "-created_at"}))
    return _tracking(placed), dict(order["items"][0])


def _gone(response: Any) -> None:
    assert _code(response) == (404, "qr_tracking_unavailable")
    assert set(response.json()) == {"type", "title", "status", "detail", "code"}
    assert response.headers["cache-control"] == "no-store"


def test_tracking_shows_only_what_the_customer_needs(resto: SimpleNamespace) -> None:
    r = resto
    secret, _order = _placed(r)
    response = _track(r.client, secret)
    tracking = _ok(response)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert set(tracking) == PUBLIC_KEYS
    assert (tracking["status"], tracking["point"]) == (
        "PENDING_CONFIRMATION",
        {"kind": "TABLE", "label": "T1"},
    )
    assert (tracking["total"], tracking["remaining_due"]) == ("1400.00", "1400.00")
    assert tracking["available_until"] is None and tracking["rejection_reason"] is None
    [line] = tracking["lines"]
    assert set(line) == {"name", "packaging_name", "quantity", "status"}
    assert (line["name"], line["quantity"], line["status"]) == ("Bière TRK", "2.000", "RECEIVED")
    flat = str(tracking)
    for internal in ("BIERE-TRK", "owner", "assigned", "user", "cost", "stock", "purchase"):
        assert internal not in flat
    # Prix d'achat (400) jamais exposé.
    assert "'400.00'" not in flat and line.get("unit_price") is None


def test_unknown_malformed_and_wrong_kind_secrets_are_refused(resto: SimpleNamespace) -> None:
    r = resto
    secret, _order = _placed(r)
    for bad in (None, "", "short", new_secret(), r.token):
        _gone(_track(r.client, bad))
    # Le secret de suivi n'ouvre jamais le menu (secrets distincts).
    assert _code(r.client.get(MENU, headers={"X-QR-Token": secret})) == (404, "qr_unavailable")
    _ok(_track(r.client, secret))


def test_replay_renews_the_secret_and_disables_the_previous_link(
    resto: SimpleNamespace,
) -> None:
    r = resto
    body = _body((r.menu.unit, 1))
    first = _tracking(_ok(_send(r.client, r.token, body), 201))
    _ok(_track(r.client, first))
    second = _tracking(_ok(_send(r.client, r.token, body), 200))
    _gone(_track(r.client, first))
    _ok(_track(r.client, second))


def test_tracking_survives_point_regeneration_and_deactivation(resto: SimpleNamespace) -> None:
    r = resto
    secret, _order = _placed(r)
    _ok(r.owner.post(f"{POINTS}/{r.point}/regenerate", json={"confirm_invalidation": True}))
    _ok(_track(r.client, secret))
    _ok(r.owner.post(f"{POINTS}/{r.point}/deactivate"))
    _ok(_track(r.client, secret))
    # Le menu, lui, n'est plus accessible par l'ancien jeton.
    assert _code(r.client.get(MENU, headers={"X-QR-Token": r.token}))[0] == 404


def test_rejection_reason_is_shown_but_never_a_staff_cancellation_reason(
    resto: SimpleNamespace,
) -> None:
    r = resto
    secret, order = _placed(r)
    _ok(r.owner.post(f"{ORDERS}/{order['id']}/reject", json={"reason": "Plus de bière"}))
    tracking = _ok(_track(r.client, secret))
    assert (tracking["status"], tracking["rejection_reason"]) == ("REJECTED", "Plus de bière")
    assert tracking["remaining_due"] == "0.00" and tracking["available_until"] is not None
    other, order2 = _placed(r)
    _ok(r.owner.post(f"{ORDERS}/{order2['id']}/confirm"))
    _ok(r.owner.post(f"{ORDERS}/{order2['id']}/cancel", json={"reason": "Motif interne"}))
    cancelled = _ok(_track(r.client, other))
    assert cancelled["status"] == "CANCELLED" and cancelled["rejection_reason"] is None
    assert "Motif interne" not in str(cancelled)


def test_link_expires_12_hours_after_the_end_only(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    secret, order = _placed(r)
    # Commande en cours depuis longtemps : jamais expirée tant qu'elle n'est pas finie.
    owner_db.execute(
        text("UPDATE restaurant_orders SET created_at = now() - interval '3 days' WHERE id = :i"),
        {"i": order["id"]},
    )
    owner_db.commit()
    _ok(_track(r.client, secret))
    # Refus daté de 11 h puis de 13 h (une commande finie est immuable : une écriture chacune).
    for hours, available in ((11, True), (13, False)):
        secret, order = _placed(r, (r.menu.crate_item, 1))
        owner_db.execute(
            text(
                "UPDATE restaurant_orders SET status = 'REJECTED', cancel_reason = 'Fermé', "
                "cancelled_by = (SELECT id FROM users WHERE email = 'owner@rqt-main.example.com'), "
                "cancelled_at = now() - make_interval(hours => :h) WHERE id = :i"
            ),
            {"h": hours, "i": order["id"]},
        )
        owner_db.commit()
        response = _track(r.client, secret)
        if available:
            assert _ok(response)["available_until"] is not None
        else:
            _gone(response)


def test_remaining_due_is_read_on_the_sale(resto: SimpleNamespace) -> None:
    r = resto
    secret, order = _placed(r, (r.dish.item, 1))
    _ok(r.owner.post(f"{ORDERS}/{order['id']}/confirm"))
    unsettled = _ok(_track(r.client, secret))
    assert (unsettled["total"], unsettled["remaining_due"]) == ("2500.00", "2500.00")
    _ok(_settle(r.owner, order["id"], payments=_cash("2500")), 201)
    # Réglée : le reste dû est lu sur la vente (paiement complet).
    tracking = _ok(_track(r.client, secret))
    assert (tracking["total"], tracking["remaining_due"]) == ("2500.00", "0.00")


def test_tracking_follows_site_subscription_and_module(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    secret, _order = _placed(r)
    assert set_site_module(r.owner, r.a, QR, False).status_code == 204
    _gone(_track(r.client, secret))
    assert set_site_module(r.owner, r.a, QR, True).status_code == 204
    owner_db.execute(
        text("UPDATE subscriptions SET status = 'suspended' WHERE site_id = :s"), {"s": r.a}
    )
    owner_db.commit()
    _gone(_track(r.client, secret))


def test_tracking_lookup_policy_shows_only_the_matching_link(
    resto: SimpleNamespace, provision: Any, qrclient: TestClient, app_engine: Engine
) -> None:
    r = resto
    secret, _order = _placed(r)
    _placed(r, (r.menu.crate_item, 1))
    t2 = provision("rqt-other", profile="restaurant.restaurant", plan="ENTREPRISE")
    factory = create_session_factory(app_engine)
    count = "SELECT count(*) FROM restaurant_qr_orders"
    with factory() as db:
        assert db.execute(text(count)).scalar_one() == 0
        db.rollback()
        set_db_context(db, tenant_id=t2.tenant_id)
        assert db.execute(text(count)).scalar_one() == 0
        db.rollback()
        set_db_context(db, tenant_id=None, qr_tracking_hash=secret_hash(secret))
        assert db.execute(text(count)).scalar_one() == 1
        assert (
            db.execute(
                text("UPDATE restaurant_qr_orders SET tracking_hash = tracking_hash")
            ).rowcount
            == 0
        )
        db.rollback()
        # Le jeton d'un point n'est pas un secret de suivi (et inversement).
        set_db_context(db, tenant_id=None, qr_tracking_hash=secret_hash(r.token))
        assert db.execute(text(count)).scalar_one() == 0
        db.rollback()
