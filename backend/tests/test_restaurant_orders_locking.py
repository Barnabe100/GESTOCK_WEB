"""Palier R5-B, sous-étape B-1 — ordre des verrous de la création d'une commande (R-2, AR-1).

Toute création (avec ou sans table) prend le site en PARTAGE DE CLÉ avant les réglages du site ;
une reconfiguration (désactivation d'un module, changement de profil) prend le site en EXCLUSIF
avant les réglages. Ces tests rejouent avec le VRAI code (API) les scénarios D1 et D2 du
diagnostic, qui produisaient un interblocage avant B-1, et les courses entre une création avec
table et la désactivation des tables ou d'une table.

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
