"""Palier R7-C (ADR-0052) — commandes sans compte par QR Code : moteur unique, plafonds réglables
par site, idempotence limitée au point et à la même requête, renouvellement du secret de suivi,
concurrence, limitation de fréquence, minimisation des données (nom, téléphone), effacement des
téléphones à 30 jours, droits SQL, migration 0057."""

import re
import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.restaurant_orders.contact_purge import erase_expired_contacts
from app.modules.restaurant_qr.tokens import secret_hash
from tests.conftest import PURGE_URL, add_site, set_site_module
from tests.test_restaurant_kitchen import _member
from tests.test_restaurant_menu_migration import _alembic, at_head  # noqa: F401
from tests.test_restaurant_orders import _api, _code, _menu, _ok
from tests.test_restaurant_qr_points import POINTS, QR, _table, _token

ORDERS = "/api/v1/public/restaurant/qr/orders"
SETTINGS = "/restaurant/qr/settings"
OWNER_SQL = "cancelled_by = (SELECT id FROM users WHERE email = 'owner@rqo-main.example.com'), "
TRACK_RE = re.compile(r"^https://app\.stockmanager\.test/q/track#([A-Za-z0-9_-]{43})$")


@pytest.fixture
def resto(provision: Any, qrclient: TestClient) -> SimpleNamespace:
    t = provision("rqo-main", profile="restaurant.restaurant", plan="ENTREPRISE")
    owner = _api(qrclient, "owner@rqo-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER", "restaurant"), 201)["id"]
    for site in (a, b):
        assert set_site_module(owner, site, QR, True).status_code == 204
    menu = _menu(owner, a, "ORD")
    menu_b = _menu(owner, b, "ORDB")
    t1 = _table(owner, a, "T1")
    table = _ok(owner.post(POINTS, json={"site_id": a, "kind": "TABLE", "table_id": t1}), 201)
    counter = _ok(
        owner.post(POINTS, json={"site_id": a, "kind": "COUNTER", "label": "Comptoir"}), 201
    )
    return SimpleNamespace(
        tenant=t.tenant_id,
        owner=owner,
        client=qrclient,
        a=a,
        b=b,
        menu=menu,
        menu_b=menu_b,
        t1=t1,
        table=_token(table),
        table_point=table["point"]["id"],
        counter=_token(counter),
        counter_point=counter["point"]["id"],
    )


def _body(*lines: tuple[str, int], **extra: Any) -> dict[str, Any]:
    return {
        "client_key": str(uuid.uuid4()),
        "lines": [{"menu_item_id": item, "quantity": qty} for item, qty in lines],
        **extra,
    }


def _send(client: TestClient, token: str, body: dict[str, Any]) -> Any:
    return client.post(ORDERS, json=body, headers={"X-QR-Token": token})


def _tracking(placed: dict[str, Any]) -> str:
    match = TRACK_RE.match(placed["tracking_url"])
    assert match is not None, placed["tracking_url"]
    return match.group(1)


def _staff_orders(r: SimpleNamespace, **params: Any) -> list[dict[str, Any]]:
    return list(_ok(r.owner.get("/restaurant/orders", params={"limit": 100, **params}))["items"])


def _settings(r: SimpleNamespace, site: str | None = None, **values: Any) -> Any:
    body = {
        "site_id": site or r.a,
        "max_lines": 30,
        "max_quantity_per_line": 20,
        "max_pending_per_point": 3,
        "customer_name_mode": "OPTIONAL",
        "customer_phone_mode": "HIDDEN",
        **values,
    }
    return r.owner.put(SETTINGS, json=body)


def _reject_in_db(owner_db: Session, order_id: str) -> None:
    owner_db.execute(
        text(
            "UPDATE restaurant_orders SET status = 'REJECTED', cancelled_at = now(), "
            f"{OWNER_SQL}cancel_reason = 'test' WHERE id = :id"
        ),
        {"id": order_id},
    )
    owner_db.commit()


# --- Création -----------------------------------------------------------------------------------


def test_counter_order_goes_through_the_single_engine_pending_confirmation(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    response = _send(r.client, r.counter, _body((r.menu.unit, 2), (r.menu.crate_item, 1)))
    placed = _ok(response, 201)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert (placed["status"], placed["replayed"]) == ("PENDING_CONFIRMATION", False)
    secret = _tracking(placed)
    [order] = _staff_orders(r)
    assert order["daily_number"] == placed["daily_number"]
    assert (order["channel"], order["service_mode"], order["status"]) == (
        "QR",
        "COUNTER",
        "PENDING_CONFIRMATION",
    )
    assert order["table_id"] is None and order["assigned_user_id"] is None
    detail = _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))
    totals = {line["menu_item_id"]: line["line_total"] for line in detail["lines"]}
    assert totals == {r.menu.unit: "1400.00", r.menu.crate_item: "8000.00"}  # prix du menu
    row = owner_db.execute(
        text(
            "SELECT o.created_by, e.actor_kind, e.actor_user_id, q.tracking_hash, q.point_id "
            "FROM restaurant_orders o JOIN restaurant_order_events e ON e.order_id = o.id "
            "JOIN restaurant_qr_orders q ON q.order_id = o.id WHERE o.id = :id"
        ),
        {"id": order["id"]},
    ).one()
    assert (row[0], row[1], row[2]) == (None, "PUBLIC", None)
    assert row[3] == secret_hash(secret) and str(row[4]) == r.counter_point
    audit = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_order.created"}))
    assert audit["total"] == 1 and audit["items"][0]["user_id"] is None
    assert secret not in str(audit)
    # Minimisation : ni l'adresse IP ni le navigateur d'un client anonyme dans l'audit.
    meta = owner_db.execute(
        text(
            "SELECT ip_address, user_agent FROM audit_logs "
            "WHERE action = 'restaurant_order.created' AND tenant_id = :t"
        ),
        {"t": r.tenant},
    ).one()
    assert tuple(meta) == (None, None)


def test_table_point_attaches_its_table_and_auto_accept_opens_the_order(
    resto: SimpleNamespace,
) -> None:
    r = resto
    placed = _ok(_send(r.client, r.table, _body((r.menu.unit, 1))), 201)
    [order] = _staff_orders(r)
    assert (order["service_mode"], order["table_id"]) == ("ON_SITE", r.t1)
    assert placed["status"] == "PENDING_CONFIRMATION"
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
    assert _ok(_send(r.client, r.table, _body((r.menu.unit, 1))), 201)["status"] == "OPEN"


def test_no_decision_field_is_accepted_from_the_public(resto: SimpleNamespace) -> None:
    r = resto
    for extra in (
        {"site_id": r.b},
        {"table_id": r.t1},
        {"status": "OPEN"},
        {"channel": "STAFF"},
        {"unit_price": "1"},
    ):
        assert _send(r.client, r.counter, _body((r.menu.unit, 1), **extra)).status_code == 422
    line_with_price = _body((r.menu.unit, 1))
    line_with_price["lines"][0]["unit_price"] = "1"
    assert _send(r.client, r.counter, line_with_price).status_code == 422
    for quantity in (0, -1, 100, 1.5, "2"):
        body = _body((r.menu.unit, 1))
        body["lines"][0]["quantity"] = quantity
        assert _send(r.client, r.counter, body).status_code == 422, quantity
    # Clé prévisible (non aléatoire) refusée.
    for key in (str(uuid.uuid1()), "00000000-0000-0000-0000-000000000000"):
        assert (
            _send(r.client, r.counter, _body((r.menu.unit, 1), client_key=key)).status_code == 422
        )
    assert (
        _send(r.client, r.counter, {"client_key": str(uuid.uuid4()), "lines": []}).status_code
        == 422
    )
    assert _staff_orders(r) == []


def test_unavailable_items_never_reveal_internal_reasons(resto: SimpleNamespace) -> None:
    r = resto
    _ok(
        r.owner.put(
            f"/restaurant/menu/items/{r.menu.unit}/availability",
            json={"available": False, "reason": "Rupture fournisseur"},
        )
    )
    for item in (r.menu.unit, r.menu_b.unit, str(uuid.uuid4())):
        response = _send(r.client, r.counter, _body((item, 1)))
        assert _code(response) == (422, "qr_order_item_unavailable")
        body = response.json()
        assert body["items"] == [item]
        for internal in ("unavailable", "blockers", "Rupture", "assortment", "label"):
            assert internal not in str({k: v for k, v in body.items() if k != "code"})
    assert _staff_orders(r) == []


# --- Réglages et plafonds -----------------------------------------------------------------------


def test_site_limits_are_enforced_and_bounded_by_the_platform(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    defaults = _ok(r.owner.get(SETTINGS, params={"site_id": r.a}))
    assert (
        defaults["max_lines"],
        defaults["max_quantity_per_line"],
        defaults["max_pending_per_point"],
        defaults["customer_name_mode"],
        defaults["customer_phone_mode"],
    ) == (30, 20, 3, "OPTIONAL", "HIDDEN")
    assert (defaults["platform_max_lines"], defaults["platform_max_quantity_per_line"]) == (100, 99)
    assert _code(_send(r.client, r.counter, _body((r.menu.unit, 21)))) == (
        422,
        "qr_order_quantity_too_large",
    )
    _ok(_settings(r, max_lines=1, max_quantity_per_line=5))
    assert _code(_send(r.client, r.counter, _body((r.menu.unit, 1), (r.menu.crate_item, 1)))) == (
        422,
        "qr_order_too_many_lines",
    )
    assert _code(_send(r.client, r.counter, _body((r.menu.unit, 6))))[1] == (
        "qr_order_quantity_too_large"
    )
    # Plafonds de la plateforme : refus du schéma, puis de la base (écriture directe).
    for field, value in (
        ("max_lines", 101),
        ("max_quantity_per_line", 100),
        ("max_pending_per_point", 21),
        ("max_lines", 0),
    ):
        assert _settings(r, **{field: value}).status_code == 422
    with pytest.raises(DBAPIError, match="max_lines_range"):
        owner_db.execute(
            text("UPDATE restaurant_qr_site_settings SET max_lines = 500 WHERE site_id = :s"),
            {"s": r.a},
        )
    owner_db.rollback()
    audit = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_qr_settings.updated"}))
    change = audit["items"][0]["data"]
    assert change["max_lines"] == {"before": "30", "after": "1"}


def test_settings_permissions_and_site_scope(resto: SimpleNamespace) -> None:
    r = resto
    seller = _member(r, "seller@rqo-main.example.com", "seller")
    assert _code(seller.put(SETTINGS, json={"site_id": r.a})) == (403, "permission_denied")
    viewer = _member(r, "viewer@rqo-main.example.com", "viewer")
    _ok(viewer.get(SETTINGS, params={"site_id": r.a}))
    assert (
        viewer.put(
            SETTINGS,
            json={
                "site_id": r.a,
                "max_lines": 5,
                "max_quantity_per_line": 5,
                "max_pending_per_point": 1,
                "customer_name_mode": "HIDDEN",
                "customer_phone_mode": "HIDDEN",
            },
        ).status_code
        == 403
    )
    manager = _member(r, "manager@rqo-main.example.com", "manager", all_sites=False, site_ids=[r.b])
    assert _code(manager.get(SETTINGS, params={"site_id": r.a})) == (403, "site_access_denied")
    _ok(manager.get(SETTINGS, params={"site_id": r.b}))


def test_name_and_phone_follow_the_site_setting(resto: SimpleNamespace) -> None:
    r = resto
    # Téléphone NON demandé : jamais conservé, même s'il est envoyé.
    _ok(
        _send(
            r.client,
            r.counter,
            _body((r.menu.unit, 1), customer_name="Awa", customer_phone="70 11 22 33"),
        ),
        201,
    )
    [order] = _staff_orders(r)
    detail = _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))
    assert (detail["call_name"], detail["contact_phone"]) == ("Awa", None)
    _ok(_settings(r, customer_name_mode="REQUIRED", customer_phone_mode="REQUIRED"))
    assert _code(_send(r.client, r.counter, _body((r.menu.unit, 1)))) == (
        422,
        "qr_order_name_required",
    )
    assert _code(_send(r.client, r.counter, _body((r.menu.unit, 1), customer_name="Awa"))) == (
        422,
        "qr_order_phone_required",
    )
    for phone in ("abc", "12", "+1234567890123456"):
        assert _code(
            _send(
                r.client,
                r.counter,
                _body((r.menu.unit, 1), customer_name="Awa", customer_phone=phone),
            )
        ) == (422, "qr_order_phone_invalid")
    _ok(
        _send(
            r.client,
            r.counter,
            _body((r.menu.unit, 1), customer_name="Awa", customer_phone="+226 70-11-22-33"),
        ),
        201,
    )
    phones = {o["contact_phone"] for o in _staff_orders(r)}
    assert "+22670112233" in phones  # séparateurs retirés, aucun indicatif ajouté
    _ok(_settings(r, customer_name_mode="HIDDEN", customer_phone_mode="OPTIONAL"))
    _ok(_send(r.client, r.counter, _body((r.menu.unit, 1), customer_name="Ignoré")), 201)
    newest = _staff_orders(r, sort="-created_at")[0]
    assert newest["call_name"] is None


# --- Idempotence, renouvellement du secret, concurrence -----------------------------------------


def test_same_key_and_same_request_never_creates_a_second_order(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    body = _body((r.menu.unit, 2))
    first = _ok(_send(r.client, r.counter, body), 201)
    response = _send(r.client, r.counter, body)
    again = _ok(response, 200)
    assert again["replayed"] is True and again["daily_number"] == first["daily_number"]
    old, new = _tracking(first), _tracking(again)
    assert old != new
    stored = (
        owner_db.execute(text("SELECT tracking_hash FROM restaurant_qr_orders")).scalars().all()
    )
    # Le secret est RENOUVELÉ : l'ancien lien ne correspond plus à rien.
    assert stored == [secret_hash(new)]
    assert len(_staff_orders(r)) == 1
    # Même clé, autre contenu : refus (une clé connue ne permet pas de changer la commande).
    changed = dict(body, lines=[{"menu_item_id": r.menu.unit, "quantity": 3}])
    assert _code(_send(r.client, r.counter, changed)) == (409, "qr_order_key_reused")
    # Même clé sur un AUTRE point : commande distincte (clé limitée au point).
    other = _ok(_send(r.client, r.table, body), 201)
    assert other["replayed"] is False and len(_staff_orders(r)) == 2
    # Au-delà du délai de rejeu : aucune émission de nouveau lien.
    owner_db.execute(text("UPDATE restaurant_qr_orders SET created_at = now() - interval '1 hour'"))
    owner_db.commit()
    assert _code(_send(r.client, r.counter, body)) == (409, "qr_order_already_sent")


def test_concurrent_identical_submissions_create_one_order(
    resto: SimpleNamespace, settings: Settings
) -> None:
    r = resto
    body = _body((r.menu.unit, 1))
    statuses: list[int] = []
    barrier = threading.Barrier(5)

    def submit() -> None:
        with TestClient(create_app(settings)) as client:
            barrier.wait()
            statuses.append(_send(client, r.counter, body).status_code)

    threads = [threading.Thread(target=submit) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [200, 200, 200, 200, 201]
    assert len(_staff_orders(r)) == 1


def test_pending_limit_per_point_holds_under_concurrency(
    resto: SimpleNamespace, settings: Settings, owner_db: Session
) -> None:
    r = resto
    _ok(_settings(r, max_pending_per_point=2))
    statuses: list[int] = []
    barrier = threading.Barrier(6)

    def submit() -> None:
        with TestClient(create_app(settings)) as client:
            barrier.wait()
            statuses.append(_send(client, r.counter, _body((r.menu.unit, 1))).status_code)

    threads = [threading.Thread(target=submit) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [201, 201, 409, 409, 409, 409]
    response = _send(r.client, r.counter, _body((r.menu.unit, 1)))
    assert _code(response) == (409, "qr_order_pending_limit")
    # L'autre point n'est pas concerné ; une commande traitée libère une place.
    _ok(_send(r.client, r.table, _body((r.menu.unit, 1))), 201)
    pending = [o for o in _staff_orders(r) if o["table_id"] is None]
    _reject_in_db(owner_db, pending[0]["id"])
    _ok(_send(r.client, r.counter, _body((r.menu.unit, 1))), 201)


def test_orders_are_rate_limited_per_ip_and_per_point(
    resto: SimpleNamespace, settings: Settings, owner_db: Session
) -> None:
    owner_db.execute(text("DELETE FROM rate_limit_hits"))
    owner_db.commit()
    per_ip = settings.model_copy(update={"qr_order_ip_limit": 2})
    with TestClient(create_app(per_ip)) as client:
        for _ in range(2):
            _ok(_send(client, resto.counter, _body((resto.menu.unit, 1))), 201)
        assert _code(_send(client, resto.counter, _body((resto.menu.unit, 1)))) == (
            429,
            "rate_limited",
        )
    owner_db.execute(text("DELETE FROM rate_limit_hits"))
    owner_db.commit()
    per_point = settings.model_copy(update={"qr_order_point_limit": 1})
    with TestClient(create_app(per_point)) as client:
        _ok(_send(client, resto.table, _body((resto.menu.unit, 1))), 201)
        assert _code(_send(client, resto.table, _body((resto.menu.unit, 1))))[0] == 429


def test_public_order_is_refused_like_the_menu(resto: SimpleNamespace) -> None:
    r = resto
    _ok(r.owner.post(f"/restaurant/tables/{r.t1}/deactivate"))
    assert _code(_send(r.client, r.table, _body((r.menu.unit, 1)))) == (404, "qr_unavailable")
    _ok(_send(r.client, r.counter, _body((r.menu.unit, 1))), 201)
    assert set_site_module(r.owner, r.a, QR, False).status_code == 204
    assert _code(_send(r.client, r.counter, _body((r.menu.unit, 1)))) == (404, "qr_unavailable")


# --- Téléphones : effacement à 30 jours ---------------------------------------------------------


@pytest.fixture
def purge_engine() -> Any:
    engine = create_engine(PURGE_URL)
    yield engine
    engine.dispose()


def test_phones_are_erased_30_days_after_the_end_only(
    resto: SimpleNamespace, owner_db: Session, purge_engine: Engine
) -> None:
    r = resto
    _ok(_settings(r, customer_phone_mode="OPTIONAL", max_pending_per_point=5))
    ids = []
    for phone in ("70000001", "70000002", "70000003"):
        _ok(_send(r.client, r.counter, _body((r.menu.unit, 1), customer_phone=phone)), 201)
    ids = {o["contact_phone"]: o["id"] for o in _staff_orders(r)}
    # Fin il y a 31 jours / fin il y a 2 jours / toujours à confirmer.
    owner_db.execute(
        text(
            f"UPDATE restaurant_orders SET status = 'REJECTED', cancel_reason = 'x', {OWNER_SQL}"
            "cancelled_at = now() - interval '31 days' WHERE id = :id"
        ),
        {"id": ids["70000001"]},
    )
    owner_db.execute(
        text(
            f"UPDATE restaurant_orders SET status = 'REJECTED', cancel_reason = 'x', {OWNER_SQL}"
            "cancelled_at = now() - interval '2 days' WHERE id = :id"
        ),
        {"id": ids["70000002"]},
    )
    owner_db.commit()
    with Session(purge_engine) as session:
        assert erase_expired_contacts(session) == 1
        session.commit()
        assert erase_expired_contacts(session) == 0  # idempotent
        session.commit()
    phones = dict(
        owner_db.execute(text("SELECT id::text, contact_phone FROM restaurant_orders")).all()
    )
    assert phones == {
        ids["70000001"]: None,
        ids["70000002"]: "70000002",
        ids["70000003"]: "70000003",
    }
    # Le rôle de purge ne voit rien d'autre et ne peut rien changer d'autre.
    with purge_engine.connect() as conn, pytest.raises(DBAPIError, match="permission denied"):
        conn.execute(text("SELECT call_name FROM restaurant_orders"))
    with purge_engine.connect() as conn:
        visible = conn.execute(text("SELECT count(*) FROM restaurant_orders")).scalar_one()
        # Seules les commandes finies depuis plus de 30 jours, téléphone déjà effacé.
        assert visible == 1
        assert (
            conn.execute(
                text("SELECT count(*) FROM restaurant_orders WHERE contact_phone IS NOT NULL")
            ).scalar_one()
            == 0
        )
    # Une commande finie reste immuable : seul l'effacement du téléphone est admis.
    with pytest.raises(DBAPIError, match="restaurant_final_state"):
        owner_db.execute(
            text("UPDATE restaurant_orders SET call_name = 'X' WHERE id = :id"),
            {"id": ids["70000002"]},
        )
    owner_db.rollback()
    with pytest.raises(DBAPIError, match="restaurant_final_state"):
        owner_db.execute(
            text(
                "UPDATE restaurant_orders SET contact_phone = NULL, call_name = 'X' WHERE id = :id"
            ),
            {"id": ids["70000002"]},
        )
    owner_db.rollback()


# --- Droits SQL et migration --------------------------------------------------------------------


def test_app_role_privileges_on_qr_tables(owner_db: Session, settings: Settings) -> None:
    role = settings.db_app_role
    for table, expected in (
        (
            "restaurant_qr_site_settings",
            {
                "max_lines",
                "max_quantity_per_line",
                "max_pending_per_point",
                "customer_name_mode",
                "customer_phone_mode",
                "updated_by",
                "updated_at",
            },
        ),
        ("restaurant_qr_orders", {"tracking_hash", "tracking_issued_at"}),
    ):
        privileges = set(
            owner_db.execute(
                text(
                    "SELECT privilege_type FROM information_schema.table_privileges "
                    "WHERE grantee = :r AND table_name = :t"
                ),
                {"r": role, "t": table},
            ).scalars()
        )
        assert privileges == {"SELECT", "INSERT"}
        updatable = set(
            owner_db.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE grantee = :r AND table_name = :t AND privilege_type = 'UPDATE'"
                ),
                {"r": role, "t": table},
            ).scalars()
        )
        assert updatable == expected
        flags = owner_db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
        assert tuple(flags) == (True, True)
    policies = dict(
        owner_db.execute(
            text("SELECT policyname, cmd FROM pg_policies WHERE tablename = 'restaurant_qr_orders'")
        ).all()
    )
    # Sauvegardes (R11, ADR-0058) : lecture et restauration d'UNE entreprise, même prédicat.
    assert policies == {
        "tenant_isolation": "ALL",
        "qr_tracking_lookup": "SELECT",
        "backup_tenant_read": "SELECT",
        "backup_tenant_restore": "ALL",
    }


def test_migration_0057_downgrade_refuses_to_lose_qr_orders(
    resto: SimpleNamespace,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    _ok(_send(resto.client, resto.counter, _body((resto.menu.unit, 1))), 201)
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="QR"):
        command.downgrade(_alembic(), "0056")
    with owner_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM restaurant_qr_orders")).scalar_one() == 1


def test_downgrade_without_data_restores_the_previous_trigger(
    owner_engine: Engine,
    migrated: None,
    at_head: None,  # noqa: F811
) -> None:
    owner_engine.dispose()
    command.downgrade(_alembic(), "0056")
    with owner_engine.begin() as conn:
        source = conn.execute(
            text("SELECT prosrc FROM pg_proc WHERE proname = 'restaurant_final_state'")
        ).scalar_one()
        assert "contact_phone" not in source
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'restaurant_orders' AND column_name = 'contact_phone'"
                )
            ).scalar_one()
            == 0
        )
    owner_engine.dispose()
    command.upgrade(_alembic(), "head")
    with owner_engine.begin() as conn:
        source = conn.execute(
            text("SELECT prosrc FROM pg_proc WHERE proname = 'restaurant_final_state'")
        ).scalar_one()
        assert "contact_phone" in source
