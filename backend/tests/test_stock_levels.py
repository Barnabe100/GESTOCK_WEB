"""Stock par site, seuils (Q2), journal des mouvements et alertes (STK-*, ALR-01, ALR-02)."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests import stock_helpers as sh
from tests.stock_helpers import World


def _levels(api: Any, **params: Any) -> dict[str, dict[str, Any]]:
    """Niveaux indexés par « référence@site »."""
    response = api.get("/stock/levels", params={"limit": 100, **params})
    assert response.status_code == 200, response.text
    return {f"{i['reference']}@{i['site_id']}": i for i in response.json()["items"]}


def _set_article_min(w: World, index: int, value: str) -> None:
    response = w.owner.patch(f"/catalog/articles/{w.articles[index]}", json={"min_stock": value})
    assert response.status_code == 200, response.text


# --- Stock par site ----------------------------------------------------------------------------


def test_levels_list_every_article_per_site_with_state(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "3", "50")])
    exit_doc = sh.exit_doc(world, [(1, "3")])
    world.owner.post(f"/stock/exits/{exit_doc['id']}/validate")
    _set_article_min(world, 0, "12")

    levels = _levels(world.owner)
    assert len(levels) == 6  # 3 articles × 2 sites
    a0 = levels[f"A-0@{world.site}"]
    assert (a0["quantity"], a0["average_cost"], a0["stock_value"]) == (
        "10.000",
        "100.0000",
        "1000.00",
    )
    assert (a0["min_stock"], a0["min_override"], a0["state"]) == ("12.000", None, "low")
    assert levels[f"A-1@{world.site}"]["state"] == "out"  # géré sur le site, stock nul
    assert levels[f"A-2@{world.site}"]["state"] == "not_stocked"
    assert levels[f"A-0@{world.site2}"]["state"] == "not_stocked"

    only_site = _levels(world.owner, site_id=world.site2)
    assert {i["site_id"] for i in only_site.values()} == {world.site2}
    assert set(_levels(world.owner, state="alerts")) == {f"A-0@{world.site}", f"A-1@{world.site}"}
    assert set(_levels(world.owner, search="a-1")) == {f"A-1@{world.site}", f"A-1@{world.site2}"}


def test_inactive_articles_are_hidden_unless_requested(world: World) -> None:
    sh.validated_entry(world, [(0, "1", "1")])
    world.owner.post(f"/catalog/articles/{world.articles[0]}/deactivate")
    assert f"A-0@{world.site}" not in _levels(world.owner)
    shown = _levels(world.owner, include_inactive="true")[f"A-0@{world.site}"]
    assert shown["article_active"] is False and shown["quantity"] == "1.000"


# --- Seuils par site (Q2) ----------------------------------------------------------------------


def test_site_threshold_override_prevails(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    _set_article_min(world, 0, "5")
    url = f"/stock/levels/{world.site}/{world.articles[0]}/thresholds"

    level = world.owner.put(url, json={"min_stock": "20", "max_stock": "50"})
    assert level.status_code == 200, level.text
    body = level.json()
    assert (body["min_stock"], body["min_override"], body["max_stock"]) == (
        "20.000",
        "20.000",
        "50.000",
    )
    assert body["state"] == "low" and body["quantity"] == "10.000"  # quantité et CMUP inchangés
    assert body["average_cost"] == "100.0000"
    # L'autre site garde le seuil de l'article.
    assert _levels(world.owner)[f"A-0@{world.site2}"]["min_stock"] == "5.000"

    reset = world.owner.put(url, json={"min_stock": None, "max_stock": None}).json()
    assert (reset["min_stock"], reset["min_override"], reset["state"]) == ("5.000", None, "ok")

    invalid = world.owner.put(url, json={"min_stock": "10", "max_stock": "2"})
    assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_stock_thresholds"
    negative = world.owner.put(url, json={"min_stock": "-1"})
    assert negative.status_code == 422

    actions = world.owner.get("/audit-logs?action=stock_threshold").json()["items"]
    assert [a["action"] for a in actions] == ["stock_threshold.updated"] * 2
    assert actions[1]["data"]["min_stock"] == {"before": None, "after": "20.000"}
    assert actions[1]["site_id"] == world.site
    # Aucun mouvement créé par un changement de seuil.
    count = owner_db.execute(text("SELECT count(*) FROM stock_movements")).scalar_one()
    assert count == 1


def test_threshold_on_unknown_article_or_foreign_site(
    world: World, provision: Any, api_for: Any
) -> None:
    missing = world.owner.put(
        f"/stock/levels/{world.site}/{world.site}/thresholds", json={"min_stock": "1"}
    )
    assert missing.status_code == 404 and missing.json()["code"] == "article_not_found"
    provision("beta")
    other = api_for("owner@beta.example.com")
    beta_site = other.get("/sites").json()[0]["id"]
    foreign_article = other.put(
        f"/stock/levels/{beta_site}/{world.articles[0]}/thresholds", json={"min_stock": "1"}
    )
    assert foreign_article.json()["code"] == "article_not_found"
    foreign_site = other.put(
        f"/stock/levels/{world.site}/{world.articles[0]}/thresholds", json={"min_stock": "1"}
    )
    assert foreign_site.json()["code"] == "site_access_denied"


# --- Journal des mouvements --------------------------------------------------------------------


def test_movement_journal(world: World) -> None:
    entry = sh.validated_entry(world, [(0, "10", "100"), (1, "5", "20")])
    exit_doc = sh.exit_doc(world, [(0, "4")])
    world.owner.post(f"/stock/exits/{exit_doc['id']}/validate")
    assert entry["number"] == "ENT-000001"
    journal = world.owner.get("/stock/movements", params={"limit": 50}).json()
    movements = journal["items"]
    types = [(m["movement_type"], m["article_reference"], m["quantity"]) for m in movements]
    assert ("EXIT", "A-0", "-4.000") in types and ("ENTRY", "A-1", "5.000") in types
    first = movements[-1]  # tri par défaut : plus récent d'abord
    assert first["document_number"] == "ENT-000001" and first["user_name"]
    exit_move = next(m for m in movements if m["movement_type"] == "EXIT")
    assert exit_move["document_number"] == "SOR-000001"
    assert exit_move["unit_cost"] == "100.0000"  # CMUP du site figé (Q1)
    assert (exit_move["quantity_before"], exit_move["quantity_after"]) == ("10.000", "6.000")

    by_type = world.owner.get("/stock/movements", params={"movement_type": "EXIT"}).json()
    assert by_type["total"] == 1
    by_doc = world.owner.get("/stock/movements", params={"search": "sor-0000"}).json()
    assert by_doc["total"] == 1
    by_article = world.owner.get(
        "/stock/movements", params={"article_id": world.articles[1]}
    ).json()
    assert {m["article_reference"] for m in by_article["items"]} == {"A-1"}
    other_site = world.owner.get("/stock/movements", params={"site_id": world.site2}).json()
    assert other_site["total"] == 0
    future = world.owner.get("/stock/movements", params={"date_from": "2999-01-01"}).json()
    assert future["total"] == 0
    today = movements[0]["occurred_at"][:10]
    assert world.owner.get("/stock/movements", params={"date_to": today}).json()["total"] >= 3


# --- Alertes (ALR-01, ALR-02) ------------------------------------------------------------------


def test_stock_alerts(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "2", "10")])
    exit_doc = sh.exit_doc(world, [(1, "2")])
    world.owner.post(f"/stock/exits/{exit_doc['id']}/validate")
    _set_article_min(world, 0, "10")

    alerts = world.owner.get("/alerts/stock").json()
    states = {i["reference"]: i["state"] for i in alerts["items"]}
    assert states == {"A-0": "low", "A-1": "out"}  # A-2 jamais gérée : pas d'alerte
    assert world.owner.get("/alerts/stock/summary").json() == {"out": 1, "low": 1}
    assert world.owner.get("/alerts/stock", params={"state": "out"}).json()["total"] == 1
    # Un filtre hors alertes est ramené aux alertes.
    assert world.owner.get("/alerts/stock", params={"state": "ok"}).json()["total"] == 2
    empty = world.owner.get("/alerts/stock/summary", params={"site_id": world.site2}).json()
    assert empty == {"out": 0, "low": 0}

    # Article désactivé : plus d'alerte.
    world.owner.post(f"/catalog/articles/{world.articles[1]}/deactivate")
    assert world.owner.get("/alerts/stock/summary").json() == {"out": 0, "low": 1}


# --- Permissions, sites, isolation --------------------------------------------------------------


def test_permissions_and_site_restriction(world: World, client: TestClient) -> None:
    sh.validated_entry(world, [(0, "1", "1")])
    viewer = sh.member(world, client, "lecteur@example.com", "viewer", all_sites=True)
    assert viewer.get("/stock/levels").status_code == 200
    assert viewer.get("/stock/movements").status_code == 200
    assert viewer.get("/alerts/stock/summary").status_code == 200
    denied = viewer.put(
        f"/stock/levels/{world.site}/{world.articles[0]}/thresholds", json={"min_stock": "1"}
    )
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"

    depot_only = sh.member(
        world, client, "depot@example.com", "stock_manager", site_ids=[world.site2]
    )
    assert {i["site_id"] for i in _levels(depot_only).values()} == {world.site2}
    assert _levels(depot_only, site_id=world.site) == {}
    assert depot_only.get("/stock/movements").json()["total"] == 0
    blocked = depot_only.put(
        f"/stock/levels/{world.site}/{world.articles[0]}/thresholds", json={"min_stock": "1"}
    )
    assert blocked.json()["code"] == "site_access_denied"
    allowed = depot_only.put(
        f"/stock/levels/{world.site2}/{world.articles[0]}/thresholds", json={"min_stock": "1"}
    )
    assert allowed.status_code == 200  # gestionnaire de stock : seuils autorisés


def test_isolation_between_tenants(world: World, provision: Any, api_for: Any) -> None:
    sh.validated_entry(world, [(0, "1", "1")])
    provision("beta")
    other = api_for("owner@beta.example.com")
    assert other.get("/stock/levels").json()["total"] == 0  # aucun article chez beta
    assert other.get("/stock/movements").json()["total"] == 0
    assert other.get("/stock/movements", params={"site_id": world.site}).json()["total"] == 0
    assert other.get("/alerts/stock/summary").json() == {"out": 0, "low": 0}


def test_alerts_module_is_available(world: World) -> None:
    capabilities = world.owner.get("/me/capabilities").json()
    modules = {m["code"]: m["status"] for m in capabilities["modules"]}
    assert modules["alerts"] == "available" and modules["stock"] == "available"
    assert "alerts.stock.view" in capabilities["permissions"]
