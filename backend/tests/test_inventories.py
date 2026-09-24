"""Inventaires (Phase 2.6) : création (complet, ciblé), articles, comptage, cycle de vie,
écart calculé sur le stock COURANT à la validation, mouvements d'ajustement, CMUP, stock
négatif, concurrence (vente pendant la validation, double validation, plusieurs articles),
RBAC (rôles de base, rôle personnalisé), périmètre des sites, abonnement, plan STANDARD,
audit, isolation API et SQL (rôle applicatif réel, RLS)."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

BASE = "/inventories"
PERMISSIONS = {
    f"inventory_count.inventory.{action}"
    for action in ("view", "create", "update", "count", "validate", "cancel")
}


# --- Aides ---------------------------------------------------------------------------------------


def _create(
    w: World,
    kind: str = "TARGETED",
    articles: list[int] | None = None,
    api: Api | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"site_id": w.site, "inventory_type": kind, **extra}
    if kind == "TARGETED":
        body["article_ids"] = [w.articles[i] for i in (articles if articles is not None else [0])]
    response = (api or w.owner).post(BASE, json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _lines(w: World, inventory: dict[str, Any], api: Api | None = None, **params: Any) -> Any:
    response = (api or w.owner).get(f"{BASE}/{inventory['id']}/lines", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _line_ids(w: World, inventory: dict[str, Any]) -> dict[str, str]:
    """article → ligne."""
    return {line["article_id"]: line["id"] for line in _lines(w, inventory)["items"]}


def _count(
    w: World, inventory: dict[str, Any], counts: dict[int, str | None], api: Api | None = None
) -> Any:
    ids = _line_ids(w, inventory)
    return (api or w.owner).patch(
        f"{BASE}/{inventory['id']}/lines",
        json={
            "counts": [
                {"line_id": ids[w.articles[i]], "quantity_physical": q} for i, q in counts.items()
            ]
        },
    )


def _action(api: Api, inventory: dict[str, Any], action: str, **kw: Any) -> Any:
    return api.post(f"{BASE}/{inventory['id']}/{action}", **kw)


def _ready(w: World, counts: dict[int, str], kind: str = "TARGETED") -> dict[str, Any]:
    """Inventaire compté et prêt à valider (articles = clés de ``counts``)."""
    inventory = _create(w, kind, list(counts) if kind == "TARGETED" else None)
    assert _action(w.owner, inventory, "start").status_code == 200
    assert _count(w, inventory, dict(counts)).status_code == 200
    response = _action(w.owner, inventory, "complete-counting")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _sale(w: World, index: int, quantity: str, validate: bool = True) -> dict[str, Any]:
    sale = w.owner.post(
        "/sales",
        json={
            "site_id": w.site,
            "lines": [{"article_id": w.articles[index], "quantity": quantity}],
        },
    )
    assert sale.status_code == 201, sale.text
    if validate:
        assert w.owner.post(f"/sales/{sale.json()['id']}/validate").status_code == 200
    return dict(sale.json())


def _adjustments(owner_db: Session) -> list[tuple[str, str, str, str | None, str]]:
    """(article, quantité, stock après, coût, numéro source) des ajustements d'inventaire."""
    owner_db.expire_all()
    return [
        (str(r[0]), r[1], r[2], r[3], r[4])
        for r in owner_db.execute(
            text(
                "SELECT article_id, quantity::text, quantity_after::text, unit_cost::text, "
                "source_number FROM stock_movements WHERE source_type = 'inventory_count' "
                "AND movement_type = 'ADJUSTMENT' ORDER BY occurred_at, article_id"
            )
        )
    ]


def _custom_member(w: World, client: TestClient, email: str, permissions: list[str]) -> Api:
    role = w.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions})
    assert role.status_code == 201, role.text
    created = w.owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": [{"role_id": role.json()["id"]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


# --- Création -------------------------------------------------------------------------------------


def test_full_inventory_takes_active_articles_managed_on_site(
    world: World, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "4", "250")])
    inventory = _create(world, "FULL")
    assert inventory["number"] == "INV-000001"
    assert inventory["status"] == "DRAFT" and inventory["inventory_type"] == "FULL"
    assert inventory["line_count"] == 2 and inventory["counted_count"] == 0
    lines = {line["reference"]: line for line in _lines(world, inventory)["items"]}
    # Article 2 jamais géré sur le site : absent d'un inventaire complet.
    assert set(lines) == {"A-0", "A-1"}
    assert lines["A-0"]["stock_theoretical_initial"] == "10.000"
    assert lines["A-1"]["stock_theoretical_initial"] == "4.000"
    assert lines["A-0"]["quantity_physical"] is None
    # Aucun effet sur le stock.
    assert sh.level(owner_db, world, 0)[0] == "10.000"
    assert _adjustments(owner_db) == []
    # Le numéro suit la séquence du tenant.
    sh.validated_entry(world, [(0, "1", "100")], site_id=world.site2)
    second = _create(world, "FULL", site_id=world.site2)
    assert second["number"] == "INV-000002"


def test_full_inventory_rules(world: World) -> None:
    empty = world.owner.post(BASE, json={"site_id": world.site, "inventory_type": "FULL"})
    assert empty.status_code == 422 and empty.json()["code"] == "inventory_empty"
    sh.validated_entry(world, [(0, "5", "100")])
    fixed = world.owner.post(
        BASE,
        json={
            "site_id": world.site,
            "inventory_type": "FULL",
            "article_ids": [world.articles[1]],
        },
    )
    assert fixed.status_code == 422 and fixed.json()["code"] == "inventory_full_articles_fixed"
    # Article désactivé : exclu d'un inventaire complet.
    sh.validated_entry(world, [(1, "5", "100")])
    world.owner.post(f"/catalog/articles/{world.articles[1]}/deactivate")
    inventory = _create(world, "FULL")
    assert [line["reference"] for line in _lines(world, inventory)["items"]] == ["A-0"]


def test_targeted_inventory_accepts_article_never_stocked_on_site(world: World) -> None:
    inventory = _create(world, "TARGETED", [2, 0])
    lines = {line["reference"]: line for line in _lines(world, inventory)["items"]}
    assert set(lines) == {"A-0", "A-2"}
    assert lines["A-2"]["stock_theoretical_initial"] == "0.000"


@pytest.mark.parametrize(
    ("article_ids", "code"),
    [
        ([], "inventory_empty"),
        (["dup", "dup"], "duplicate_article_line"),
        ([str(uuid.uuid4())], "article_not_found"),
    ],
)
def test_targeted_inventory_article_rules(world: World, article_ids: list[str], code: str) -> None:
    ids = [world.articles[0] if a == "dup" else a for a in article_ids]
    response = world.owner.post(
        BASE, json={"site_id": world.site, "inventory_type": "TARGETED", "article_ids": ids}
    )
    assert response.status_code == 422 and response.json()["code"] == code


def test_inactive_article_is_refused(world: World) -> None:
    world.owner.post(f"/catalog/articles/{world.articles[1]}/deactivate")
    response = world.owner.post(
        BASE,
        json={
            "site_id": world.site,
            "inventory_type": "TARGETED",
            "article_ids": [world.articles[1]],
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "article_inactive"


def test_site_is_required_and_must_be_accessible(
    world: World, provision: Any, client: TestClient
) -> None:
    missing = world.owner.post(
        BASE, json={"inventory_type": "TARGETED", "article_ids": [world.articles[0]]}
    )
    assert missing.status_code == 422 and missing.json()["code"] == "site_required"
    # Site d'un autre tenant.
    beta = provision("beta")
    other = world.owner.post(
        BASE,
        json={
            "site_id": str(beta.site_id),
            "inventory_type": "TARGETED",
            "article_ids": [world.articles[0]],
        },
    )
    assert other.status_code == 403 and other.json()["code"] == "site_access_denied"
    # Membre limité au site A : site B refusé, site A autorisé.
    shop = sh.member(world, client, "boutique@example.com", "manager", site_ids=[world.site])
    denied = shop.post(
        BASE,
        json={
            "site_id": world.site2,
            "inventory_type": "TARGETED",
            "article_ids": [world.articles[0]],
        },
    )
    assert denied.status_code == 403 and denied.json()["code"] == "site_access_denied"
    assert _create(world, api=shop)["site_id"] == world.site


def test_article_in_only_one_open_inventory_per_site(world: World) -> None:
    first = _create(world, "TARGETED", [0, 1])
    clash = world.owner.post(
        BASE,
        json={
            "site_id": world.site,
            "inventory_type": "TARGETED",
            "article_ids": [world.articles[1], world.articles[2]],
        },
    )
    assert clash.status_code == 409 and clash.json()["code"] == "article_in_open_inventory"
    assert clash.json()["articles"] == ["A-1"]
    assert clash.json()["inventories"] == [first["number"]]
    # Autre site : aucun conflit.
    _create(world, "TARGETED", [1], site_id=world.site2)
    # Inventaire annulé : l'article redevient disponible.
    assert (
        _action(world.owner, first, "cancel", json={"reason": "Erreur de saisie"}).status_code
        == 200
    )
    _create(world, "TARGETED", [1, 2])


def test_update_draft_articles_and_comment(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "3", "100"), (2, "7", "100")])
    inventory = _create(world, "TARGETED", [0, 1])
    url = f"{BASE}/{inventory['id']}"
    updated = world.owner.put(
        url,
        json={
            "comment": "Rayon outillage",
            "add_article_ids": [world.articles[2], world.articles[0]],
            "remove_article_ids": [world.articles[1]],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["comment"] == "Rayon outillage" and updated.json()["line_count"] == 2
    lines = {line["reference"]: line for line in _lines(world, inventory)["items"]}
    assert set(lines) == {"A-0", "A-2"} and lines["A-2"]["stock_theoretical_initial"] == "7.000"
    # Retirer tous les articles : refusé (un inventaire compte au moins un article).
    emptied = world.owner.put(
        url, json={"remove_article_ids": [world.articles[0], world.articles[2]]}
    )
    assert emptied.status_code == 422 and emptied.json()["code"] == "inventory_empty"
    # Inventaire complet : liste d'articles non modifiable.
    sh.validated_entry(world, [(1, "2", "100")], site_id=world.site2)
    full = _create(world, "FULL", site_id=world.site2)
    fixed = world.owner.put(f"{BASE}/{full['id']}", json={"add_article_ids": [world.articles[0]]})
    assert fixed.status_code == 422 and fixed.json()["code"] == "inventory_full_articles_fixed"
    # Hors brouillon : refus.
    _action(world.owner, inventory, "start")
    locked = world.owner.put(url, json={"comment": "x"})
    assert locked.status_code == 409 and locked.json()["code"] == "inventory_invalid_transition"


# --- Comptage -------------------------------------------------------------------------------------


def test_start_refreshes_snapshot_and_full_list(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    inventory = _create(world, "FULL")
    # Entre la création et le début du comptage : entrée et nouvel article géré sur le site.
    sh.validated_entry(world, [(0, "5", "100"), (1, "2", "100")])
    started = _action(world.owner, inventory, "start")
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] == "COUNTING" and body["started_at"] and body["started_by_name"]
    lines = {line["reference"]: line for line in _lines(world, inventory)["items"]}
    assert set(lines) == {"A-0", "A-1"}
    assert lines["A-0"]["stock_theoretical_initial"] == "15.000"
    assert lines["A-1"]["stock_theoretical_initial"] == "2.000"


def test_counting_progression_modification_and_rules(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "4", "100")])
    inventory = _create(world, "TARGETED", [0, 1, 2])
    # Saisie impossible avant le début du comptage.
    early = _count(world, inventory, {0: "9"})
    assert early.status_code == 409 and early.json()["code"] == "inventory_invalid_transition"
    _action(world.owner, inventory, "start")

    saved = _count(world, inventory, {0: "9.5"})
    assert saved.status_code == 200, saved.text
    line = saved.json()["lines"][0]
    assert line["quantity_physical"] == "9.500"
    assert line["indicative_variance"] == "-0.500" and line["counted_by_name"]
    summary = saved.json()["summary"]
    assert (summary["lines"], summary["counted"], summary["shortage"]) == (3, 1, 1)
    # Modification, puis effacement (null) d'une ligne.
    assert _count(world, inventory, {0: "10", 1: "4"}).json()["summary"]["no_variance"] == 2
    cleared = _count(world, inventory, {1: None})
    assert cleared.json()["summary"]["counted"] == 1
    assert world.owner.get(f"{BASE}/{inventory['id']}").json()["counted_count"] == 1

    # Quantité négative, trop de décimales : refusées par la validation d'entrée.
    for bad in ("-1", "1.0001"):
        response = _count(world, inventory, {0: bad})
        assert response.status_code == 422
    # Ligne inconnue ou d'un autre inventaire, ligne en double.
    unknown = world.owner.patch(
        f"{BASE}/{inventory['id']}/lines",
        json={"counts": [{"line_id": str(uuid.uuid4()), "quantity_physical": "1"}]},
    )
    assert unknown.status_code == 422 and unknown.json()["code"] == "inventory_line_not_found"
    line_id = _line_ids(world, inventory)[world.articles[0]]
    twice = world.owner.patch(
        f"{BASE}/{inventory['id']}/lines",
        json={
            "counts": [
                {"line_id": line_id, "quantity_physical": "1"},
                {"line_id": line_id, "quantity_physical": "2"},
            ]
        },
    )
    assert twice.status_code == 422 and twice.json()["code"] == "duplicate_count_line"

    # Terminer : toutes les lignes doivent être comptées.
    incomplete = _action(world.owner, inventory, "complete-counting")
    assert incomplete.status_code == 422
    assert incomplete.json()["code"] == "inventory_not_fully_counted"
    assert incomplete.json()["remaining"] == 2
    _count(world, inventory, {1: "4", 2: "0"})
    ready = _action(world.owner, inventory, "complete-counting")
    assert ready.status_code == 200 and ready.json()["status"] == "READY_TO_VALIDATE"
    # Comptage figé ; reprise possible avant validation.
    assert _count(world, inventory, {0: "1"}).status_code == 409
    reopened = _action(world.owner, inventory, "reopen-counting")
    assert reopened.status_code == 200 and reopened.json()["status"] == "COUNTING"
    assert reopened.json()["completed_at"] is None
    assert _count(world, inventory, {0: "8"}).status_code == 200


def test_lines_filters_search_and_candidates(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "4", "100"), (2, "1", "100")])
    inventory = _create(world, "FULL")
    _action(world.owner, inventory, "start")
    _count(world, inventory, {0: "12", 1: "3"})
    states = {
        state: [line["reference"] for line in _lines(world, inventory, state=state)["items"]]
        for state in ("counted", "uncounted", "surplus", "shortage", "no_variance")
    }
    assert states == {
        "counted": ["A-0", "A-1"],
        "uncounted": ["A-2"],
        "surplus": ["A-0"],
        "shortage": ["A-1"],
        "no_variance": [],
    }
    assert _lines(world, inventory, search="Article 1")["total"] == 1
    page = _lines(world, inventory, limit=2, sort="-reference")
    assert page["total"] == 3 and [line["reference"] for line in page["items"]] == ["A-2", "A-1"]
    # Articles proposables : recherche serveur, stock courant du site.
    candidates = world.owner.get(
        f"{BASE}/candidates", params={"site_id": world.site2, "search": "A-1"}
    ).json()
    assert candidates["total"] == 1
    assert candidates["items"][0]["stocked"] is False
    assert candidates["items"][0]["quantity"] == "0.000"
    stocked = world.owner.get(
        f"{BASE}/candidates", params={"site_id": world.site, "stocked_only": "true"}
    ).json()
    assert stocked["total"] == 3


# --- Workflow -------------------------------------------------------------------------------------


def test_invalid_transitions(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    inventory = _create(world)
    # DRAFT → VALIDATED interdit ; comptage à terminer avant validation.
    for action in ("validate", "complete-counting", "reopen-counting"):
        response = _action(world.owner, inventory, action)
        assert response.status_code == 409, action
        assert response.json()["code"] == "inventory_invalid_transition"
        assert response.json()["status"] == "DRAFT"
    _action(world.owner, inventory, "start")
    assert _action(world.owner, inventory, "start").status_code == 409
    assert _action(world.owner, inventory, "validate").status_code == 409  # COUNTING
    # Annulation : motif obligatoire (5 caractères au moins).
    assert _action(world.owner, inventory, "cancel", json={"reason": "no"}).status_code == 422


def test_cancel_from_each_open_status_has_no_stock_effect(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "10", "100"), (2, "10", "100")])
    draft = _create(world, "TARGETED", [0])
    counting = _create(world, "TARGETED", [1])
    _action(world.owner, counting, "start")
    ready = None
    for index, inventory in ((0, draft), (1, counting)):
        cancelled = _action(
            world.owner, inventory, "cancel", json={"reason": "Abandon du comptage"}
        )
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED", index
    ready = _ready(world, {2: "3"})
    cancelled = _action(world.owner, ready, "cancel", json={"reason": "Recomptage prévu"})
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancellation_reason"] == "Recomptage prévu"
    assert cancelled.json()["cancelled_by_name"]
    again = _action(world.owner, ready, "cancel", json={"reason": "Encore une fois"})
    assert again.status_code == 409
    assert _action(world.owner, ready, "validate").status_code == 409
    assert [sh.level(owner_db, world, i)[0] for i in range(3)] == ["10.000"] * 3
    assert _adjustments(owner_db) == []


# --- Stock : règle centrale -----------------------------------------------------------------------


def test_variance_uses_current_stock_at_validation(world: World, owner_db: Session) -> None:
    """Exemple métier : 100 au début, 95 comptés, +10 entrée et −5 vente pendant le comptage :
    stock courant 105, écart −10, stock final 95 (et non 95 − 100 = −5)."""
    sh.validated_entry(world, [(0, "100", "1000")])
    inventory = _create(world)
    _action(world.owner, inventory, "start")
    assert _lines(world, inventory)["items"][0]["stock_theoretical_initial"] == "100.000"
    _count(world, inventory, {0: "95"})
    sh.validated_entry(world, [(0, "10", "1000")])
    _sale(world, 0, "5")
    line = _lines(world, inventory)["items"][0]
    assert line["stock_current"] == "105.000"
    assert line["indicative_variance"] == "-5.000"  # information : physique − initial
    assert line["quantity_variance"] == "-10.000"  # ce que la validation appliquera
    _action(world.owner, inventory, "complete-counting")
    validated = _action(world.owner, inventory, "validate")
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["status"] == "VALIDATED" and body["validated_by_name"]
    assert body["summary"] == {
        "lines": 1,
        "counted": 1,
        "surplus": 0,
        "shortage": 1,
        "no_variance": 0,
        "surplus_value": "0.00",
        "shortage_value": "10000.00",
        "adjustment_value": "-10000.00",
        "final": True,
    }
    line = _lines(world, inventory)["items"][0]
    assert line["stock_theoretical_initial"] == "100.000"
    assert line["stock_theoretical_at_validation"] == "105.000"
    assert line["quantity_physical"] == "95.000"
    assert line["quantity_variance"] == "-10.000"
    assert line["unit_cost"] == "1000.0000" and line["adjustment_value"] == "-10000.00"
    assert line["stock_current"] is None  # figé : plus de stock courant affiché
    assert sh.level(owner_db, world, 0) == ("95.000", "1000.0000")
    assert _adjustments(owner_db) == [
        (world.articles[0], "-10.000", "95.000", "1000.0000", inventory["number"])
    ]


def test_surplus_shortage_and_no_variance_with_cmup(world: World, owner_db: Session) -> None:
    # CMUP 1000 (5 × 800 + 5 × 1200) sur l'article 0 ; 250 sur l'article 1 ; 40 sur l'article 2.
    sh.validated_entry(world, [(0, "5", "800"), (1, "8", "250"), (2, "4", "40")])
    sh.validated_entry(world, [(0, "5", "1200")])
    assert sh.level(owner_db, world, 0) == ("10.000", "1000.0000")
    inventory = _ready(world, {0: "13", 1: "6.5", 2: "4"})
    validated = _action(world.owner, inventory, "validate").json()
    assert validated["summary"]["surplus"] == 1
    assert validated["summary"]["shortage"] == 1
    assert validated["summary"]["no_variance"] == 1
    assert validated["summary"]["surplus_value"] == "3000.00"  # 3 × CMUP courant
    assert validated["summary"]["shortage_value"] == "375.00"  # 1,5 × 250
    assert validated["summary"]["adjustment_value"] == "2625.00"
    assert validated["variance_count"] == 2
    # Excédent valorisé au CMUP courant : CMUP inchangé (pas de recalcul artificiel).
    assert sh.level(owner_db, world, 0) == ("13.000", "1000.0000")
    # Manquant : CMUP inchangé.
    assert sh.level(owner_db, world, 1) == ("6.500", "250.0000")
    # Aucun écart : aucun mouvement.
    assert sh.level(owner_db, world, 2) == ("4.000", "40.0000")
    moves = {m[0]: m for m in _adjustments(owner_db)}
    assert set(moves) == {world.articles[0], world.articles[1]}
    assert moves[world.articles[0]][1:4] == ("3.000", "13.000", "1000.0000")
    assert moves[world.articles[1]][1:4] == ("-1.500", "6.500", "250.0000")
    # Journal des mouvements : type ajustement, numéro de l'inventaire.
    journal = world.owner.get("/stock/movements", params={"search": inventory["number"]}).json()
    assert journal["total"] == 2
    assert {m["movement_type"] for m in journal["items"]} == {"ADJUSTMENT"}


def test_count_to_zero_and_article_never_stocked(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "7", "100")])
    inventory = _ready(world, {0: "0", 2: "3"})
    assert _action(world.owner, inventory, "validate").status_code == 200
    assert sh.level(owner_db, world, 0) == ("0.000", "100.0000")  # jamais négatif
    # Article jamais géré sur le site : excédent au CMUP courant (0), niveau créé.
    assert sh.level(owner_db, world, 2) == ("3.000", "0.0000")


def test_validated_inventory_is_immutable(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    inventory = _ready(world, {0: "8"})
    assert _action(world.owner, inventory, "validate").status_code == 200
    url = f"{BASE}/{inventory['id']}"
    for response in (
        world.owner.put(url, json={"comment": "après coup"}),
        _count(world, inventory, {0: "9"}),
        _action(world.owner, inventory, "start"),
        _action(world.owner, inventory, "reopen-counting"),
        _action(world.owner, inventory, "complete-counting"),
        _action(world.owner, inventory, "validate"),
        _action(world.owner, inventory, "cancel", json={"reason": "Erreur constatée"}),
    ):
        assert response.status_code == 409
        assert response.json()["code"] == "inventory_invalid_transition"
    assert world.owner.get(url).json()["status"] == "VALIDATED"
    assert sh.level(owner_db, world, 0)[0] == "8.000"
    assert len(_adjustments(owner_db)) == 1
    # Aucune suppression possible via l'API.
    assert world.owner.delete(url).status_code == 405
    # Correction : un nouvel inventaire est possible (l'article n'est plus « en cours »).
    _create(world)


# --- Concurrence ----------------------------------------------------------------------------------


def _run_concurrently(app: Any, token: str, paths: list[str]) -> list[int]:
    barrier = threading.Barrier(len(paths))
    statuses: list[int] = []

    def run(path: str) -> None:
        with TestClient(app) as client:
            api = Api(client, token)
            barrier.wait()
            statuses.append(api.post(path).status_code)

    threads = [threading.Thread(target=run, args=(path,)) for path in paths]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return sorted(statuses)


def test_sale_during_validation_is_taken_into_account(
    world: World, app: Any, owner_db: Session
) -> None:
    """Stock 10, 8 comptés ; une vente de 4 est validée en même temps que l'inventaire.
    Quel que soit l'ordre, le stock final est cohérent : l'inventaire relit le stock courant
    verrouillé (vente avant : 6 → +2 → 8 ; vente après : 10 → −2 → 8, puis −4 → 4)."""
    sh.validated_entry(world, [(0, "10", "100")])
    inventory = _ready(world, {0: "8"})
    sale = _sale(world, 0, "4", validate=False)
    statuses = _run_concurrently(
        app,
        world.owner.token,
        [f"{BASE}/{inventory['id']}/validate", f"/sales/{sale['id']}/validate"],
    )
    assert statuses == [200, 200]
    line = _lines(world, inventory)["items"][0]
    final = sh.level(owner_db, world, 0)[0]
    if line["stock_theoretical_at_validation"] == "6.000":  # vente d'abord
        assert line["quantity_variance"] == "2.000" and final == "8.000"
    else:  # inventaire d'abord
        assert line["stock_theoretical_at_validation"] == "10.000"
        assert line["quantity_variance"] == "-2.000" and final == "4.000"


def test_concurrent_double_validation_applies_once(
    world: World, app: Any, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "5", "100")])
    inventory = _ready(world, {0: "7", 1: "6"})
    path = f"{BASE}/{inventory['id']}/validate"
    assert _run_concurrently(app, world.owner.token, [path, path]) == [200, 409]
    assert len(_adjustments(owner_db)) == 2
    assert sh.level(owner_db, world, 0)[0] == "7.000"
    assert sh.level(owner_db, world, 1)[0] == "6.000"


def test_multi_article_validation_does_not_deadlock(
    world: World, app: Any, owner_db: Session
) -> None:
    """Inventaire sur 3 articles et transfert A → B des mêmes articles dans l'ordre inverse,
    validés simultanément : même ordre global de verrouillage, aucun interblocage."""
    sh.validated_entry(world, [(0, "10", "100"), (1, "10", "100"), (2, "10", "100")])
    inventory = _ready(world, {0: "9", 1: "9", 2: "9"})
    transfer = world.owner.post(
        "/stock/transfers",
        json={
            "source_site_id": world.site,
            "destination_site_id": world.site2,
            "lines": [
                {"article_id": world.articles[2], "quantity": "1"},
                {"article_id": world.articles[1], "quantity": "1"},
            ],
        },
    ).json()
    statuses = _run_concurrently(
        app,
        world.owner.token,
        [f"{BASE}/{inventory['id']}/validate", f"/stock/transfers/{transfer['id']}/validate"],
    )
    assert statuses == [200, 200]
    levels = [sh.level(owner_db, world, i)[0] for i in range(3)]
    assert levels[0] == "9.000"
    # Transfert après l'inventaire : 9 − 1 ; avant : l'inventaire ramène à 9.
    assert levels[1] == levels[2] and levels[1] in ("8.000", "9.000")


# --- Sécurité : RBAC, sites, abonnement, plan -----------------------------------------------------


def test_permissions_by_base_role(world: World, client: TestClient) -> None:
    sh.validated_entry(world, [(0, "20", "100"), (1, "20", "100")])
    manager = sh.member(world, client, "gestion@example.com", "manager", all_sites=True)
    seller = sh.member(world, client, "vendeur@example.com", "seller", all_sites=True)
    viewer = sh.member(world, client, "consultant@example.com", "viewer", all_sites=True)
    admin = sh.member(world, client, "admin@example.com", "administrator", all_sites=True)
    for api in (manager, admin):
        assert set(api.get("/me/capabilities").json()["permissions"]) >= PERMISSIONS

    # Gestionnaire : cycle complet, y compris validation et annulation.
    inventory = _create(world, "TARGETED", [0], api=manager)
    assert _action(manager, inventory, "start").status_code == 200
    assert _count(world, inventory, {0: "19"}, api=manager).status_code == 200
    assert _action(manager, inventory, "complete-counting").status_code == 200
    assert _action(manager, inventory, "validate").status_code == 200
    other = _create(world, "TARGETED", [1], api=manager)
    assert _action(manager, other, "cancel", json={"reason": "Doublon"}).status_code == 200

    # Vendeur et Consultant : consultation seulement.
    for api in (seller, viewer):
        assert api.get(BASE).json()["total"] == 2
        assert api.get(f"{BASE}/{inventory['id']}").status_code == 200
        assert api.get(f"{BASE}/{inventory['id']}/lines").status_code == 200
        body = {
            "site_id": world.site,
            "inventory_type": "TARGETED",
            "article_ids": [world.articles[1]],
        }
        for response in (
            api.post(BASE, json=body),
            api.get(f"{BASE}/candidates", params={"site_id": world.site}),
            _action(api, other, "start"),
            _action(api, inventory, "validate"),
            _action(api, other, "cancel", json={"reason": "Tentative"}),
        ):
            assert response.status_code == 403
            assert response.json()["code"] == "permission_denied"


def test_custom_role_permissions_are_individual(world: World, client: TestClient) -> None:
    """Rôle personnalisé « compteur » : consultation et comptage, sans validation."""
    sh.validated_entry(world, [(0, "10", "100")])
    counter = _custom_member(
        world,
        client,
        "compteur@example.com",
        ["inventory_count.inventory.view", "inventory_count.inventory.count"],
    )
    inventory = _create(world)
    assert _action(counter, inventory, "start").status_code == 200
    assert _count(world, inventory, {0: "9"}, api=counter).status_code == 200
    assert _action(counter, inventory, "complete-counting").status_code == 200
    denied = _action(counter, inventory, "validate")
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    assert counter.put(f"{BASE}/{inventory['id']}", json={}).status_code == 403
    # Le propriétaire valide ; le comptage est attribué au compteur.
    assert _action(world.owner, inventory, "validate").status_code == 200
    assert _lines(world, inventory)["items"][0]["counted_by_name"] == "compteur@example.com"


def test_site_scope_on_read_and_write(world: World, client: TestClient) -> None:
    sh.validated_entry(world, [(0, "10", "100")], site_id=world.site2)
    depot = _create(world, "TARGETED", [0], site_id=world.site2)
    shop = sh.member(world, client, "boutique@example.com", "manager", site_ids=[world.site])
    assert shop.get(BASE).json()["total"] == 0
    for response in (
        shop.get(f"{BASE}/{depot['id']}"),
        shop.get(f"{BASE}/{depot['id']}/lines"),
        shop.put(f"{BASE}/{depot['id']}", json={"comment": "x"}),
        _action(shop, depot, "start"),
        _action(shop, depot, "cancel", json={"reason": "Tentative"}),
    ):
        assert response.status_code == 404 and response.json()["code"] == "inventory_not_found"
    # Site sélectionné (X-Site-Id) différent : refus explicite.
    world.owner.site_id = uuid.UUID(world.site)
    mismatch = world.owner.get(f"{BASE}/{depot['id']}")
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "site_mismatch"
    assert world.owner.get(BASE).json()["total"] == 0  # liste restreinte au site sélectionné


def test_expired_subscription_allows_read_only(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    inventory = _ready(world, {0: "9"})
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert world.owner.get(f"{BASE}/{inventory['id']}").status_code == 200
    assert world.owner.get(f"{BASE}/{inventory['id']}/lines").status_code == 200
    for response in (
        _action(world.owner, inventory, "validate"),
        world.owner.post(BASE, json={"site_id": world.site, "inventory_type": "FULL"}),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "subscription_restricted"
    assert sh.level(owner_db, world, 0)[0] == "10.000"


def test_standard_plan_includes_inventories(provision: Any, api_for: Any) -> None:
    provision("gamma", profile="quincaillerie", plan="STANDARD")
    api: Api = api_for("owner@gamma.example.com")
    caps = api.get("/me/capabilities").json()
    assert "inventory_count" in {m["code"] for m in caps["modules"] if m["status"] == "available"}
    assert set(caps["permissions"]) >= PERMISSIONS
    category = api.post("/catalog/categories", json={"name": "Divers"}).json()
    article = api.post(
        "/catalog/articles",
        json={
            "reference": "S-1",
            "designation": "Standard",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "10",
            "sale_price": "15",
        },
    ).json()
    created = api.post(BASE, json={"inventory_type": "TARGETED", "article_ids": [article["id"]]})
    # Un seul site (plan STANDARD) : site obligatoire faute de site sélectionné.
    assert created.status_code == 422 and created.json()["code"] == "site_required"
    site = api.get("/sites").json()[0]["id"]
    assert (
        api.post(
            BASE,
            json={"site_id": site, "inventory_type": "TARGETED", "article_ids": [article["id"]]},
        ).status_code
        == 201
    )


def test_module_deactivation_blocks_routes(world: World, owner_db: Session) -> None:
    owner_db.execute(
        text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'inventory_count'")
    )
    owner_db.commit()
    denied = world.owner.get(BASE)
    assert denied.status_code == 403 and denied.json()["code"] == "module_unavailable"


# --- Audit ----------------------------------------------------------------------------------------


def test_audit_trail(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    inventory = _create(world, "TARGETED", [0, 1])
    world.owner.put(
        f"{BASE}/{inventory['id']}",
        json={"comment": "Rayon A", "remove_article_ids": [world.articles[1]]},
    )
    _action(world.owner, inventory, "start")
    _count(world, inventory, {0: "8"})
    _action(world.owner, inventory, "complete-counting")
    _action(world.owner, inventory, "reopen-counting")
    _action(world.owner, inventory, "complete-counting")
    _action(world.owner, inventory, "validate")
    other = _create(world, "TARGETED", [1])
    _action(world.owner, other, "cancel", json={"reason": "Doublon de saisie"})

    logs = world.owner.get("/audit-logs", params={"action": "inventory.", "limit": 100}).json()[
        "items"
    ]
    mine = [log for log in logs if log["entity_type"] == "inventory"]
    actions = sorted(log["action"] for log in mine if log["entity_id"] == inventory["id"])
    assert actions == sorted(
        [
            "inventory.created",
            "inventory.updated",
            "inventory.started",
            "inventory.counted",
            "inventory.count_completed",
            "inventory.counting_reopened",
            "inventory.count_completed",
            "inventory.validated",
        ]
    )
    by_action = {log["action"]: log for log in mine if log["entity_id"] == inventory["id"]}
    validated = by_action["inventory.validated"]
    assert validated["site_id"] == world.site and validated["user_id"]
    assert validated["data"]["number"] == inventory["number"]
    assert validated["data"]["shortage"] == 1 and validated["data"]["movements"] == 1
    assert validated["data"]["adjustment_value"] == "-200.00"
    counted = by_action["inventory.counted"]["data"]["changes"]
    assert counted == [{"reference": "A-0", "before": None, "after": "8.000"}]
    cancelled = [log for log in mine if log["entity_id"] == other["id"]]
    assert "inventory.cancelled" in {log["action"] for log in cancelled}


# --- Multi-tenant ---------------------------------------------------------------------------------


def test_isolation_between_tenants_api(world: World, provision: Any, api_for: Any) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    inventory = _ready(world, {0: "9"})
    provision("beta", profile="quincaillerie", plan="ENTREPRISE")
    beta: Api = api_for("owner@beta.example.com")
    assert beta.get(BASE).json()["total"] == 0
    url = f"{BASE}/{inventory['id']}"
    line_id = _line_ids(world, inventory)[world.articles[0]]
    for response in (
        beta.get(url),
        beta.get(f"{url}/lines"),
        beta.put(url, json={"comment": "x"}),
        beta.patch(
            f"{url}/lines", json={"counts": [{"line_id": line_id, "quantity_physical": "1"}]}
        ),
        _action(beta, inventory, "validate"),
        _action(beta, inventory, "cancel", json={"reason": "Piratage"}),
    ):
        assert response.status_code == 404 and response.json()["code"] == "inventory_not_found"
    # Article d'un autre tenant : introuvable.
    beta_site = beta.get("/sites").json()[0]["id"]
    foreign = beta.post(
        BASE,
        json={
            "site_id": beta_site,
            "inventory_type": "TARGETED",
            "article_ids": [world.articles[0]],
        },
    )
    assert foreign.status_code == 422 and foreign.json()["code"] == "article_not_found"
    assert world.owner.get(url).json()["status"] == "READY_TO_VALIDATE"


def test_isolation_with_app_role_and_rls(
    world: World, provision: Any, app_engine: Engine, owner_db: Session
) -> None:
    inventory = _create(world)
    b = provision("beta")
    tenant_a = owner_db.execute(text("SELECT tenant_id FROM inventories")).scalar_one()
    # Sans contexte tenant : rien n'est visible.
    with create_session_factory(app_engine)() as db:
        assert db.execute(text("SELECT count(*) FROM inventories")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM inventory_lines")).scalar_one() == 0
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.execute(text("SELECT count(*) FROM inventories")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM inventory_lines")).scalar_one() == 0
        for sql in (
            "UPDATE inventories SET comment = 'x' WHERE id = :id",
            "UPDATE inventory_lines SET quantity_physical = 1 WHERE inventory_id = :id",
            "DELETE FROM inventory_lines WHERE inventory_id = :id",
        ):
            assert db.execute(text(sql), {"id": inventory["id"]}).rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO inventories (id, tenant_id, number, site_id, status, "
                    "inventory_type) VALUES (:id, :tenant, 'INV-X', :site, 'DRAFT', 'FULL')"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_a, "site": world.site},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM inventories"))  # jamais de suppression physique
    # Contraintes en base, même en contournant l'application (session propriétaire) :
    # site ou article d'un autre tenant (FK composites), quantité négative, article en double.
    other_site_sql = (
        "INSERT INTO inventories (id, tenant_id, number, site_id, status, inventory_type) "
        "VALUES (:id, :tenant, :number, :site, 'DRAFT', 'FULL')"
    )
    with pytest.raises(IntegrityError):
        owner_db.execute(
            text(other_site_sql),
            {"id": uuid.uuid4(), "tenant": tenant_a, "number": "INV-Z", "site": str(b.site_id)},
        )
    owner_db.rollback()
    line_sql = (
        "INSERT INTO inventory_lines (id, tenant_id, inventory_id, article_id, "
        "stock_theoretical_initial, quantity_physical, counted_at) "
        "VALUES (:id, :tenant, :inventory, :article, 0, :q, now())"
    )
    beta_category = uuid.uuid4()
    beta_article = uuid.uuid4()
    owner_db.execute(
        text(
            "INSERT INTO catalog_categories (id, tenant_id, name, is_active) "
            "VALUES (:id, :t, 'X', true)"
        ),
        {"id": beta_category, "t": b.tenant_id},
    )
    owner_db.execute(
        text(
            "INSERT INTO catalog_articles (id, tenant_id, reference, designation, category_id, "
            "unit, purchase_price, sale_price, min_stock, is_active) "
            "VALUES (:id, :t, 'B-1', 'B', :c, 'u', 0, 0, 0, true)"
        ),
        {"id": beta_article, "t": b.tenant_id, "c": beta_category},
    )
    owner_db.commit()
    for article, quantity in ((beta_article, "1"), (world.articles[1], "-1")):
        with pytest.raises(IntegrityError):
            owner_db.execute(
                text(line_sql),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "inventory": inventory["id"],
                    "article": article,
                    "q": quantity,
                },
            )
        owner_db.rollback()
