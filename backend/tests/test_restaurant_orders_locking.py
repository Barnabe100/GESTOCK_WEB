"""Palier R5-B, sous-étapes B-1 et B-2 — ordre des verrous de la création d'une commande (R-2,
AR-1) et du changement ou du retrait de sa table (B-2 : site en partage de clé → table cible en
partage → commande en exclusif).

Toute création (avec ou sans table) prend le site en PARTAGE DE CLÉ avant les réglages du site ;
une reconfiguration (désactivation d'un module, changement de profil) prend le site en EXCLUSIF
avant les réglages. Ces tests rejouent avec le VRAI code (API) les scénarios D1 et D2 du
diagnostic, qui produisaient un interblocage avant B-1, les courses entre une création avec
table et la désactivation des tables ou d'une table, puis celles du changement de table (B-2)
avec la désactivation de la table, le changement de profil, le règlement et la clôture.

Méthode (aucune attente arbitraire) : une transaction « portillon » retient une opération à un
point précis ; l'attente réelle de chaque opération est constatée dans ``pg_locks`` /
``pg_stat_activity`` (verrou non accordé, requête attendue) ; on ouvre le portillon, puis on
vérifie les résultats et l'absence d'erreur (``40P01`` comprise). Ces scénarios ne prouvent pas
l'absence générale d'interblocage : ils couvrent les chemins nommés.
"""

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, text

from app.modules.restaurant_orders.settings import order_sequence_key
from app.shared.clock import utcnow
from tests.conftest import Api
from tests.test_restaurant_orders import _api, _line, _menu, _ok, _order
from tests.test_restaurant_orders_settlement import _cash, _receive, _serve_all, _settle

MAQUIS = "restaurant.maquis"
RESTAURANT = "restaurant.restaurant"
TIMEOUT = 15.0


def _waiting(owner_engine: Engine) -> list[str]:
    """Requêtes en attente d'un verrou (non accordé) dans la base de test."""
    with owner_engine.connect() as conn:
        return [
            str(query)
            for (query,) in conn.execute(
                text(
                    "SELECT DISTINCT a.query FROM pg_stat_activity a "
                    "JOIN pg_locks l ON l.pid = a.pid AND NOT l.granted "
                    "WHERE a.datname = current_database()"
                )
            ).all()
        ]


def _wait_until(owner_engine: Engine, *fragments: str) -> list[str]:
    """Attend (borné) que chaque fragment apparaisse dans une requête en attente de verrou."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        waiting = _waiting(owner_engine)
        if all(any(f in q for q in waiting) for f in fragments):
            return waiting
        time.sleep(0.05)
    raise AssertionError(
        f"attentes non constatées {fragments} ; en attente : {_waiting(owner_engine)}"
    )


class Call:
    """Appel d'API dans un fil ; résultat ``(statut, code)`` ou exception capturée."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.result: tuple[Any, Any] | None = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, args=(fn,))
        self.thread.start()

    def _run(self, fn: Callable[[], Any]) -> None:
        try:
            response = fn()
            body = response.json() if response.content else {}
            self.result = (
                response.status_code,
                body.get("code") if isinstance(body, dict) else None,
            )
        except BaseException as exc:  # noqa: BLE001 — l'erreur (ex. 40P01) est le résultat testé
            self.error = exc

    def join(self) -> tuple[Any, Any]:
        self.thread.join(30)
        assert not self.thread.is_alive(), "l'appel ne s'est pas terminé"
        assert self.error is None, f"erreur inattendue : {self.error!r}"
        assert self.result is not None
        return self.result


@contextmanager
def _gate(owner_engine: Engine, sql: str, **params: Any) -> Iterator[Connection]:
    """Transaction tenant un verrou de ligne jusqu'à la sortie du bloc (validation)."""
    conn = owner_engine.connect()
    tx = conn.begin()
    try:
        rows = conn.execute(text(sql), params).all()
        assert rows, f"portillon sans ligne : {sql}"
        yield conn
    finally:
        tx.commit()
        conn.close()


def _counter_key(site: str) -> str:
    day = utcnow().astimezone(ZoneInfo("Africa/Ouagadougou")).date()
    return order_sequence_key(uuid.UUID(site), day)


COUNTER = "SELECT sequence_key FROM document_sequences WHERE sequence_key = :k FOR UPDATE"
SITE_MODULE = "SELECT id FROM site_modules WHERE site_id = :s AND module_code = :c FOR UPDATE"


def _world(provision: Any, client: TestClient, slug: str, profile: str) -> SimpleNamespace:
    t = provision(slug, profile=profile, plan="ENTREPRISE")
    owner = _api(client, f"owner@{slug}.example.com")
    site = str(t.site_id)
    menu = _menu(owner, site, slug[-3:].upper())
    # Compteur du jour créé (commande annulée : aucune commande ouverte sur le site).
    first = _ok(_order(owner, site, [_line(menu.unit)]), 201)["id"]
    _ok(owner.post(f"/restaurant/orders/{first}/cancel", json={"reason": "Préparation"}))
    return SimpleNamespace(owner=owner, site=site, menu=menu, key=_counter_key(site))


def _disable(owner: Api, site: str, code: str) -> Any:
    return owner.put(f"/sites/{site}/modules/{code}", json={"enabled": False})


def _change_profile(owner: Api, site: str, fingerprint: str) -> Any:
    return owner.put(
        f"/sites/{site}/business-profile",
        json={
            "profile_code": "retail.alimentation",
            "preview_fingerprint": fingerprint,
            "confirmation": "CHANGER DE PROFIL",
        },
    )


def _fingerprint(owner: Api, site: str) -> str:
    preview = owner.get(
        f"/sites/{site}/business-profile/preview", params={"profile_code": "retail.alimentation"}
    )
    return str(_ok(preview)["fingerprint"])


def _state(owner_engine: Engine, site: str, code: str) -> tuple[bool, int]:
    """État final EN BASE : activation du module sur le site, commandes non finales du site."""
    with owner_engine.connect() as conn:
        enabled = conn.execute(
            text("SELECT enabled FROM site_modules WHERE site_id = :s AND module_code = :c"),
            {"s": site, "c": code},
        ).scalar_one()
        open_orders = conn.execute(
            text(
                "SELECT count(*) FROM restaurant_orders WHERE site_id = :s "
                "AND status IN ('OPEN', 'PENDING_CONFIRMATION')"
            ),
            {"s": site},
        ).scalar_one()
    return bool(enabled), int(open_orders)


def _table(owner: Api, site: str, label: str) -> str:
    return str(
        _ok(owner.post("/restaurant/tables", json={"site_id": site, "label": label}), 201)["id"]
    )


# --- D1 / D2 : création SANS table contre reconfiguration des commandes ----------------------


def test_d1_creation_without_table_vs_disabling_orders(
    provision: Any, oclient: TestClient, owner_engine: Engine
) -> None:
    """Création retenue au compteur : elle tient déjà le site (partage de clé) et les réglages ;
    la désactivation attend AU VERROU DU SITE (avant les réglages), puis voit la commande."""
    w = _world(provision, oclient, "rl-d1", MAQUIS)
    with _gate(owner_engine, COUNTER, k=w.key):
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)]))
        _wait_until(owner_engine, "INSERT INTO document_sequences")
        disable = Call(lambda: _disable(w.owner, w.site, "restaurant.orders"))
        _wait_until(owner_engine, "INSERT INTO document_sequences", "FROM sites")
    assert create.join() == (201, None)
    assert disable.join() == (409, "module_has_open_operations")
    # Création normale aboutie ; désactivation refusée : module toujours actif, une commande.
    assert _state(owner_engine, w.site, "restaurant.orders") == (True, 1)


def test_d1_reverse_disabling_first_then_creation_without_table_is_refused(
    provision: Any, oclient: TestClient, owner_engine: Engine
) -> None:
    """Ordre inverse : la désactivation tient le site (retenue sur sa ligne ``site_modules``) ;
    la création attend dès son PREMIER verrou (site, partage de clé), sans interblocage, puis
    recalcule l'effectivité du module APRÈS ce verrou : le module vient d'être désactivé, la
    commande n'est pas créée."""
    w = _world(provision, oclient, "rl-d1r", MAQUIS)
    with _gate(owner_engine, SITE_MODULE, s=w.site, c="restaurant.orders"):
        disable = Call(lambda: _disable(w.owner, w.site, "restaurant.orders"))
        _wait_until(owner_engine, "FROM site_modules")
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)]))
        _wait_until(owner_engine, "FROM site_modules", "FOR KEY SHARE")
    assert disable.join() == (204, None)
    assert create.join() == (403, "module_unavailable")
    # Aucune commande ouverte indésirable : module désactivé, zéro commande en cours.
    assert _state(owner_engine, w.site, "restaurant.orders") == (False, 0)


def test_d1_reverse_disabling_first_then_creation_with_table_is_refused(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Même scénario avec une table (registre de test : commandes et tables disponibles). Les
    tables, qui dépendent des commandes, sont d'abord désactivées (sinon la désactivation des
    commandes est refusée : ``module_has_dependents``) ; la création portant une table attend
    au verrou du site, puis est refusée parce que les commandes ne sont plus effectives."""
    w = _world(provision, tclient, "rl-d1rt", RESTAURANT)
    table = _table(w.owner, w.site, "T1")
    assert _disable(w.owner, w.site, "restaurant.tables").status_code == 204
    with _gate(owner_engine, SITE_MODULE, s=w.site, c="restaurant.orders"):
        disable = Call(lambda: _disable(w.owner, w.site, "restaurant.orders"))
        _wait_until(owner_engine, "FROM site_modules")
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)], table_id=table))
        _wait_until(owner_engine, "FROM site_modules", "FOR KEY SHARE")
    assert disable.join() == (204, None)
    assert create.join() == (403, "module_unavailable")
    assert _state(owner_engine, w.site, "restaurant.orders") == (False, 0)
    with owner_engine.connect() as conn:
        attached = conn.execute(
            text("SELECT count(*) FROM restaurant_orders WHERE table_id = :t"), {"t": table}
        ).scalar_one()
    assert attached == 0


def test_d2_creation_without_table_vs_profile_change(
    provision: Any, oclient: TestClient, owner_engine: Engine
) -> None:
    w = _world(provision, oclient, "rl-d2", MAQUIS)
    fingerprint = _fingerprint(w.owner, w.site)
    with _gate(owner_engine, COUNTER, k=w.key):
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)]))
        _wait_until(owner_engine, "INSERT INTO document_sequences")
        change = Call(lambda: _change_profile(w.owner, w.site, fingerprint))
        _wait_until(owner_engine, "INSERT INTO document_sequences", "FROM sites")
    assert create.join() == (201, None)
    # Une commande ouverte existe désormais : l'aperçu est périmé ou le changement bloqué.
    status, code = change.join()
    assert status == 409 and code in ("profile_preview_outdated", "profile_change_blocked")


def test_d2_creation_with_table_vs_profile_change(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    w = _world(provision, tclient, "rl-d2t", RESTAURANT)
    table = _table(w.owner, w.site, "T1")
    fingerprint = _fingerprint(w.owner, w.site)
    with _gate(owner_engine, COUNTER, k=w.key):
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)], table_id=table))
        _wait_until(owner_engine, "INSERT INTO document_sequences")
        change = Call(lambda: _change_profile(w.owner, w.site, fingerprint))
        _wait_until(owner_engine, "INSERT INTO document_sequences", "FROM sites")
    assert create.join() == (201, None)
    status, code = change.join()
    assert status == 409 and code in ("profile_preview_outdated", "profile_change_blocked")


# --- Création AVEC table contre désactivation des tables ou d'une table ----------------------


def test_creation_with_table_first_then_disabling_tables(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    w = _world(provision, tclient, "rl-t1", RESTAURANT)
    table = _table(w.owner, w.site, "T1")
    with _gate(owner_engine, COUNTER, k=w.key):
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)], table_id=table))
        _wait_until(owner_engine, "INSERT INTO document_sequences")
        disable = Call(lambda: _disable(w.owner, w.site, "restaurant.tables"))
        _wait_until(owner_engine, "INSERT INTO document_sequences", "FROM sites")
    assert create.join() == (201, None)
    # La commande est rattachée à une table : la désactivation est refusée (A6).
    assert disable.join() == (409, "module_has_open_operations")


def test_disabling_tables_first_then_creation_with_table_is_refused(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """La création attend le verrou du site, puis RECALCULE les capacités : les tables ne sont
    plus effectives, la table n'est pas rattachée."""
    w = _world(provision, tclient, "rl-t2", RESTAURANT)
    table = _table(w.owner, w.site, "T1")
    with _gate(owner_engine, SITE_MODULE, s=w.site, c="restaurant.tables"):
        disable = Call(lambda: _disable(w.owner, w.site, "restaurant.tables"))
        _wait_until(owner_engine, "FROM site_modules")
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)], table_id=table))
        _wait_until(owner_engine, "FROM site_modules", "FOR KEY SHARE")
    assert disable.join() == (204, None)
    assert create.join() == (422, "tables_unavailable")


def test_creation_with_table_first_then_deactivating_the_table(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    w = _world(provision, tclient, "rl-t3", RESTAURANT)
    table = _table(w.owner, w.site, "T1")
    with _gate(owner_engine, COUNTER, k=w.key):
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)], table_id=table))
        _wait_until(owner_engine, "INSERT INTO document_sequences")
        deactivate = Call(lambda: w.owner.post(f"/restaurant/tables/{table}/deactivate"))
        _wait_until(owner_engine, "INSERT INTO document_sequences", "FROM restaurant_tables")
    assert create.join() == (201, None)
    assert deactivate.join() == (409, "table_has_open_orders")


def test_deactivating_the_table_first_then_creation_is_refused(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Désactivation en cours (table tenue en exclusif, comme le service de R5-A) : la création
    attend au verrou de la table, puis la relit inactive."""
    w = _world(provision, tclient, "rl-t4", RESTAURANT)
    table = _table(w.owner, w.site, "T1")
    with _gate(
        owner_engine, "SELECT id FROM restaurant_tables WHERE id = :t FOR UPDATE", t=table
    ) as gate:
        gate.execute(
            text("UPDATE restaurant_tables SET is_active = false WHERE id = :t"), {"t": table}
        )
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)], table_id=table))
        _wait_until(owner_engine, "FROM restaurant_tables")
    assert create.join() == (422, "table_inactive")


@pytest.mark.parametrize("profile", [MAQUIS])
def test_order_lock_sequence_is_site_then_settings(
    provision: Any, oclient: TestClient, owner_engine: Engine, profile: str
) -> None:
    """Ordre réel constaté : réglages tenus en exclusif (comme une reconfiguration) — la
    création attend AUX RÉGLAGES, donc APRÈS avoir obtenu le site (partage de clé)."""
    w = _world(provision, oclient, "rl-seq", profile)
    with _gate(
        owner_engine,
        "SELECT id FROM restaurant_site_settings WHERE site_id = :s FOR UPDATE",
        s=w.site,
    ):
        create = Call(lambda: _order(w.owner, w.site, [_line(w.menu.unit)]))
        _wait_until(owner_engine, "FROM restaurant_site_settings")
        with owner_engine.connect() as conn:
            held = conn.execute(
                text(
                    "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                    "WHERE a.query LIKE '%restaurant_site_settings%' AND l.granted "
                    "AND l.locktype = 'relation' AND l.relation = 'sites'::regclass "
                    "AND l.mode = 'RowShareLock'"
                )
            ).scalar_one()
        assert held >= 1, "la création attend les réglages sans tenir le site"
    assert create.join() == (201, None)


# --- B-2 : changement et retrait de table ------------------------------------------------------

ORDER_ROW = "SELECT id FROM restaurant_orders WHERE id = :o FOR UPDATE"
STOCK_LEVEL = "SELECT id FROM stock_levels WHERE site_id = :s AND article_id = :a FOR UPDATE"


def _waiters(owner_engine: Engine, fragment: str) -> int:
    """Nombre de sessions en attente d'un verrou dont la requête contient ``fragment``."""
    with owner_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(DISTINCT a.pid) FROM pg_stat_activity a "
                    "JOIN pg_locks l ON l.pid = a.pid AND NOT l.granted "
                    "WHERE a.datname = current_database() AND a.query LIKE :f"
                ),
                {"f": f"%{fragment}%"},
            ).scalar_one()
        )


def _wait_for_waiters(owner_engine: Engine, fragment: str, count: int) -> None:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if _waiters(owner_engine, fragment) >= count:
            return
        time.sleep(0.05)
    raise AssertionError(f"{count} attente(s) non constatée(s) sur {fragment}")


def _set_table(owner: Api, order: str, table: str | None) -> Any:
    return owner.put(f"/restaurant/orders/{order}/table", json={"table_id": table})


def _order_row(owner_engine: Engine, order: str) -> tuple[str | None, str, str]:
    """État final EN BASE : table, statut et état de règlement de la commande."""
    with owner_engine.connect() as conn:
        table, status, settlement = conn.execute(
            text("SELECT table_id, status, settlement_status FROM restaurant_orders WHERE id = :o"),
            {"o": order},
        ).one()
    return (None if table is None else str(table)), str(status), str(settlement)


def _table_changes(owner_engine: Engine, order: str) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM restaurant_order_events "
                    "WHERE order_id = :o AND event_type = 'TABLE_CHANGED'"
                ),
                {"o": order},
            ).scalar_one()
        )


def _stocked(provision: Any, client: TestClient, slug: str) -> SimpleNamespace:
    """Restaurant (commandes et tables disponibles), stock de l'article du menu, deux tables."""
    w = _world(provision, client, slug, RESTAURANT)
    supplier = _ok(w.owner.post("/suppliers", json={"name": "Brasserie"}), 201)["id"]
    _receive(SimpleNamespace(owner=w.owner, site=w.site, supplier=supplier), w.menu.article, "50")
    w.t1 = _table(w.owner, w.site, "T1")
    w.t2 = _table(w.owner, w.site, "T2")
    return w


def test_table_change_first_then_deactivating_the_table(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Le changement tient la table cible (partage) en attendant la commande ; la désactivation
    attend la table (exclusif), puis compte la commande désormais rattachée : refus (A6)."""
    w = _stocked(provision, tclient, "rl-b2a")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)]), 201)["id"]
    with _gate(owner_engine, ORDER_ROW, o=order):
        change = Call(lambda: _set_table(w.owner, order, w.t1))
        _wait_until(owner_engine, "FROM restaurant_orders")
        deactivate = Call(lambda: w.owner.post(f"/restaurant/tables/{w.t1}/deactivate"))
        _wait_until(owner_engine, "FROM restaurant_orders", "FROM restaurant_tables")
    assert change.join() == (200, None)
    assert deactivate.join() == (409, "table_has_open_orders")
    assert _order_row(owner_engine, order)[0] == w.t1
    with owner_engine.connect() as conn:
        active = conn.execute(
            text("SELECT is_active FROM restaurant_tables WHERE id = :t"), {"t": w.t1}
        ).scalar_one()
    assert active is True


def test_deactivating_the_table_first_then_table_change_is_refused(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Désactivation en cours (table tenue en exclusif) : le changement attend au verrou de la
    table, la relit inactive, et la commande garde sa table."""
    w = _stocked(provision, tclient, "rl-b2b")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)], table_id=w.t1), 201)["id"]
    with _gate(
        owner_engine, "SELECT id FROM restaurant_tables WHERE id = :t FOR UPDATE", t=w.t2
    ) as gate:
        gate.execute(
            text("UPDATE restaurant_tables SET is_active = false WHERE id = :t"), {"t": w.t2}
        )
        change = Call(lambda: _set_table(w.owner, order, w.t2))
        _wait_until(owner_engine, "FROM restaurant_tables")
    assert change.join() == (422, "table_inactive")
    assert _order_row(owner_engine, order)[0] == w.t1
    assert _table_changes(owner_engine, order) == 0


def test_removal_does_not_wait_for_the_deactivation_of_the_previous_table(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Le retrait ne verrouille aucune table : la désactivation de l'ancienne table, lancée
    pendant le retrait non validé, voit encore la commande et est refusée sans attendre ;
    ensuite, la table se désactive."""
    w = _stocked(provision, tclient, "rl-b2c")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)], table_id=w.t1), 201)["id"]
    with _gate(owner_engine, ORDER_ROW, o=order):
        removal = Call(lambda: _set_table(w.owner, order, None))
        _wait_until(owner_engine, "FROM restaurant_orders")
        refused = w.owner.post(f"/restaurant/tables/{w.t1}/deactivate")
        assert (refused.status_code, refused.json()["code"]) == (409, "table_has_open_orders")
    assert removal.join() == (200, None)
    assert _order_row(owner_engine, order)[0] is None
    _ok(w.owner.post(f"/restaurant/tables/{w.t1}/deactivate"))


def test_table_change_first_then_profile_change(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Le changement tient le site (partage de clé) : le changement de profil attend AU VERROU
    DU SITE, puis voit la commande rattachée (empreinte des tables modifiée) : refus."""
    w = _stocked(provision, tclient, "rl-b2d")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)]), 201)["id"]
    fingerprint = _fingerprint(w.owner, w.site)
    with _gate(owner_engine, ORDER_ROW, o=order):
        change = Call(lambda: _set_table(w.owner, order, w.t1))
        _wait_until(owner_engine, "FROM restaurant_orders")
        profile = Call(lambda: _change_profile(w.owner, w.site, fingerprint))
        _wait_until(owner_engine, "FROM restaurant_orders", "FROM sites")
    assert change.join() == (200, None)
    status, code = profile.join()
    assert status == 409 and code in ("profile_preview_outdated", "profile_change_blocked")
    assert _order_row(owner_engine, order)[0] == w.t1
    assert _state(owner_engine, w.site, "restaurant.tables") == (True, 1)


def test_profile_change_first_then_table_changes(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Changement de profil en cours (site exclusif, retenu sur la ligne ``site_modules`` des
    tables) : association et retrait attendent AU VERROU DU SITE, sans interblocage, puis
    s'appliquent avec les modules RECALCULÉS (profil de restauration : tables conservées). Un
    profil arrêtant les commandes est refusé tant qu'une commande est ouverte (constaté :
    ``profile_change_blocked``) ; or seule une commande ouverte change de table."""
    w = _stocked(provision, tclient, "rl-b2e")
    seated = _ok(_order(w.owner, w.site, [_line(w.menu.unit)], table_id=w.t1), 201)["id"]
    bare = _ok(_order(w.owner, w.site, [_line(w.menu.unit)]), 201)["id"]
    stopping = _fingerprint(w.owner, w.site)
    assert _change_profile(w.owner, w.site, stopping).json()["code"] == "profile_change_blocked"
    target = "restaurant.fast_food"
    preview = w.owner.get(
        f"/sites/{w.site}/business-profile/preview", params={"profile_code": target}
    )
    body = {
        "profile_code": target,
        "preview_fingerprint": _ok(preview)["fingerprint"],
        "confirmation": "CHANGER DE PROFIL",
    }
    with _gate(owner_engine, SITE_MODULE, s=w.site, c="restaurant.tables"):
        profile = Call(lambda: w.owner.put(f"/sites/{w.site}/business-profile", json=body))
        _wait_until(owner_engine, "FROM site_modules")
        association = Call(lambda: _set_table(w.owner, bare, w.t2))
        removal = Call(lambda: _set_table(w.owner, seated, None))
        _wait_for_waiters(owner_engine, "FOR KEY SHARE", 2)
    assert profile.join() == (200, None)
    assert association.join() == (200, None)
    assert removal.join() == (200, None)
    assert _order_row(owner_engine, bare)[0] == w.t2
    assert _order_row(owner_engine, seated)[0] is None
    assert _state(owner_engine, w.site, "restaurant.tables") == (True, 2)


def test_settlement_first_then_table_change(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Le règlement tient la commande (exclusif) et attend au stock ; le changement attend la
    commande, puis s'applique à la commande réglée, encore ouverte (non servie)."""
    w = _stocked(provision, tclient, "rl-b2f")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)], table_id=w.t1), 201)["id"]
    with _gate(owner_engine, STOCK_LEVEL, s=w.site, a=w.menu.article):
        settle = Call(lambda: _settle(w.owner, order, payments=_cash("700")))
        _wait_until(owner_engine, "FROM stock_levels")
        change = Call(lambda: _set_table(w.owner, order, w.t2))
        _wait_until(owner_engine, "FROM stock_levels", "FROM restaurant_orders")
    assert settle.join() == (201, None)
    assert change.join() == (200, None)
    assert _order_row(owner_engine, order) == (w.t2, "OPEN", "SETTLED")


def test_table_change_first_then_settlement(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Les deux attendent la commande, le changement en premier : il s'applique, puis le
    règlement aboutit sur la commande déplacée."""
    w = _stocked(provision, tclient, "rl-b2g")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)], table_id=w.t1), 201)["id"]
    with _gate(owner_engine, ORDER_ROW, o=order):
        change = Call(lambda: _set_table(w.owner, order, w.t2))
        _wait_for_waiters(owner_engine, "FROM restaurant_orders", 1)
        settle = Call(lambda: _settle(w.owner, order, payments=_cash("700")))
        _wait_for_waiters(owner_engine, "FROM restaurant_orders", 2)
    assert change.join() == (200, None)
    assert settle.join() == (201, None)
    assert _order_row(owner_engine, order) == (w.t2, "OPEN", "SETTLED")


def test_closure_first_then_table_change_is_refused(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Commande servie : son règlement la CLÔT. Il tient la commande en attendant le stock ; le
    changement attend la commande, puis la trouve close : refus, table inchangée."""
    w = _stocked(provision, tclient, "rl-b2h")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)], table_id=w.t1), 201)["id"]
    _serve_all(w.owner, order)
    with _gate(owner_engine, STOCK_LEVEL, s=w.site, a=w.menu.article):
        settle = Call(lambda: _settle(w.owner, order, payments=_cash("700")))
        _wait_until(owner_engine, "FROM stock_levels")
        change = Call(lambda: _set_table(w.owner, order, w.t2))
        _wait_until(owner_engine, "FROM stock_levels", "FROM restaurant_orders")
    assert settle.join() == (201, None)
    assert change.join() == (409, "order_closed")
    assert _order_row(owner_engine, order) == (w.t1, "CLOSED", "SETTLED")
    assert _table_changes(owner_engine, order) == 0


def test_table_change_first_then_closure(
    provision: Any, tclient: TestClient, owner_engine: Engine
) -> None:
    """Commande réglée non servie : le changement passe avant le service qui la clôt ; la
    commande close garde la nouvelle table."""
    w = _stocked(provision, tclient, "rl-b2i")
    order = _ok(_order(w.owner, w.site, [_line(w.menu.unit)], table_id=w.t1), 201)["id"]
    _ok(_settle(w.owner, order, payments=_cash("700")), 201)
    for action in ("start", "ready"):
        _ok(w.owner.post(f"/restaurant/orders/{order}/{action}"))
    with _gate(owner_engine, ORDER_ROW, o=order):
        change = Call(lambda: _set_table(w.owner, order, w.t2))
        _wait_for_waiters(owner_engine, "FROM restaurant_orders", 1)
        serve = Call(lambda: w.owner.post(f"/restaurant/orders/{order}/serve"))
        _wait_for_waiters(owner_engine, "FROM restaurant_orders", 2)
    assert change.join() == (200, None)
    assert serve.join() == (200, None)
    assert _order_row(owner_engine, order) == (w.t2, "CLOSED", "SETTLED")
    assert _table_changes(owner_engine, order) == 1
