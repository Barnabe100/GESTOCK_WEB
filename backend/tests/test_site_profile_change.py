"""Changement du profil d'UN site (palier D, ADR-0048).

Niveaux SIMPLE / STRONG / BLOCKED calculés par le serveur ; l'historique seul ne bloque jamais ;
une opération en cours ne bloque que si elle devient impossible (session de caisse ouverte vers
un profil sans caisse). Seuls ``sites.business_profile_code`` du site ciblé et ses
``site_modules`` changent ; aucune donnée n'est supprimée ; aperçu sans écriture ; empreinte
recalculée sous verrou (``409 profile_preview_outdated``) ; refus BLOCKED journalisé.
"""

import json
import threading
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, add_site, login, set_site_module
from tests.stock_helpers import World

ENTREPOT = "distribution.entrepot"  # seul profil sans caisse ni point de vente
MAQUIS = "restaurant.maquis"  # modules restaurant (planifiés), caisse conservée
ORIGIN = "retail.quincaillerie"
CONFIRM = "CHANGER DE PROFIL"


# --- Aides ---------------------------------------------------------------------------------------


def _preview(api: Api, site: str, code: str) -> Any:
    return api.get(f"/sites/{site}/business-profile/preview", params={"profile_code": code})


def _ok_preview(api: Api, site: str, code: str) -> dict[str, Any]:
    response = _preview(api, site, code)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _change(
    api: Api, site: str, code: str, fingerprint: str, confirmation: str | None = None
) -> Any:
    body: dict[str, Any] = {"profile_code": code, "preview_fingerprint": fingerprint}
    if confirmation is not None:
        body["confirmation"] = confirmation
    return api.put(f"/sites/{site}/business-profile", json=body)


def _apply(api: Api, site: str, code: str) -> Any:
    preview = _ok_preview(api, site, code)
    confirmation = CONFIRM if preview["level"] == "STRONG" else None
    return _change(api, site, code, preview["fingerprint"], confirmation)


def _profile(owner_db: Session, site: str) -> str:
    owner_db.expire_all()
    return str(
        owner_db.execute(
            text("SELECT business_profile_code FROM sites WHERE id = :s"), {"s": site}
        ).scalar_one()
    )


def _activations(owner_db: Session, site: str) -> dict[str, bool]:
    owner_db.expire_all()
    return {
        row[0]: row[1]
        for row in owner_db.execute(
            text("SELECT module_code, enabled FROM site_modules WHERE site_id = :s"), {"s": site}
        )
    }


def _audits(owner_db: Session, action: str) -> list[dict[str, Any]]:
    owner_db.expire_all()
    rows = owner_db.execute(
        text("SELECT data FROM audit_logs WHERE action = :a ORDER BY occurred_at, id"),
        {"a": action},
    )
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


def _counts(owner_db: Session) -> dict[str, int]:
    tables = (
        "sales",
        "payments",
        "stock_movements",
        "stock_entries",
        "stock_levels",
        "inventories",
        "cash_sessions",
        "catalog_site_articles",
        "site_modules",
    )
    return {t: sh.count(owner_db, f"SELECT count(*) FROM {t}") for t in tables}


def _modules_of(preview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["code"]: m for m in preview["modules"]}


def _kinds(items: list[dict[str, Any]]) -> set[str]:
    return {i["kind"] for i in items}


def _caps_modules(api: Api, site: str) -> set[str]:
    scoped = Api(api.client, api.token, uuid.UUID(site))
    return {m["code"] for m in scoped.get("/me/capabilities").json()["modules"]}


def _sale(w: World, quantity: str = "1") -> dict[str, Any]:
    response = w.owner.post(
        "/sales",
        json={
            "site_id": w.site,
            "customer_id": sh.credit_customer(w),
            "lines": [{"article_id": w.articles[0], "quantity": quantity}],
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _validated_sale(w: World) -> dict[str, Any]:
    sale = _sale(w)
    response = w.owner.post(f"/sales/{sale['id']}/validate")
    assert response.status_code == 200, response.text
    return dict(response.json())


# --- Aperçu et changement SIMPLE ---------------------------------------------------------------


def test_preview_writes_nothing(world: World, owner_db: Session) -> None:
    before = _counts(owner_db)
    audits = sh.count(owner_db, "SELECT count(*) FROM audit_logs")
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    assert preview["level"] == "SIMPLE"
    assert preview["current_profile"]["code"] == ORIGIN
    assert preview["target_profile"]["code"] == MAQUIS
    assert preview["confirmation_text"] is None
    assert preview["assortment_unchanged"] is True
    assert len(preview["fingerprint"]) == 64
    # Configuration du site affichée, sans élever le niveau (assortiment explicite).
    assert "assortment_articles" in _kinds(preview["configuration"])
    assert _counts(owner_db) == before
    assert sh.count(owner_db, "SELECT count(*) FROM audit_logs") == audits
    assert _profile(owner_db, world.site) == ORIGIN


def test_simple_change_touches_only_the_target_site(world: World, owner_db: Session) -> None:
    other_before = _activations(owner_db, world.site2)
    origin = sh.count(owner_db, "SELECT count(*) FROM tenants WHERE business_profile_code = 'x'")
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    modules = _modules_of(preview)
    assert modules["restaurant.orders"]["change"] == "added"
    assert modules["restaurant.orders"]["action"] == "enable"
    assert modules["restaurant.tables"]["action"] == "none"  # facultatif pour un maquis (D11)
    assert modules["restaurant.qr"]["action"] == "none"  # facultatif : reste désactivé
    assert modules["restaurant.qr"]["reason"] == "optional"
    assert modules["pos"]["change"] == "kept" and modules["pos"]["action"] == "none"
    response = _change(world.owner, world.site, MAQUIS, preview["fingerprint"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["previous_profile"] == ORIGIN
    assert body["business_profile_code"] == MAQUIS
    assert body["level"] == "SIMPLE"
    assert "restaurant.orders" in body["activated"]
    assert _profile(owner_db, world.site) == MAQUIS
    assert _profile(owner_db, world.site2) == ORIGIN  # autre site inchangé
    assert _activations(owner_db, world.site2) == other_before
    # Profil d'origine du tenant jamais modifié.
    assert (
        sh.count(
            owner_db,
            f"SELECT count(*) FROM tenants WHERE business_profile_code = '{ORIGIN}'",
        )
        == 1
    )
    assert origin == 0
    activations = _activations(owner_db, world.site)
    assert activations["restaurant.orders"] is True
    assert activations["restaurant.tables"] is False  # facultatif (D11)
    assert activations["restaurant.qr"] is False
    assert activations["pos"] is True
    # Capacités du site : nouveau profil exposé, modules recalculés.
    site_caps = Api(world.owner.client, world.owner.token, uuid.UUID(world.site)).get(
        "/me/capabilities"
    )
    caps = site_caps.json()
    assert caps["profile"]["code"] == MAQUIS
    assert {"restaurant.orders", "pos"} <= {m["code"] for m in caps["modules"]}
    entry = next(s for s in caps["sites"] if s["id"] == world.site)
    assert entry["profile"]["code"] == MAQUIS
    # Audit complet du changement.
    [audit] = _audits(owner_db, "site.profile_changed")
    assert audit["previous_profile"] == ORIGIN and audit["profile"] == MAQUIS
    assert audit["level"] == "SIMPLE" and audit["confirmed"] is False
    assert audit["fingerprint"] == preview["fingerprint"]
    changed = {m["code"]: m for m in audit["site_modules"]}
    assert changed["restaurant.orders"] == {
        "code": "restaurant.orders",
        "previous": None,
        "enabled": True,
        "reason": "default",
    }
    # Salle facultative pour un maquis (D11) : ligne écrite désactivée.
    assert changed["restaurant.tables"] == {
        "code": "restaurant.tables",
        "previous": None,
        "enabled": False,
        "reason": "optional",
    }


def test_removed_modules_are_deactivated_never_deleted(world: World, owner_db: Session) -> None:
    rows = sh.count(owner_db, f"SELECT count(*) FROM site_modules WHERE site_id = '{world.site}'")
    preview = _ok_preview(world.owner, world.site, ENTREPOT)
    modules = _modules_of(preview)
    assert modules["pos"]["change"] == "removed"
    assert modules["pos"]["action"] == "disable"
    assert modules["pos"]["after"]["effective"] is False
    assert modules["cash_register"]["reason"] == "removed_from_profile"
    assert set(preview["summary"]["removed"]) == {"pos", "cash_register"}
    assert preview["level"] == "SIMPLE"
    assert _change(world.owner, world.site, ENTREPOT, preview["fingerprint"]).status_code == 200
    activations = _activations(owner_db, world.site)
    assert activations["pos"] is False and activations["cash_register"] is False
    assert (
        sh.count(owner_db, f"SELECT count(*) FROM site_modules WHERE site_id = '{world.site}'")
        == rows
    )  # aucune ligne supprimée
    assert not {"pos", "cash_register"} & _caps_modules(world.owner, world.site)
    assert {"pos", "cash_register"} <= _caps_modules(world.owner, world.site2)
    # Retour au profil d'origine : modules nouvellement proposés → défauts du profil.
    back = _ok_preview(world.owner, world.site, ORIGIN)
    assert _modules_of(back)["pos"]["action"] == "enable"
    assert _change(world.owner, world.site, ORIGIN, back["fingerprint"]).status_code == 200
    assert _activations(owner_db, world.site)["pos"] is True


def test_kept_modules_keep_the_site_choice(world: World, owner_db: Session) -> None:
    assert set_site_module(world.owner, world.site, "alerts", False).status_code == 204
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    assert _modules_of(preview)["alerts"]["action"] == "none"
    assert _change(world.owner, world.site, MAQUIS, preview["fingerprint"]).status_code == 200
    assert _activations(owner_db, world.site)["alerts"] is False


# --- STRONG : l'historique seul ne bloque jamais -------------------------------------------------


def test_history_makes_strong_never_blocked(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "50", "100")])
    for _ in range(3):
        _validated_sale(world)
    before = _counts(owner_db)
    preview = _ok_preview(world.owner, world.site, ENTREPOT)
    assert preview["level"] == "STRONG"
    assert preview["blockers"] == []
    assert preview["confirmation_text"] == CONFIRM
    kinds = _kinds(preview["history"])
    assert {"sales_validated", "stock_movements", "stock_entries", "stock_levels"} <= kinds
    sales = next(i for i in preview["history"] if i["kind"] == "sales_validated")
    assert (sales["count"], sales["capped"], sales["module"]) == (3, False, "sales")
    # Sans confirmation, puis texte incorrect : refus, rien ne change.
    missing = _change(world.owner, world.site, ENTREPOT, preview["fingerprint"])
    assert (missing.status_code, missing.json()["code"]) == (
        422,
        "profile_change_confirmation_required",
    )
    for wrong in ("changer de profil", " CHANGER DE PROFIL", "CHANGER DE PROFIL ", "OUI"):
        refused = _change(world.owner, world.site, ENTREPOT, preview["fingerprint"], wrong)
        assert (refused.status_code, refused.json()["code"]) == (
            422,
            "profile_change_confirmation_invalid",
        )
    assert _profile(owner_db, world.site) == ORIGIN
    ok = _change(world.owner, world.site, ENTREPOT, preview["fingerprint"], CONFIRM)
    assert ok.status_code == 200, ok.text
    assert ok.json()["level"] == "STRONG"
    # Aucune donnée supprimée ni transformée (seules les activations changent de valeur).
    assert _counts(owner_db) == before
    [audit] = _audits(owner_db, "site.profile_changed")
    assert audit["level"] == "STRONG" and audit["confirmed"] is True
    assert audit["history"]["sales.sales_validated"] == 3


@pytest.mark.parametrize(
    "build, kind",
    [
        ("draft_sale", "sales_draft"),
        ("draft_entry", "stock_entries_draft"),
        ("draft_transfer", "stock_transfers_draft"),
        ("open_inventory", "inventories_open"),
        ("closed_cash", "cash_sessions_closed"),
        ("transfer_in", "stock_transfers"),
    ],
)
def test_each_kind_of_data_makes_strong(
    world: World, owner_db: Session, build: str, kind: str
) -> None:
    site = world.site
    if build == "draft_sale":
        _sale(world)
    elif build == "draft_entry":
        sh.entry(world, [(0, "5", "100")])
    elif build == "draft_transfer":
        sh.validated_entry(world, [(0, "5", "100")])
        site = world.site2  # le site destination voit aussi le transfert
        response = world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site,
                "destination_site_id": world.site2,
                "lines": [{"article_id": world.articles[0], "quantity": "1"}],
            },
        )
        assert response.status_code == 201, response.text
    elif build == "open_inventory":
        response = world.owner.post(
            "/inventories",
            json={"site_id": site, "inventory_type": "TARGETED", "article_ids": world.articles[:1]},
        )
        assert response.status_code == 201, response.text
    elif build == "closed_cash":
        session = sh.open_cash(world)
        closed = world.owner.post(
            f"/cash/sessions/{session['id']}/close", json={"counted_balance": "0"}
        )
        assert closed.status_code == 200, closed.text
    elif build == "transfer_in":
        sh.validated_entry(world, [(0, "5", "100")])
        site = world.site2
        response = world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site,
                "destination_site_id": world.site2,
                "lines": [{"article_id": world.articles[0], "quantity": "1"}],
            },
        )
        assert response.status_code == 201, response.text
        validated = world.owner.post(f"/stock/transfers/{response.json()['id']}/validate")
        assert validated.status_code == 200, validated.text
    preview = _ok_preview(world.owner, site, MAQUIS)
    assert preview["level"] == "STRONG", preview
    assert kind in _kinds(preview["history"]) | _kinds(preview["open_operations"])
    assert preview["blockers"] == []


# --- BLOCKED : opération en cours devenue impossible ---------------------------------------------


def test_open_cash_session_blocks_a_profile_without_cash(world: World, owner_db: Session) -> None:
    session = sh.open_cash(world)
    preview = _ok_preview(world.owner, world.site, ENTREPOT)
    assert preview["level"] == "BLOCKED"
    assert preview["confirmation_text"] is None
    assert preview["blockers"] == [
        {
            "kind": "cash_sessions_open",
            "module": "cash_register",
            "count": 1,
            "capped": False,
            "blocking": True,
        }
    ]
    before = _counts(owner_db)
    refused = _change(world.owner, world.site, ENTREPOT, preview["fingerprint"], CONFIRM)
    assert (refused.status_code, refused.json()["code"]) == (409, "profile_change_blocked")
    assert refused.json()["blockers"][0]["kind"] == "cash_sessions_open"
    assert _profile(owner_db, world.site) == ORIGIN
    assert _counts(owner_db) == before
    assert _audits(owner_db, "site.profile_changed") == []
    [refusal] = _audits(owner_db, "site.profile_change_refused")
    assert refusal["changed"] is False
    assert refusal["requested_profile"] == ENTREPOT
    assert refusal["blockers"] == {"cash_register.cash_sessions_open": 1}
    # Session clôturée : plus de blocage (aucun mouvement : rien d'historique en caisse).
    closed = world.owner.post(
        f"/cash/sessions/{session['id']}/close", json={"counted_balance": "0"}
    )
    assert closed.status_code == 200, closed.text
    assert _apply(world.owner, world.site, ENTREPOT).status_code == 200


def test_open_cash_session_with_kept_cash_follows_general_rules(
    world: World, owner_db: Session
) -> None:
    sh.open_cash(world)  # fond initial nul : aucun mouvement de caisse
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    # Caisse conservée : session affichée, ni bloquante ni suffisante pour STRONG.
    assert preview["level"] == "SIMPLE"
    [item] = [i for i in preview["open_operations"] if i["kind"] == "cash_sessions_open"]
    assert item["blocking"] is False
    # Avec un fond initial (mouvement de caisse = historique) : STRONG.
    sh.open_cash(world, opening_float="5000", name="Caisse 2")
    assert _ok_preview(world.owner, world.site, MAQUIS)["level"] == "STRONG"


def test_manual_cash_register_disable_with_open_session_is_refused(
    world: World, owner_db: Session
) -> None:
    session = sh.open_cash(world)
    refused = set_site_module(world.owner, world.site, "cash_register", False)
    assert (refused.status_code, refused.json()["code"]) == (409, "module_has_open_operations")
    assert refused.json()["operations"] == {"cash_sessions_open": 1}
    assert _activations(owner_db, world.site)["cash_register"] is True
    # Autre site sans session : désactivation permise.
    assert set_site_module(world.owner, world.site2, "cash_register", False).status_code == 204
    closed = world.owner.post(
        f"/cash/sessions/{session['id']}/close", json={"counted_balance": "0"}
    )
    assert closed.status_code == 200
    assert set_site_module(world.owner, world.site, "cash_register", False).status_code == 204


# --- Aperçu obsolète et concurrence ---------------------------------------------------------------


def test_preview_outdated_when_level_changes(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    preview = _ok_preview(world.owner, world.site2, MAQUIS)  # dépôt vide : SIMPLE
    assert preview["level"] == "SIMPLE"
    response = world.owner.post(
        "/stock/transfers",
        json={
            "source_site_id": world.site,
            "destination_site_id": world.site2,
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        },
    )
    assert response.status_code == 201
    stale = _change(world.owner, world.site2, MAQUIS, preview["fingerprint"])
    assert (stale.status_code, stale.json()["code"]) == (409, "profile_preview_outdated")
    assert _profile(owner_db, world.site2) == ORIGIN


def test_more_history_does_not_outdate_a_strong_preview(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    _validated_sale(world)
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    _validated_sale(world)  # compteurs différents, même décision
    assert _change(
        world.owner, world.site, MAQUIS, preview["fingerprint"], CONFIRM
    ).status_code == (200)


def test_preview_outdated_after_module_toggle_or_other_change(world: World) -> None:
    preview = _ok_preview(world.owner, world.site, ENTREPOT)
    assert set_site_module(world.owner, world.site, "pos", False).status_code == 204
    stale = _change(world.owner, world.site, ENTREPOT, preview["fingerprint"])
    assert stale.json()["code"] == "profile_preview_outdated"
    first = _ok_preview(world.owner, world.site, MAQUIS)
    assert _change(world.owner, world.site, MAQUIS, first["fingerprint"]).status_code == 200
    replay = _change(world.owner, world.site, MAQUIS, first["fingerprint"])
    assert (replay.status_code, replay.json()["code"]) == (409, "profile_preview_outdated")


def test_concurrent_changes_one_wins(world: World, app: Any, owner_db: Session) -> None:
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def change() -> None:
        with TestClient(app) as client:
            api = Api(client, world.owner.token)
            barrier.wait()
            statuses.append(_change(api, world.site, MAQUIS, preview["fingerprint"]).status_code)

    threads = [threading.Thread(target=change) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(statuses) == [200, 409]
    assert len(_audits(owner_db, "site.profile_changed")) == 1


def _in_thread(fn: Any) -> tuple[threading.Thread, list[Any]]:
    result: list[Any] = []
    thread = threading.Thread(target=lambda: result.append(fn()))
    thread.start()
    return thread, result


def test_change_waits_for_a_cash_session_being_opened(
    world: World, app: Any, owner_engine: Engine, owner_db: Session
) -> None:
    """Une ouverture de session en cours (verrou partagé du réglage de caisse, comme
    ``open_session``) est attendue puis vue : le changement vers un profil sans caisse est
    alors BLOCKED — jamais de session orpheline."""
    sh.enable_cash(world.owner, world.site)
    register = world.owner.post("/cash/registers", json={"site_id": world.site, "name": "C1"})
    assert register.status_code == 201
    preview = _ok_preview(world.owner, world.site, ENTREPOT)
    assert preview["level"] == "SIMPLE"
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": world.site}
    ).scalar_one()
    with owner_engine.connect() as conn:
        tx = conn.begin()
        conn.execute(
            text("SELECT 1 FROM cash_site_settings WHERE site_id = :s FOR SHARE"),
            {"s": world.site},
        )
        conn.execute(
            text(
                "INSERT INTO cash_sessions (id, tenant_id, number, cash_register_id, site_id, "
                "status, opening_float, opened_at) VALUES (gen_random_uuid(), :t, 'SES-RACE', "
                ":r, :s, 'OPEN', 0, now())"
            ),
            {"t": tenant, "r": register.json()["id"], "s": world.site},
        )

        def change() -> Any:
            with TestClient(app) as client:
                return _change(
                    Api(client, world.owner.token), world.site, ENTREPOT, preview["fingerprint"]
                )

        thread, result = _in_thread(change)
        time.sleep(1.0)
        assert thread.is_alive()  # en attente du verrou du réglage de caisse
        tx.commit()
        thread.join(timeout=30)
    # Le niveau a changé (BLOCKED) : aperçu obsolète, rien n'est modifié.
    assert result[0].status_code == 409
    assert _profile(owner_db, world.site) == ORIGIN
    assert _ok_preview(world.owner, world.site, ENTREPOT)["level"] == "BLOCKED"


def test_change_waits_for_writes_referencing_the_site(
    world: World, app: Any, owner_engine: Engine, owner_db: Session
) -> None:
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    with owner_engine.connect() as conn:
        tx = conn.begin()
        # Une écriture en cours qui référence le site (clé étrangère) tient ce verrou.
        conn.execute(text("SELECT 1 FROM sites WHERE id = :s FOR KEY SHARE"), {"s": world.site})

        def change() -> Any:
            with TestClient(app) as client:
                return _change(
                    Api(client, world.owner.token), world.site, MAQUIS, preview["fingerprint"]
                )

        thread, result = _in_thread(change)
        time.sleep(1.0)
        assert thread.is_alive()
        tx.commit()
        thread.join(timeout=30)
    assert result[0].status_code == 200
    assert _profile(owner_db, world.site) == MAQUIS


def test_concurrent_change_and_module_toggle(world: World, app: Any, owner_db: Session) -> None:
    preview = _ok_preview(world.owner, world.site, MAQUIS)
    barrier = threading.Barrier(2)
    statuses: dict[str, int] = {}

    def change() -> None:
        with TestClient(app) as client:
            barrier.wait()
            statuses["change"] = _change(
                Api(client, world.owner.token), world.site, MAQUIS, preview["fingerprint"]
            ).status_code

    def toggle() -> None:
        with TestClient(app) as client:
            barrier.wait()
            statuses["toggle"] = set_site_module(
                Api(client, world.owner.token), world.site, "alerts", False
            ).status_code

    threads = [threading.Thread(target=change), threading.Thread(target=toggle)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    # Aucun interblocage (ordre site → site_modules des deux côtés) ; la bascule réussit
    # toujours ; passée en premier, elle change l'état effectif montré par l'aperçu, qui est
    # alors obsolète (409), sinon le changement réussit.
    assert statuses["toggle"] == 204
    assert statuses["change"] in (200, 409)
    assert _activations(owner_db, world.site)["alerts"] is False
    expected = MAQUIS if statuses["change"] == 200 else ORIGIN
    assert _profile(owner_db, world.site) == expected


# --- Plan : avertir seulement, jamais contourner -------------------------------------------------


def test_profile_partially_outside_the_plan(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("standard", profile=ORIGIN, plan="STANDARD")
    owner = api_for("owner@standard.example.com")
    site = str(t.site_id)
    preview = _ok_preview(owner, site, MAQUIS)
    assert preview["plan"] == {
        "code": "STANDARD",
        "compatibility": "PARTIAL",
        "modules_not_in_plan": ["restaurant.qr"],
    }
    qr = _modules_of(preview)["restaurant.qr"]
    assert qr["after"] == {
        "in_profile": True,
        "in_plan": False,
        "activated": False,
        "effective": False,
    }
    assert qr["reason"] == "not_in_plan"
    assert _change(owner, site, MAQUIS, preview["fingerprint"]).status_code == 200
    assert "restaurant.qr" not in _activations(owner_db, site)
    # Aucun contournement : l'activation reste refusée par le plan du site.
    refused = set_site_module(owner, site, "restaurant.qr", True)
    assert (refused.status_code, refused.json()["code"]) == (422, "module_not_offered")


# --- Sécurité et erreurs -------------------------------------------------------------------------


def test_errors_and_scope(
    world: World, client: TestClient, owner_db: Session, provision: Any, api_for: Any
) -> None:
    owner = world.owner
    unknown = _preview(owner, world.site, "retail.inexistant")
    assert (unknown.status_code, unknown.json()["code"]) == (422, "unknown_profile")
    same = _preview(owner, world.site, ORIGIN)
    assert (same.status_code, same.json()["code"]) == (422, "profile_unchanged")
    missing = _preview(owner, str(uuid.uuid4()), MAQUIS)
    assert (missing.status_code, missing.json()["code"]) == (404, "site_not_found")
    bad = owner.put(
        f"/sites/{world.site}/business-profile",
        json={"profile_code": MAQUIS, "preview_fingerprint": "x"},
    )
    assert (bad.status_code, bad.json()["code"]) == (422, "validation_error")
    # Site d'un autre tenant : introuvable.
    other = provision("other", profile=ORIGIN)
    foreign = _preview(api_for("owner@other.example.com"), world.site, MAQUIS)
    assert (foreign.status_code, foreign.json()["code"]) == (404, "site_not_found")
    assert other.site_id
    # Vendeur : permission absente ; administrateur limité au site principal.
    seller = sh.member(world, client, "vendeur@alpha.example.com", "seller")
    denied = _preview(seller, world.site, MAQUIS)
    assert (denied.status_code, denied.json()["code"]) == (403, "permission_denied")
    admin_a = sh.member(
        world,
        client,
        "admin-a@alpha.example.com",
        "administrator",
        all_sites=False,
        site_ids=[world.site],
    )
    assert _preview(admin_a, world.site, MAQUIS).status_code == 200
    refused = _preview(admin_a, world.site2, MAQUIS)
    assert (refused.status_code, refused.json()["code"]) == (403, "site_access_denied")
    # La portée vient de l'URL : l'en-tête d'un autre site ne change pas le site ciblé.
    scoped = Api(owner.client, owner.token, uuid.UUID(world.site2))
    preview = _ok_preview(scoped, world.site, MAQUIS)
    assert preview["site_id"] == world.site
    # Site désactivé : hors des sites accessibles (règle existante, comme ``X-Site-Id``).
    owner_db.execute(text("UPDATE sites SET is_active = false WHERE id = :s"), {"s": world.site2})
    owner_db.commit()
    inactive = _preview(owner, world.site2, MAQUIS)
    assert (inactive.status_code, inactive.json()["code"]) == (403, "site_access_denied")


def test_expired_site_subscription_is_restricted(world: World, owner_db: Session) -> None:
    owner_db.execute(
        text("UPDATE subscriptions SET status = 'expired' WHERE site_id = :s"), {"s": world.site2}
    )
    owner_db.commit()
    refused = _preview(world.owner, world.site2, MAQUIS)
    assert (refused.status_code, refused.json()["code"]) == (403, "subscription_restricted")
    assert _ok_preview(world.owner, world.site, MAQUIS)["level"] == "SIMPLE"


def test_counts_never_include_another_tenant(world: World, provision: Any, api_for: Any) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    _validated_sale(world)
    other = provision("beta", profile=ORIGIN)
    preview = _ok_preview(api_for("owner@beta.example.com"), str(other.site_id), MAQUIS)
    assert preview["level"] == "SIMPLE"
    assert preview["history"] == [] and preview["open_operations"] == []


def test_tenant_origin_profile_route_unchanged(world: World) -> None:
    refused = world.owner.put("/tenant/business-profile", json={"code": MAQUIS})
    assert (refused.status_code, refused.json()["code"]) == (409, "profile_is_per_site")


# --- Création d'un site avec un profil (Q4) ------------------------------------------------------


def test_create_site_with_a_profile(world: World, owner_db: Session, client: TestClient) -> None:
    created = add_site(world.owner, "Maquis", "MAQ", "restaurant", business_profile_code=MAQUIS)
    assert created.status_code == 201, created.text
    site = created.json()
    assert site["business_profile_code"] == MAQUIS
    activations = _activations(owner_db, site["id"])
    assert activations["restaurant.orders"] is True
    assert activations["restaurant.tables"] is False  # facultatif (D11)
    assert activations["restaurant.qr"] is False  # facultatif
    assert activations["pos"] is True
    # Rien n'est copié d'un autre site : aucune donnée sur le nouveau site.
    assert _ok_preview(world.owner, site["id"], ORIGIN)["level"] == "SIMPLE"
    default = add_site(world.owner, "Boutique 2", "B2")
    assert default.json()["business_profile_code"] == ORIGIN
    unknown = add_site(world.owner, "X", "X1", business_profile_code="retail.inexistant")
    assert (unknown.status_code, unknown.json()["code"]) == (422, "unknown_profile")
    # Un autre profil que celui d'origine exige aussi organization.profile.manage.
    custom = world.owner.post(
        "/roles",
        json={
            "name": "Sites seulement",
            "permissions": ["organization.site.view", "organization.site.manage"],
        },
    )
    assert custom.status_code == 201, custom.text
    body = {
        "email": "sites@alpha.example.com",
        "full_name": "Sites",
        "password": "Provisoire-123",
        "roles": [{"role_id": custom.json()["id"]}],
    }
    assert world.owner.post("/members", json=body).status_code == 201
    token = login(client, "sites@alpha.example.com", "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    sites_admin = Api(client, login(client, "sites@alpha.example.com").json()["access_token"])
    denied = add_site(sites_admin, "Y", "Y1", business_profile_code=MAQUIS)
    assert (denied.status_code, denied.json()["code"]) == (403, "permission_denied")
    assert add_site(sites_admin, "Z", "Z1").status_code == 201
