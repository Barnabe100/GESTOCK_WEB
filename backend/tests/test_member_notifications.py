"""Palier R4 — notifications des membres (ADR-0049 D8 ; ``RESTAURANT.md`` §4, §9, §12, §14).

- Destinataires de « commande prête » : responsable + titulaires EFFECTIFS de
  ``restaurant.orders.order.serve`` sur le site, lecteurs de la commande, MOINS l'auteur.
- Une notification par destinataire et par évènement (clé anti-doublon), même sous
  concurrence ou si l'émission est rejouée ; nouvelle transition légitime = nouvel avis.
- Résolution calculée à la lecture ; lu / non lu individuel ; compteur ; « tout marquer lu ».
- Isolation : jamais lisibles ni modifiables par un autre membre, un autre site ou une autre
  entreprise (API ET SQL, RLS restrictive) ; rôle applicatif sans ``DELETE``.
- Purge à 30 jours par le rôle SQL DÉDIÉ (droits minimaux vérifiés).

``restaurant.orders`` reste PLANIFIÉ en production : les parcours des commandes utilisent le
registre de test (``oclient``) ; un test vérifie que la production n'expose aucune route de
commande tandis que les routes génériques des notifications sont montées.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from app.cli import main as cli_main
from app.core.config import Settings, get_settings
from app.core.db import create_session_factory, set_db_context
from app.platform.member_notifications.service import notify_members
from app.platform.registry import ModuleStatus, get_registry
from tests.conftest import PASSWORD, PURGE_URL, Api, add_site
from tests.test_restaurant_orders import MAQUIS, ORDERS, _api, _code, _line, _menu, _ok, _order
from tests.test_restaurant_orders_migration import _alembic, at_head  # noqa: F401

P = "restaurant.orders.order"
VIEW = "organization.notification.view"
KIND = "restaurant.order.ready"


def _join(
    r: SimpleNamespace, email: str, roles: list[dict[str, str]], **access: Any
) -> SimpleNamespace:
    body = {
        "email": email,
        "full_name": email.split("@")[0],
        "password": "Provisoire-123",
        "roles": roles,
        **({"all_sites": True} | access),
    }
    member = _ok(r.owner.post("/members", json=body), 201)
    temporary = _api(r.client, email, "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return SimpleNamespace(
        api=_api(r.client, email), user_id=member["user_id"], membership_id=member["id"]
    )


def _template(r: SimpleNamespace, code: str) -> str:
    roles = {x["template_code"]: x["id"] for x in r.owner.get("/roles").json()}
    if code not in roles:
        roles[code] = _ok(r.owner.post("/roles/from-template", json={"template_code": code}), 201)[
            "id"
        ]
    return roles[code]


def _custom(r: SimpleNamespace, name: str, permissions: list[str]) -> str:
    return str(
        _ok(r.owner.post("/roles", json={"name": name, "permissions": permissions}), 201)["id"]
    )


def _mine(api: Api, **params: Any) -> list[dict[str, Any]]:
    return list(_ok(api.get("/member-notifications", params={"limit": 100, **params}))["items"])


def _unread(api: Api) -> int:
    return int(_ok(api.get("/member-notifications/unread-count"))["unread"])


def _ready_everything(api: Api, order_id: str) -> Any:
    base = f"/restaurant/orders/{order_id}"
    _ok(api.post(f"{base}/start"))
    return _ok(api.post(f"{base}/ready"))


def _rows(owner_engine: Engine, order_id: str | None = None) -> list[Any]:
    """Notifications vues par le propriétaire du schéma (hors RLS, le temps du comptage)."""
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE member_notifications NO FORCE ROW LEVEL SECURITY"))
        rows = conn.execute(
            text(
                "SELECT * FROM member_notifications "
                + ("WHERE entity_id = :o " if order_id else "")
                + "ORDER BY created_at, recipient_user_id"
            ),
            {"o": order_id} if order_id else {},
        ).all()
        conn.execute(text("ALTER TABLE member_notifications FORCE ROW LEVEL SECURITY"))
    return list(rows)


@pytest.fixture
def team(provision: Any, oclient: TestClient) -> SimpleNamespace:
    """Maquis, menu, et une équipe couvrant chaque cas de D8."""
    t = provision("ro-notif", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-notif.example.com")
    site = str(t.site_id)
    r = SimpleNamespace(
        tenant=t.tenant_id,
        site=site,
        owner=owner,
        owner_id=str(t.owner_user_id),
        client=oclient,
        menu=_menu(owner, site, "N"),
    )
    other = _ok(
        add_site(owner, "Maquis Nord", "MNORD", "restaurant", business_profile_code=MAQUIS), 201
    )
    r.other_site = other["id"]
    # Préparateur (modèle facultatif) : prépare, ne sert pas — auteur de « prête ».
    r.preparer = _join(r, "prep@ro-notif.example.com", [{"role_id": _template(r, "preparer")}])
    # Serveur (modèle facultatif) : sert.
    r.waiter = _join(r, "serveur@ro-notif.example.com", [{"role_id": _template(r, "waiter")}])
    # Responsable sans ``serve`` : lecture + prise en charge seulement.
    claimer = _custom(r, "Prise seule", [f"{P}.view", f"{P}.claim", VIEW])
    r.claimer = _join(r, "responsable@ro-notif.example.com", [{"role_id": claimer}])
    # Rôle personnalisé avec ``serve`` (et ``view``) : inclus.
    custom = _custom(r, "Runner", [f"{P}.view", f"{P}.serve", VIEW])
    r.runner = _join(r, "runner@ro-notif.example.com", [{"role_id": custom}])
    # ``serve`` sans ``view`` : écarté.
    blind = _custom(r, "Service aveugle", [f"{P}.serve"])
    r.blind = _join(r, "aveugle@ro-notif.example.com", [{"role_id": blind}])
    # Serveur dont le rôle est limité à l'AUTRE site : écarté.
    r.elsewhere = _join(
        r,
        "ailleurs@ro-notif.example.com",
        [{"role_id": _template(r, "waiter"), "site_id": r.other_site}],
    )
    # Accès au seul autre site : écarté.
    r.other_team = _join(
        r,
        "nord@ro-notif.example.com",
        [{"role_id": _template(r, "waiter")}],
        all_sites=False,
        site_ids=[r.other_site],
    )
    # Consultant : lit la commande, ne sert pas — écarté (sauf s'il était responsable).
    r.viewer = _join(r, "consultant@ro-notif.example.com", [{"role_id": _template(r, "viewer")}])
    # Serveur désactivé : écarté.
    r.gone = _join(r, "parti@ro-notif.example.com", [{"role_id": _template(r, "waiter")}])
    _ok(r.owner.post(f"/members/{r.gone.membership_id}/deactivate"))
    return r


# --- Destinataires ------------------------------------------------------------------------------


def test_ready_notifies_responsible_and_effective_servers_minus_the_author(
    team: SimpleNamespace, owner_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit), _line(r.menu.crate_item)]), 201)
    _ok(r.claimer.api.post(f"/restaurant/orders/{order['id']}/claim"))
    _ready_everything(r.preparer.api, order["id"])
    rows = _rows(owner_engine, order["id"])
    recipients = {str(row.recipient_user_id) for row in rows}
    assert recipients == {r.owner_id, r.waiter.user_id, r.claimer.user_id, r.runner.user_id}
    # Jamais l'auteur ; ni ``serve`` sans ``view``, ni rôle d'un autre site, ni autre site,
    # ni simple lecteur, ni membre désactivé.
    for excluded in (r.preparer, r.blind, r.elsewhere, r.other_team, r.viewer, r.gone):
        assert excluded.user_id not in recipients
    # Une seule notification par destinataire ; même évènement source ; contexte sans prix.
    assert len(rows) == 4
    assert len({row.source_id for row in rows}) == 1
    payload = rows[0].payload
    assert payload["daily_number"] == order["daily_number"]
    assert payload["line_count"] == 2
    assert set(payload) == {"daily_number", "business_date", "line_count", "line_ids"}
    assert all(str(row.site_id) == r.site for row in rows)
    # Vu par chacun via l'API.
    item = _mine(r.waiter.api)[0]
    assert (item["kind"], item["entity_id"], item["resolved"]) == (KIND, order["id"], False)
    assert item["site"]["id"] == r.site
    assert _mine(r.preparer.api) == []


def test_responsible_author_is_excluded_and_others_are_notified(
    team: SimpleNamespace, owner_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(r.owner.post(f"/restaurant/orders/{order['id']}/claim"))
    _ready_everything(r.owner, order["id"])  # responsable ET auteur
    recipients = {str(row.recipient_user_id) for row in _rows(owner_engine, order["id"])}
    assert r.owner_id not in recipients
    assert recipients == {r.waiter.user_id, r.runner.user_id}
    assert _mine(r.owner) == []


def test_viewer_responsible_is_notified_but_not_without_order_view(
    team: SimpleNamespace, owner_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(r.claimer.api.post(f"/restaurant/orders/{order['id']}/claim"))
    # Le responsable perd la lecture des commandes avant « prête » : écarté.
    _ok(
        r.owner.patch(
            f"/members/{r.claimer.membership_id}",
            json={"roles": [{"role_id": _custom(r, "Rien", [VIEW])}], "all_sites": True},
        )
    )
    _ready_everything(r.preparer.api, order["id"])
    recipients = {str(row.recipient_user_id) for row in _rows(owner_engine, order["id"])}
    assert r.claimer.user_id not in recipients
    assert r.waiter.user_id in recipients


def test_notification_only_when_the_whole_order_is_ready(
    team: SimpleNamespace, owner_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit), _line(r.menu.crate_item)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    first, second = (line["id"] for line in order["lines"])
    _ok(r.preparer.api.post(f"{base}/start"))
    _ok(r.preparer.api.post(f"{base}/ready", json={"line_ids": [first]}))
    assert _rows(owner_engine, order["id"]) == []  # une partie seulement : aucun avis
    _ok(r.preparer.api.post(f"{base}/ready", json={"line_ids": [second]}))
    rows = _rows(owner_engine, order["id"])
    assert len(rows) == 3  # propriétaire, serveur, runner (aucun responsable)
    assert set(rows[0].payload["line_ids"]) == {first, second}


# --- Doublons, transitions répétées, rejeu, concurrence -----------------------------------------


def test_repeated_legitimate_transition_creates_a_new_notice_never_a_duplicate(
    team: SimpleNamespace, owner_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    _ready_everything(r.preparer.api, order["id"])
    _ok(r.preparer.api.post(f"{base}/revert"))  # correction : aucun avis
    assert len(_rows(owner_engine, order["id"])) == 3
    _ok(r.preparer.api.post(f"{base}/ready"))  # nouvel évènement READY
    rows = _rows(owner_engine, order["id"])
    assert len(rows) == 6
    assert len({row.source_id for row in rows}) == 2
    per_recipient: dict[str, int] = {}
    for row in rows:
        per_recipient[str(row.recipient_user_id)] = (
            per_recipient.get(str(row.recipient_user_id), 0) + 1
        )
    assert set(per_recipient.values()) == {2}
    # Lignes ajoutées à une commande prête, puis prêtes : nouvel avis.
    _ok(r.owner.post(f"{base}/lines", json={"lines": [_line(r.menu.unit)]}))
    _ready_everything(r.preparer.api, order["id"])
    assert len(_rows(owner_engine, order["id"])) == 9
    assert _unread(r.waiter.api) == 3


def test_replayed_emission_creates_nothing(
    team: SimpleNamespace, app_engine: Engine, owner_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ready_everything(r.preparer.api, order["id"])
    first = _rows(owner_engine, order["id"])
    event = first[0].source_id
    ctx = SimpleNamespace(
        tenant_id=r.tenant, user=SimpleNamespace(id=uuid.UUID(r.preparer.user_id))
    )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=r.tenant, user_id=uuid.UUID(r.preparer.user_id))
        created = notify_members(
            db,
            ctx,  # type: ignore[arg-type]
            site_id=uuid.UUID(r.site),
            recipients=[row.recipient_user_id for row in first] + [uuid.UUID(r.preparer.user_id)],
            kind=KIND,
            source_type="restaurant_order_event",
            source_id=event,
            entity_type="restaurant_order",
            entity_id=uuid.UUID(order["id"]),
            payload={},
            now=datetime.now(UTC),
        )
        db.commit()
    assert created == []  # déjà notifiés ; l'auteur jamais
    assert len(_rows(owner_engine, order["id"])) == len(first)


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


def test_concurrent_transitions_notify_once(team: SimpleNamespace, owner_engine: Engine) -> None:
    r = team
    second_preparer = _join(
        r, "prep2@ro-notif.example.com", [{"role_id": _template(r, "preparer")}]
    )
    for _ in range(3):
        order = _ok(_order(r.owner, r.site, [_line(r.menu.unit), _line(r.menu.crate_item)]), 201)
        base = f"/restaurant/orders/{order['id']}"
        first, second = (line["id"] for line in order["lines"])
        _ok(r.preparer.api.post(f"{base}/start"))
        # Deux préparateurs terminent chacun une ligne en même temps : une seule transition rend
        # la commande entière prête.
        results = _race(
            [
                lambda b=base, li=first: r.preparer.api.post(f"{b}/ready", json={"line_ids": [li]}),
                lambda b=base, li=second: second_preparer.api.post(
                    f"{b}/ready", json={"line_ids": [li]}
                ),
            ]
        )
        assert [res.status_code for res in results] == [200, 200]
        rows = _rows(owner_engine, order["id"])
        assert len({row.source_id for row in rows}) == 1
        recipients = [str(row.recipient_user_id) for row in rows]
        assert len(recipients) == len(set(recipients))
        # Le dernier auteur est exclu ; l'autre préparateur ne sert pas : jamais destinataire.
        assert set(recipients) == {
            r.owner_id,
            r.waiter.user_id,
            r.runner.user_id,
        }
    # Deux « prête » simultanées sur la même commande : une seule réussit, un seul avis.
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    _ok(r.preparer.api.post(f"{base}/start"))
    results = _race(
        [
            lambda: r.preparer.api.post(f"{base}/ready"),
            lambda: second_preparer.api.post(f"{base}/ready"),
        ]
    )
    assert sorted(res.status_code for res in results) == [200, 409]
    rows = _rows(owner_engine, order["id"])
    assert len({row.source_id for row in rows}) == 1
    assert len(rows) == 3


# --- Lecture : résolution, lu / non lu, compteur ------------------------------------------------


def test_resolution_is_computed_at_read_time(team: SimpleNamespace, owner_engine: Engine) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit), _line(r.menu.crate_item)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    first, second = (line["id"] for line in order["lines"])
    _ready_everything(r.preparer.api, order["id"])
    before = _rows(owner_engine, order["id"])
    assert _mine(r.waiter.api)[0]["resolved"] is False
    _ok(r.waiter.api.post(f"{base}/serve", json={"line_ids": [first]}))
    assert _mine(r.runner.api)[0]["resolved"] is False  # une ligne reste à servir
    _ok(r.owner.post(f"{base}/cancel-lines", json={"line_ids": [second], "reason": "Casse"}))
    assert _mine(r.runner.api)[0]["resolved"] is True  # servie + annulée
    assert _mine(r.waiter.api)[0]["resolved"] is True
    # Aucune écriture : lignes de notification inchangées (ni lues, ni modifiées).
    after = _rows(owner_engine, order["id"])
    assert [(row.id, row.read_at, row.payload) for row in after] == [
        (row.id, row.read_at, row.payload) for row in before
    ]


def test_read_state_is_individual_with_counter_and_mark_all(team: SimpleNamespace) -> None:
    r = team
    orders = [_ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201) for _ in range(3)]
    for order in orders:
        _ready_everything(r.preparer.api, order["id"])
    assert _unread(r.waiter.api) == 3
    assert _unread(r.runner.api) == 3
    items = _mine(r.waiter.api)
    assert [i["payload"]["daily_number"] for i in items] == [3, 2, 1]  # plus récentes d'abord
    assert r.waiter.api.post(f"/member-notifications/{items[0]['id']}/read").status_code == 204
    assert r.waiter.api.post(f"/member-notifications/{items[0]['id']}/read").status_code == 204
    assert _unread(r.waiter.api) == 2
    assert len(_mine(r.waiter.api, unread="true")) == 2
    assert _unread(r.runner.api) == 3  # propre à chaque membre
    assert r.waiter.api.post("/member-notifications/read-all").status_code == 204
    assert _unread(r.waiter.api) == 0
    assert all(i["read_at"] for i in _mine(r.waiter.api))
    assert _unread(r.runner.api) == 3


# --- Isolation : API et SQL ----------------------------------------------------------------------


def test_notifications_of_others_are_unreadable_and_unwritable(
    team: SimpleNamespace, provision: Any, oclient: TestClient, app_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ready_everything(r.preparer.api, order["id"])
    theirs = _mine(r.waiter.api)[0]["id"]
    # API : un autre membre (même destinataire d'avis, même site) ne la voit ni ne la marque.
    assert theirs not in {i["id"] for i in _mine(r.runner.api)}
    assert _code(r.runner.api.post(f"/member-notifications/{theirs}/read")) == (
        404,
        "notification_not_found",
    )
    assert _code(r.preparer.api.post(f"/member-notifications/{theirs}/read")) == (
        404,
        "notification_not_found",
    )
    # Autre entreprise.
    provision("ro-notif2", profile=MAQUIS, plan="ENTREPRISE")
    stranger = _api(oclient, "owner@ro-notif2.example.com")
    assert _mine(stranger) == []
    assert stranger.post(f"/member-notifications/{theirs}/read").status_code == 404
    assert _unread(r.waiter.api) == 1  # toujours non lue
    # SQL (rôle applicatif, RLS) : autre membre de l'entreprise → invisible, non modifiable.
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=r.tenant, user_id=uuid.UUID(r.runner.user_id))
        assert (
            db.execute(
                text("SELECT count(*) FROM member_notifications WHERE id = :i"), {"i": theirs}
            ).scalar_one()
            == 0
        )
        updated = db.execute(
            text("UPDATE member_notifications SET read_at = now() WHERE id = :i"), {"i": theirs}
        )
        assert updated.rowcount == 0  # type: ignore[attr-defined]
        db.rollback()
        # Le destinataire lui-même ne peut modifier que ``read_at``.
        set_db_context(db, tenant_id=r.tenant, user_id=uuid.UUID(r.waiter.user_id))
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(
                text("UPDATE member_notifications SET payload = '{}' WHERE id = :i"),
                {"i": theirs},
            )
        db.rollback()
        # Jamais de suppression par le rôle applicatif.
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM member_notifications WHERE id = :i"), {"i": theirs})
        db.rollback()
        # Insertion pour une autre entreprise refusée (isolation).
        set_db_context(db, tenant_id=r.tenant, user_id=uuid.UUID(r.waiter.user_id))
        stranger_tenant = uuid.uuid4()
        with pytest.raises(DBAPIError):
            db.execute(
                text(
                    "INSERT INTO member_notifications (id, tenant_id, site_id, recipient_user_id, "
                    "kind, source_type, source_id, entity_type, entity_id, payload) VALUES "
                    "(:id, :t, :s, :u, 'x', 'x', :id, 'x', :id, '{}')"
                ),
                {"id": uuid.uuid4(), "t": stranger_tenant, "s": r.site, "u": r.waiter.user_id},
            )
        db.rollback()
        # Autre entreprise : rien de visible.
        set_db_context(db, tenant_id=uuid.uuid4(), user_id=uuid.UUID(r.waiter.user_id))
        assert db.execute(text("SELECT count(*) FROM member_notifications")).scalar_one() == 0
        db.rollback()


def test_visibility_follows_site_access_and_permission(
    team: SimpleNamespace, owner_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ready_everything(r.preparer.api, order["id"])
    assert len(_mine(r.waiter.api)) == 1
    # Filtre de site : l'autre site n'en a aucune.
    assert _mine(r.waiter.api, site_id=r.other_site) == []
    # Le serveur n'a plus accès qu'à l'autre site : sa notification n'est plus lisible.
    _ok(
        r.owner.patch(
            f"/members/{r.waiter.membership_id}",
            json={
                "roles": [{"role_id": _template(r, "waiter")}],
                "all_sites": False,
                "site_ids": [r.other_site],
            },
        )
    )
    assert _mine(r.waiter.api) == []
    assert _unread(r.waiter.api) == 0
    # Sans ``organization.notification.view`` : refus.
    no_view = _custom(r, "Sans avis", [f"{P}.view", f"{P}.serve"])
    _ok(
        r.owner.patch(
            f"/members/{r.runner.membership_id}",
            json={"roles": [{"role_id": no_view}], "all_sites": True},
        )
    )
    assert _code(r.runner.api.get("/member-notifications")) == (403, "permission_denied")
    assert len(_rows(owner_engine, order["id"])) == 3  # historique intact


# --- Production : module planifié ---------------------------------------------------------------


def test_production_keeps_orders_planned_and_mounts_generic_notifications(
    app: Any, provision: Any, api_for: Any
) -> None:
    assert get_registry().get(ORDERS).status is ModuleStatus.PLANNED
    paths = set(app.openapi()["paths"])
    assert not [p for p in paths if p.startswith("/api/v1/restaurant/orders")]
    assert {
        "/api/v1/member-notifications",
        "/api/v1/member-notifications/unread-count",
        "/api/v1/member-notifications/read-all",
        "/api/v1/member-notifications/{notification_id}/read",
    } <= paths
    provision("ro-notif-prod", profile=MAQUIS)
    owner = api_for("owner@ro-notif-prod.example.com")
    assert VIEW in owner.get("/me/capabilities").json()["permissions"]
    assert _mine(owner) == []
    assert _unread(owner) == 0


def test_base_roles_grant_notification_view() -> None:
    from app.platform.catalog.loader import load_catalog

    catalog = load_catalog(get_registry())
    templates = catalog.role_templates
    for code in ("manager", "seller", "waiter", "preparer"):
        assert VIEW in templates[code].permission_patterns, code


# --- Purge (rôle SQL dédié) ----------------------------------------------------------------------


def _age(owner_engine: Engine, ids: list[uuid.UUID], days: int) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE member_notifications NO FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                "UPDATE member_notifications SET created_at = now() - make_interval(days => :d) "
                "WHERE id = ANY(:ids)"
            ),
            {"d": days, "ids": ids},
        )
        conn.execute(text("ALTER TABLE member_notifications FORCE ROW LEVEL SECURITY"))


@pytest.fixture
def purge_engine() -> Any:
    engine = create_engine(PURGE_URL)
    yield engine
    engine.dispose()


def test_purge_removes_only_notifications_older_than_30_days(
    team: SimpleNamespace, owner_engine: Engine, settings: Settings, capsys: Any
) -> None:
    r = team
    orders = [_ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201) for _ in range(3)]
    for order in orders:
        _ready_everything(r.preparer.api, order["id"])
    old = [row.id for row in _rows(owner_engine, orders[0]["id"])]
    edge = [row.id for row in _rows(owner_engine, orders[1]["id"])]
    _age(owner_engine, old, 31)
    _age(owner_engine, edge, 29)
    assert cli_main(["notifications", "purge"], settings) == 0
    assert "purgées (plus de 30 jours) : 3" in capsys.readouterr().out
    remaining = {row.id for row in _rows(owner_engine)}
    assert not remaining & set(old)
    assert set(edge) <= remaining
    assert len(remaining) == 6
    # Idempotente.
    assert cli_main(["notifications", "purge"], settings) == 0
    assert "purgées (plus de 30 jours) : 0" in capsys.readouterr().out
    assert len(_rows(owner_engine)) == 6


def test_purge_role_has_minimal_rights(
    team: SimpleNamespace, owner_engine: Engine, purge_engine: Engine
) -> None:
    r = team
    orders = [_ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201) for _ in range(2)]
    for order in orders:
        _ready_everything(r.preparer.api, order["id"])
    old = [row.id for row in _rows(owner_engine, orders[0]["id"])]
    _age(owner_engine, old, 40)
    recent = _rows(owner_engine, orders[1]["id"])[0].id

    def refused(sql: str, **params: Any) -> None:
        with purge_engine.connect() as conn, pytest.raises(DBAPIError, match="permission denied"):
            conn.execute(text(sql), params)

    with purge_engine.connect() as conn:
        role = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert (role.rolsuper, role.rolbypassrls) == (False, False)
        # Identifiant et date des seules notifications de plus de 30 jours.
        seen = {
            row.id for row in conn.execute(text("SELECT id, created_at FROM member_notifications"))
        }
        assert seen == set(old)
        # Jamais une notification récente, même ciblée.
        gone = conn.execute(text("DELETE FROM member_notifications WHERE id = :i"), {"i": recent})
        assert gone.rowcount == 0
        conn.rollback()
    # Aucune autre colonne, aucune écriture hors suppression, aucune autre table.
    refused("SELECT payload FROM member_notifications")
    refused("SELECT recipient_user_id FROM member_notifications")
    refused("SELECT * FROM member_notifications")
    refused("UPDATE member_notifications SET read_at = now()")
    refused(
        "INSERT INTO member_notifications (id, tenant_id, site_id, recipient_user_id, kind, "
        "source_type, source_id, entity_type, entity_id, payload) VALUES "
        "(:i, :i, :i, :i, 'x', 'x', :i, 'x', :i, '{}')",
        i=uuid.uuid4(),
    )
    for table in ("tenants", "users", "restaurant_orders", "notifications", "sales"):
        refused(f"SELECT count(*) FROM {table}")
    assert {row.id for row in _rows(owner_engine)} >= {recent}


def test_app_role_cannot_purge(
    team: SimpleNamespace, owner_engine: Engine, app_engine: Engine
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ready_everything(r.preparer.api, order["id"])
    ids = [row.id for row in _rows(owner_engine, order["id"])]
    _age(owner_engine, ids, 45)
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=r.tenant, user_id=uuid.UUID(r.waiter.user_id))
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM member_notifications"))
        db.rollback()
    assert len(_rows(owner_engine, order["id"])) == len(ids)
    assert timedelta(days=30)  # durée documentée (RETENTION_DAYS)


# --- Migration 0045 ------------------------------------------------------------------------------


def test_migration_0045_round_trip_and_requires_the_purge_role(
    team: SimpleNamespace,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = team
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ready_everything(r.preparer.api, order["id"])
    assert _rows(owner_engine, order["id"])
    owner_engine.dispose()
    # Descente permise : notifications éphémères (purgées à 30 jours), aucune donnée métier.
    command.downgrade(_alembic(), "0044")
    with owner_engine.begin() as conn:
        assert conn.execute(text("SELECT to_regclass('member_notifications')")).scalar() is None
        privileges = conn.execute(
            text("SELECT has_schema_privilege('stockmanager_purge', 'public', 'USAGE')")
        ).scalar_one()
        assert privileges is False
    # Rôle de purge absent : montée refusée avec la procédure à suivre.
    get_settings.cache_clear()
    monkeypatch.setenv("SM_DB_PURGE_ROLE", "stockmanager_purge_absent")
    try:
        with pytest.raises(RuntimeError, match="03-purge-role.sh"):
            command.upgrade(_alembic(), "0045")
    finally:
        monkeypatch.delenv("SM_DB_PURGE_ROLE")
        get_settings.cache_clear()
    owner_engine.dispose()
    command.upgrade(_alembic(), "0045")
    with owner_engine.begin() as conn:
        policies = set(
            conn.execute(
                text(
                    "SELECT policyname || ':' || permissive FROM pg_policies "
                    "WHERE tablename = 'member_notifications'"
                )
            ).scalars()
        )
        assert policies == {
            "tenant_isolation:PERMISSIVE",
            "recipient_read:RESTRICTIVE",
            "recipient_update:RESTRICTIVE",
            "purge_read:PERMISSIVE",
            "purge_delete:PERMISSIVE",
        }
        forced = conn.execute(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE relname = 'member_notifications'"
            )
        ).scalar_one()
        assert forced is True
