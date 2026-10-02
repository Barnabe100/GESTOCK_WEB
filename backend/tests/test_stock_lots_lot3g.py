"""Lot 3-G (ADR-0045) — lots et péremption : stock et réception.

- P1-b LEVÉE (clôture du Lot 3-H) : le suivi par lot est activable en exploitation (constante
  du code, jamais un réglage) ; la fixture ``lot_tracking_open`` reste explicite mais n'a plus
  d'effet ; le refus ``lot_tracking_unavailable`` n'est vérifié qu'en refermant la constante le
  temps d'un test (``monkeypatch``).
- Lot = (article, numéro sans distinction de casse) ; solde par lot et par site, ventilation du
  stock du site (Σ lots = stock) ; réceptions d'achat et de stock initial ; annulation sur le
  même lot ; lot connu reçu avec une autre péremption : refus ; CMUP inchangé (aucun coût par
  lot) ; état de péremption calculé (``tenant_today``, seuil du tenant).
- Sécurité : RLS, FK composites (lot du même article), portée des sites, permissions, coûts.
"""

import re
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.modules.catalog import lot_tracking
from app.platform.context import get_now
from app.shared.clock import utcnow
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def _track(w: World, index: int, *, expiry: bool = True) -> None:
    _ok(
        w.owner.patch(
            f"/catalog/articles/{w.articles[index]}",
            json={"lot_tracked": True, "expiry_tracked": expiry},
        )
    )


def _line(
    w: World,
    index: int,
    quantity: str,
    cost: str = "100",
    lot: str | None = None,
    expiry: str | None = None,
    made: str | None = None,
    packaging: str | None = None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "article_id": w.articles[index],
        "quantity": quantity,
        "unit_cost": cost,
    }
    for key, value in (
        ("lot_number", lot),
        ("lot_expiry_date", expiry),
        ("lot_manufacturing_date", made),
        ("packaging_id", packaging),
    ):
        if value is not None:
            line[key] = value
    return line


def _draft(
    w: World,
    lines: list[dict[str, Any]],
    site: str | None = None,
    api: Api | None = None,
    **extra: Any,
) -> Any:
    body = {"site_id": site or w.site, "supplier_id": w.supplier, "lines": lines, **extra}
    return (api or w.owner).post("/stock/entries", json=body)


def _receive(
    w: World, lines: list[dict[str, Any]], site: str | None = None, **extra: Any
) -> dict[str, Any]:
    entry = _ok(_draft(w, lines, site, **extra), 201)
    return dict(_ok(w.owner.post(f"/stock/entries/{entry['id']}/validate")))


def _lots(api: Api, **params: Any) -> list[dict[str, Any]]:
    return list(_ok(api.get("/stock/lots", params={"limit": 100, **params}))["items"])


def _lot(w: World, number: str, api: Api | None = None, **params: Any) -> dict[str, Any]:
    found = [lot for lot in _lots(api or w.owner, **params) if lot["number"] == number]
    assert len(found) == 1, found
    return found[0]


def _lot_levels(owner_db: Session, lot_id: str) -> dict[str, str]:
    owner_db.expire_all()
    rows = owner_db.execute(
        text("SELECT site_id::text, quantity::text FROM stock_lot_levels WHERE lot_id = :l"),
        {"l": lot_id},
    ).all()
    return {row[0]: row[1] for row in rows}


def _invariant(owner_db: Session, w: World, index: int, site: str | None = None) -> None:
    """Σ soldes des lots du site = stock (site, article) (D1)."""
    owner_db.expire_all()
    total = owner_db.execute(
        text(
            "SELECT COALESCE(sum(quantity), 0)::numeric(18,3)::text FROM stock_lot_levels "
            "WHERE site_id = :s AND article_id = :a"
        ),
        {"s": site or w.site, "a": w.articles[index]},
    ).scalar_one()
    assert total == sh.level(owner_db, w, index, site)[0]


def _member(w: World, client: TestClient, email: str, permissions: list[str], **access: Any) -> Api:
    role = _ok(
        w.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions}), 201
    )
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{"role_id": role["id"]}],
        **(access or {"all_sites": True}),
    }
    _ok(w.owner.post("/members", json=body), 201)
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _audit(w: World, prefix: str) -> list[dict[str, Any]]:
    items = _ok(w.owner.get("/audit-logs", params={"action": prefix, "limit": 50}))["items"]
    return list(reversed(items))


def _today() -> date:
    return utcnow().astimezone(ZoneInfo("Africa/Ouagadougou")).date()


def _iso(days: int) -> str:
    return (_today() + timedelta(days=days)).isoformat()


# --- 1-3. P1-b levée ------------------------------------------------------------------------------


def test_lot_tracking_available_in_production(world: World, owner_db: Session) -> None:
    """P1-b levée : activation RÉELLE (aucune fixture) sur un article géré en stock à stock nul ;
    règles des réglages conservées (péremption ⇒ lot ⇒ géré en stock) ; activation auditée."""
    assert lot_tracking.LOT_TRACKING_AVAILABLE is True
    assert _ok(world.owner.get("/catalog/lot-tracking")) == {"available": True}
    article = world.articles[0]
    # Le suivi de péremption suppose le suivi par lot : refusé seul.
    assert _code(
        world.owner.patch(f"/catalog/articles/{article}", json={"expiry_tracked": True})
    ) == (422, "expiry_tracking_requires_lots")
    # Lot seul, puis péremption.
    _ok(world.owner.patch(f"/catalog/articles/{article}", json={"lot_tracked": True}))
    detail = _ok(world.owner.get(f"/catalog/articles/{article}"))
    assert (detail["lot_tracked"], detail["expiry_tracked"]) == (True, False)
    _ok(world.owner.patch(f"/catalog/articles/{article}", json={"expiry_tracked": True}))
    detail = _ok(world.owner.get(f"/catalog/articles/{article}"))
    assert (detail["lot_tracked"], detail["expiry_tracked"]) == (True, True)
    audit = _audit(world, "article.updated")
    assert {"before": False, "after": True} in [a["data"].get("lot_tracked") for a in audit]
    # Désormais : le lot est exigé à la réception, puis reçu et soldé par lot.
    assert _code(_draft(world, [_line(world, 0, "5")])) == (422, "lot_number_required")
    _receive(world, [_line(world, 0, "5", lot="L1", expiry=_iso(60))])
    _invariant(owner_db, world, 0)
    # Article créé directement suivi (géré en stock) ; incohérences refusées à la création.
    category = _ok(world.owner.get("/catalog/articles", params={"limit": 1}))["items"][0]
    base = {"designation": "Lait", "category_id": category["category_id"], "unit": "u"}
    created = _ok(
        world.owner.post(
            "/catalog/articles",
            json={**base, "reference": "LOT-1", "lot_tracked": True, "expiry_tracked": True},
        ),
        201,
    )
    assert (created["lot_tracked"], created["expiry_tracked"]) == (True, True)
    assert _code(
        world.owner.post(
            "/catalog/articles",
            json={**base, "reference": "LOT-2", "lot_tracked": False, "expiry_tracked": True},
        )
    ) == (422, "expiry_tracking_requires_lots")
    assert _code(
        world.owner.post(
            "/catalog/articles",
            json={**base, "reference": "LOT-3", "stock_managed": False, "lot_tracked": True},
        )
    ) == (422, "lot_tracking_requires_stock")


def test_lot_tracking_refused_if_the_gate_were_closed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le mécanisme de disponibilité reste en place : constante refermée (test seulement), le
    serveur refuse l'activation, par modification comme par création."""
    monkeypatch.setattr(lot_tracking, "LOT_TRACKING_AVAILABLE", False)
    assert _ok(world.owner.get("/catalog/lot-tracking")) == {"available": False}
    article = world.articles[0]
    for body in ({"lot_tracked": True}, {"lot_tracked": True, "expiry_tracked": True}):
        assert _code(world.owner.patch(f"/catalog/articles/{article}", json=body)) == (
            422,
            "lot_tracking_unavailable",
        )
    category = _ok(world.owner.get("/catalog/articles", params={"limit": 1}))["items"][0]
    created = world.owner.post(
        "/catalog/articles",
        json={
            "reference": "LOT-1",
            "designation": "Lait",
            "category_id": category["category_id"],
            "unit": "u",
            "lot_tracked": True,
        },
    )
    assert _code(created) == (422, "lot_tracking_unavailable")
    detail = _ok(world.owner.get(f"/catalog/articles/{article}"))
    assert (detail["lot_tracked"], detail["expiry_tracked"]) == (False, False)


def test_lot_tracking_gate_is_a_code_constant_never_changed_by_the_app() -> None:
    """La disponibilité est une constante du code (``True`` depuis la levée de P1-b) : ni
    variable d'environnement, ni réglage, ni donnée ; aucun module de l'application ne la
    modifie."""
    source = (APP_DIR / "modules/catalog/lot_tracking.py").read_text()
    assert "LOT_TRACKING_AVAILABLE: Final[bool] = True" in source
    assert lot_tracking.LOT_TRACKING_AVAILABLE is True
    assert not re.search(r"environ|getenv|settings|os\.", source.split('"""', 2)[2])
    assigning = [
        path
        for path in APP_DIR.rglob("*.py")
        if re.search(r"LOT_TRACKING_AVAILABLE\s*[:=]|setattr\([^)]*LOT_TRACKING", path.read_text())
    ]
    assert assigning == [APP_DIR / "modules/catalog/lot_tracking.py"]


# --- Réglages de l'article (D6, D7) ---------------------------------------------------------------


def test_tracking_changes_only_at_zero_stock(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "5", "100")])
    # D6 : activation refusée tant que du stock existe sur un site.
    assert _code(
        world.owner.patch(f"/catalog/articles/{world.articles[0]}", json={"lot_tracked": True})
    ) == (409, "article_has_stock")
    # Article non géré en stock : aucun suivi par lot.
    _ok(world.owner.patch(f"/catalog/articles/{world.articles[2]}", json={"stock_managed": False}))
    assert _code(
        world.owner.patch(f"/catalog/articles/{world.articles[2]}", json={"lot_tracked": True})
    ) == (422, "lot_tracking_requires_stock")
    # D7 : désactivation refusée tant qu'un lot a un solde ; permise une fois tout à zéro.
    _track(world, 1)
    received = _receive(world, [_line(world, 1, "4", lot="L1", expiry=_iso(90))])
    assert _code(
        world.owner.patch(f"/catalog/articles/{world.articles[1]}", json={"lot_tracked": False})
    ) == (422, "expiry_tracking_requires_lots")
    off = {"lot_tracked": False, "expiry_tracked": False}
    assert _code(world.owner.patch(f"/catalog/articles/{world.articles[1]}", json=off)) == (
        409,
        "article_has_stock",
    )
    assert _code(
        world.owner.patch(f"/catalog/articles/{world.articles[1]}", json={"stock_managed": False})
    ) == (422, "lot_tracking_requires_stock")
    _ok(world.owner.post(f"/stock/entries/{received['id']}/cancel", json={"reason": "Erreur"}))
    _ok(world.owner.patch(f"/catalog/articles/{world.articles[1]}", json=off))
    _invariant(owner_db, world, 1)


# --- 4. Article non suivi inchangé ----------------------------------------------------------------


def test_untracked_article_unchanged(world: World, lot_tracking_open: None) -> None:
    _track(world, 0)
    # Article non suivi : aucun lot demandé ni accepté ; une ligne par présentation.
    for field, value in (
        ("lot", "L1"),
        ("expiry", _iso(10)),
        ("made", _iso(-10)),
    ):
        assert _code(_draft(world, [_line(world, 1, "1", **{field: value})])) == (
            422,
            "article_not_lot_tracked",
        )
    assert _code(_draft(world, [_line(world, 1, "1"), _line(world, 1, "2")])) == (
        422,
        "duplicate_article_line",
    )
    received = _receive(world, [_line(world, 1, "3", "200")])
    assert received["lines"][0]["lot_number"] is None
    assert received["lines"][0]["lot_state"] is None
    # Sortie, vente et transfert d'un article non suivi : inchangés, aucun lot.
    exit_doc = sh.exit_doc(world, [(1, "1")])
    _ok(world.owner.post(f"/stock/exits/{exit_doc['id']}/validate"))
    movements = _ok(world.owner.get("/stock/movements", params={"article_id": world.articles[1]}))
    assert {m["lot_id"] for m in movements["items"]} == {None}
    assert _lots(world.owner) == []


# --- 5-6. Réception d'un lot ----------------------------------------------------------------------


def test_reception_creates_lot_balance_and_movement(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _track(world, 2, expiry=False)
    # D8 : lot obligatoire dès le brouillon ; péremption obligatoire si suivie (D5).
    assert _code(_draft(world, [_line(world, 0, "5")])) == (422, "lot_number_required")
    assert _code(_draft(world, [_line(world, 0, "5", lot="L1")])) == (422, "lot_expiry_required")
    # Article suivi sans péremption : date facultative.
    entry = _ok(
        _draft(
            world,
            [_line(world, 0, "5", lot="  L-001 ", expiry=_iso(60)), _line(world, 2, "2", lot="X9")],
        ),
        201,
    )
    assert entry["lines"][0]["lot_number"] == "L-001"  # espaces de bord retirés
    assert entry["lines"][0]["lot_id"] is None  # résolu à la validation
    # Brouillon modifiable : lot corrigé avant validation.
    entry = _ok(
        world.owner.put(
            f"/stock/entries/{entry['id']}",
            json={
                "supplier_id": world.supplier,
                "lines": [
                    _line(world, 0, "5", "120", lot="L-002", expiry=_iso(60), made=_iso(-5)),
                    _line(world, 2, "2", lot="X9"),
                ],
            },
        )
    )
    assert _lots(world.owner) == []  # aucun lot avant la validation
    validated = _ok(world.owner.post(f"/stock/entries/{entry['id']}/validate"))
    line = validated["lines"][0]
    lot = _lot(world, "L-002")
    assert line["lot_id"] == lot["id"]
    assert (lot["expiry_date"], lot["manufacturing_date"]) == (_iso(60), _iso(-5))
    assert (lot["quantity"], lot["site_count"], lot["state"]) == ("5.000", 1, "ok")
    assert _lot(world, "X9")["state"] == "no_expiry"
    assert _lot_levels(owner_db, lot["id"]) == {world.site: "5.000"}
    _invariant(owner_db, world, 0)
    # Mouvement d'entrée tracé avec le lot ; CMUP du site comme sans lot (C1).
    movements = _ok(world.owner.get("/stock/movements", params={"lot_id": lot["id"]}))["items"]
    assert [(m["movement_type"], m["quantity"], m["lot_number"]) for m in movements] == [
        ("ENTRY", "5.000", "L-002")
    ]
    assert sh.level(owner_db, world, 0) == ("5.000", "120.0000")
    # Réceptions du lot ; audit de la création et de la validation.
    entries = _ok(world.owner.get("/stock/entries", params={"lot_id": lot["id"]}))["items"]
    assert [e["id"] for e in entries] == [entry["id"]]
    created = _audit(world, "stock_lot.created")
    assert {a["data"]["lot_number"] for a in created} == {"L-002", "X9"}
    assert _audit(world, "stock_entry.validated")[-1]["data"]["lots"][0] == {
        "reference": "A-0",
        "lot_number": "L-002",
        "base_quantity": "5.000",
    }


def test_cmup_unchanged_by_lots(world: World, lot_tracking_open: None, owner_db: Session) -> None:
    """C1 : même suite de réceptions sur un article suivi et sur un article non suivi."""
    _track(world, 0)
    _receive(world, [_line(world, 0, "10", "100", lot="A", expiry=_iso(30))])
    _receive(world, [_line(world, 0, "30", "180", lot="B", expiry=_iso(60))])
    _receive(world, [_line(world, 1, "10", "100")])
    _receive(world, [_line(world, 1, "30", "180")])
    assert sh.level(owner_db, world, 0) == sh.level(owner_db, world, 1) == ("40.000", "160.0000")


# --- 7-10. Plusieurs lots, lot connu, présentations -----------------------------------------------


def test_two_lots_of_one_article_in_one_reception(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    lines = [
        _line(world, 0, "3", lot="L1", expiry=_iso(20)),
        _line(world, 0, "7", lot="L2", expiry=_iso(40)),
    ]
    _receive(world, lines)
    assert {(lot["number"], lot["quantity"]) for lot in _lots(world.owner)} == {
        ("L1", "3.000"),
        ("L2", "7.000"),
    }
    _invariant(owner_db, world, 0)
    # Même lot deux fois dans la même présentation (casse indifférente) : refus.
    same = [
        _line(world, 0, "1", lot="L3", expiry=_iso(20)),
        _line(world, 0, "1", lot="l3", expiry=_iso(20)),
    ]
    assert _code(_draft(world, same)) == (422, "duplicate_article_line")
    # Même lot saisi avec des dates différentes dans un même document : refus.
    mixed = [
        _line(world, 0, "1", lot="L4", expiry=_iso(20), packaging=None),
        _line(world, 0, "1", lot="L4", expiry=_iso(21), packaging=_carton(world)),
    ]
    assert _code(_draft(world, mixed)) == (422, "lot_data_inconsistent")


def _carton(w: World, index: int = 0) -> str:
    response = w.owner.post(
        f"/catalog/articles/{w.articles[index]}/packagings",
        json={"name": "Carton 24", "conversion": "24"},
    )
    if response.status_code == 409:  # déjà créé dans ce test
        items = _ok(w.owner.get(f"/catalog/articles/{w.articles[index]}/packagings"))["items"]
        return str(items[0]["id"])
    return str(_ok(response, 201)["id"])


def test_known_lot_same_expiry_adds_quantity(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _receive(world, [_line(world, 0, "4", lot="L1", expiry=_iso(50))])
    # Même numéro (autre casse), même péremption, autre fournisseur, autre site : même lot.
    other = _ok(world.owner.post("/suppliers", json={"name": "Autre fournisseur"}), 201)["id"]
    _receive(world, [_line(world, 0, "6", lot="l1", expiry=_iso(50))], supplier_id=other)
    _receive(world, [_line(world, 0, "2", lot="L1", expiry=_iso(50))], site=world.site2)
    lots = _lots(world.owner)
    assert [(lot["number"], lot["quantity"], lot["site_count"]) for lot in lots] == [
        ("L1", "12.000", 2)
    ]
    assert _lot_levels(owner_db, lots[0]["id"]) == {world.site: "10.000", world.site2: "2.000"}
    _invariant(owner_db, world, 0)
    _invariant(owner_db, world, 0, world.site2)
    assert len(_audit(world, "stock_lot.created")) == 1


def test_known_lot_with_other_expiry_refused(world: World, lot_tracking_open: None) -> None:
    _track(world, 0)
    # Brouillon préparé avant que le lot n'existe.
    pending = _ok(_draft(world, [_line(world, 0, "1", lot="L1", expiry=_iso(31))]), 201)
    _receive(world, [_line(world, 0, "4", lot="L1", expiry=_iso(30), made=_iso(-30))])
    # D4 : refus dès le brouillon…
    response = _draft(world, [_line(world, 0, "1", lot="L1", expiry=_iso(31))])
    assert _code(response) == (422, "lot_expiry_mismatch")
    assert response.json()["expiry_date"] == _iso(30)
    # … et revérifié à la validation (lot créé entre-temps).
    assert _code(world.owner.post(f"/stock/entries/{pending['id']}/validate")) == (
        422,
        "lot_expiry_mismatch",
    )
    # Fabrication renseignée et différente de celle du lot : refus ; absente : acceptée.
    assert _code(
        _draft(world, [_line(world, 0, "1", lot="L1", expiry=_iso(30), made=_iso(-29))])
    ) == (422, "lot_manufacturing_mismatch")
    _receive(world, [_line(world, 0, "1", lot="L1", expiry=_iso(30))])
    assert _lot(world, "L1")["quantity"] == "5.000"


def test_one_lot_in_several_presentations(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    carton = _carton(world)
    received = _receive(
        world,
        [
            _line(world, 0, "2", "2400", lot="L001", expiry=_iso(100), packaging=carton),
            _line(world, 0, "10", "100", lot="L001", expiry=_iso(100)),
        ],
    )
    assert {line["lot_id"] for line in received["lines"]} == {_lot(world, "L001")["id"]}
    # Quantité du lot en unité de base : 2 × 24 + 10 = 58.
    assert _lot(world, "L001")["quantity"] == "58.000"
    _invariant(owner_db, world, 0)
    movements = _ok(
        world.owner.get("/stock/movements", params={"lot_id": received["lines"][0]["lot_id"]})
    )
    assert sorted(m["quantity"] for m in movements["items"]) == ["10.000", "48.000"]


# --- 11. Stock initial ----------------------------------------------------------------------------


def test_initial_stock_with_lots(world: World, lot_tracking_open: None, owner_db: Session) -> None:
    _track(world, 0)
    body = {"kind": "INITIAL_STOCK", "supplier_id": None}
    assert _code(_draft(world, [_line(world, 0, "8")], **body)) == (422, "lot_number_required")
    received = _receive(world, [_line(world, 0, "8", lot="INIT-1", expiry=_iso(15))], **body)
    assert received["kind"] == "INITIAL_STOCK"
    assert _lot(world, "INIT-1")["quantity"] == "8.000"
    _invariant(owner_db, world, 0)


# --- 12-13. Annulation ----------------------------------------------------------------------------


def test_cancellation_targets_the_same_lot(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    first = _receive(world, [_line(world, 0, "10", "100", lot="L1", expiry=_iso(30))])
    _receive(world, [_line(world, 0, "5", "160", lot="L1", expiry=_iso(30))])
    cmup = sh.level(owner_db, world, 0)[1]
    _ok(world.owner.post(f"/stock/entries/{first['id']}/cancel", json={"reason": "Erreur de lot"}))
    lot = _lot(world, "L1")
    assert lot["quantity"] == "5.000"
    assert sh.level(owner_db, world, 0) == ("5.000", cmup)  # CMUP inchangé (ENT-08)
    _invariant(owner_db, world, 0)
    movements = _ok(world.owner.get("/stock/movements", params={"lot_id": lot["id"]}))["items"]
    cancellation = next(m for m in movements if m["movement_type"] == "CANCELLATION")
    assert (cancellation["quantity"], cancellation["lot_number"]) == ("-10.000", "L1")
    # Idempotence : une réception déjà annulée ne s'annule pas deux fois.
    assert _code(
        world.owner.post(f"/stock/entries/{first['id']}/cancel", json={"reason": "Encore"})
    ) == (409, "document_not_validated")


def test_cancellation_refused_when_lot_would_go_negative(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    received = _receive(world, [_line(world, 0, "10", lot="L1", expiry=_iso(30))])
    _receive(world, [_line(world, 0, "10", lot="L2", expiry=_iso(30))])
    # Consommation de L1 simulée (elle viendra avec le Lot 3-H) : 6 sur 10, invariant conservé.
    lot = _lot(world, "L1")
    owner_db.execute(
        text("UPDATE stock_lot_levels SET quantity = 4 WHERE lot_id = :l"), {"l": lot["id"]}
    )
    owner_db.execute(
        text("UPDATE stock_levels SET quantity = 14 WHERE site_id = :s AND article_id = :a"),
        {"s": world.site, "a": world.articles[0]},
    )
    owner_db.commit()
    response = world.owner.post(
        f"/stock/entries/{received['id']}/cancel", json={"reason": "Erreur"}
    )
    assert _code(response) == (422, "insufficient_lot_stock")
    assert response.json()["lots"][0]["lot_number"] == "L1"
    assert response.json()["lots"][0]["available"] == "4.000"
    # Refus total : rien n'a bougé.
    assert _lot(world, "L1")["quantity"] == "4.000"
    assert sh.level(owner_db, world, 0)[0] == "14.000"
    assert _ok(world.owner.get(f"/stock/entries/{received['id']}"))["status"] == "VALIDATED"


# --- 14-18. Péremption ----------------------------------------------------------------------------


def test_expiry_states_threshold_and_filters(world: World, lot_tracking_open: None) -> None:
    _track(world, 0)
    _track(world, 1, expiry=False)
    _receive(
        world,
        [
            _line(world, 0, "1", lot="PAST", expiry=_iso(-1)),  # D17 : accepté, périmé
            _line(world, 0, "1", lot="TODAY", expiry=_iso(0)),  # aujourd'hui : bientôt périmé
            _line(world, 0, "1", lot="D30", expiry=_iso(30)),  # dans le seuil (30 j par défaut)
            _line(world, 0, "1", lot="D31", expiry=_iso(31)),  # au-delà
            _line(world, 1, "1", lot="NONE"),
        ],
    )
    states = {lot["number"]: lot["state"] for lot in _lots(world.owner)}
    assert states == {
        "PAST": "expired",
        "TODAY": "expiring_soon",
        "D30": "expiring_soon",
        "D31": "ok",
        "NONE": "no_expiry",
    }
    # Tri par défaut : échéance la plus proche d'abord, lots sans date en fin.
    assert [lot["number"] for lot in _lots(world.owner)] == ["PAST", "TODAY", "D30", "D31", "NONE"]
    assert [lot["number"] for lot in _lots(world.owner, state="expiring_soon")] == ["TODAY", "D30"]
    assert [lot["number"] for lot in _lots(world.owner, expires_before=_iso(0))] == [
        "PAST",
        "TODAY",
    ]
    assert [lot["number"] for lot in _lots(world.owner, search="d3", sort="-number")] == [
        "D31",
        "D30",
    ]
    assert {lot["number"] for lot in _lots(world.owner, article_id=world.articles[1])} == {"NONE"}
    # Seuil du tenant (D16) : 0 jour → seul aujourd'hui est « bientôt périmé ».
    assert _ok(world.owner.get("/stock/settings")) == {"expiry_warning_days": 30}
    _ok(world.owner.put("/stock/settings", json={"expiry_warning_days": 0}))
    states = {lot["number"]: lot["state"] for lot in _lots(world.owner)}
    assert (states["TODAY"], states["D30"]) == ("expiring_soon", "ok")
    assert _audit(world, "stock_settings.updated")[-1]["data"]["expiry_warning_days"] == {
        "before": 30,
        "after": 0,
    }
    for value in (-1, 366):
        assert (
            world.owner.put("/stock/settings", json={"expiry_warning_days": value}).status_code
            == 422
        )
    # État aussi sur les lignes de la réception.
    entry = _ok(world.owner.get("/stock/entries", params={"limit": 1}))["items"][0]
    lines = _ok(world.owner.get(f"/stock/entries/{entry['id']}"))["lines"]
    assert {line["lot_number"]: line["lot_state"] for line in lines}["PAST"] == "expired"


def test_manufacturing_after_expiry_refused(world: World, lot_tracking_open: None) -> None:
    _track(world, 0)
    assert _code(
        _draft(world, [_line(world, 0, "1", lot="L1", expiry=_iso(10), made=_iso(11))])
    ) == (
        422,
        "lot_dates_invalid",
    )
    _receive(world, [_line(world, 0, "1", lot="L1", expiry=_iso(10), made=_iso(10))])


def test_expiry_uses_tenant_today_not_server_date(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    """Fuseau UTC+14 : à 20 h UTC, le tenant est déjà le lendemain — un lot qui expire « ce jour »
    selon le serveur est PÉRIMÉ pour le tenant."""
    _track(world, 0)
    server_day = utcnow().date()
    now = datetime(server_day.year, server_day.month, server_day.day, 20, 0, tzinfo=UTC)
    owner_db.execute(
        text(
            "UPDATE tenants SET timezone = 'Pacific/Kiritimati' "
            "WHERE id = (SELECT tenant_id FROM sites WHERE id = :s)"
        ),
        {"s": world.site},
    )
    owner_db.commit()
    app.dependency_overrides[get_now] = lambda: now
    try:
        _receive(world, [_line(world, 0, "1", lot="L1", expiry=server_day.isoformat())])
        assert _lot(world, "L1")["state"] == "expired"
        tomorrow = (server_day + timedelta(days=1)).isoformat()
        _receive(world, [_line(world, 0, "1", lot="L2", expiry=tomorrow)])
        assert _lot(world, "L2")["state"] == "expiring_soon"
    finally:
        app.dependency_overrides.pop(get_now, None)


# --- 19-20. Isolation et FK composites ------------------------------------------------------------


def test_tenant_isolation_rls_and_rights(
    world: World,
    lot_tracking_open: None,
    provision: Any,
    api_for: Any,
    db: Session,
    owner_db: Session,
) -> None:
    _track(world, 0)
    _receive(world, [_line(world, 0, "3", lot="L1", expiry=_iso(30))])
    _ok(world.owner.put("/stock/settings", json={"expiry_warning_days": 7}))
    lot = _lot(world, "L1")
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    assert _lots(other) == []
    assert _code(other.get(f"/stock/lots/{lot['id']}")) == (404, "stock_lot_not_found")
    assert _ok(other.get("/stock/movements", params={"lot_id": lot["id"]}))["total"] == 0
    assert _ok(other.get("/stock/entries", params={"lot_id": lot["id"]}))["total"] == 0
    assert _ok(other.get("/stock/settings")) == {"expiry_warning_days": 30}
    set_db_context(db, tenant_id=beta.tenant_id)
    for table in ("stock_lots", "stock_lot_levels", "stock_settings"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    alpha = owner_db.execute(
        text("SELECT tenant_id FROM stock_lots WHERE id = :id"), {"id": lot["id"]}
    ).scalar_one()
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO stock_lots (id, tenant_id, article_id, number) "
                "VALUES (gen_random_uuid(), :t, :a, 'PIRATE')"
            ),
            {"t": alpha, "a": world.articles[0]},
        )
    db.rollback()
    # Droits minimaux : lots figés (ni UPDATE ni DELETE), soldes modifiables en quantité seule.
    for sql in (
        "UPDATE stock_lots SET number = 'X' WHERE id = :id",
        "DELETE FROM stock_lots WHERE id = :id",
        "UPDATE stock_lot_levels SET lot_id = :id",
        "DELETE FROM stock_lot_levels WHERE lot_id = :id",
    ):
        set_db_context(db, tenant_id=alpha)
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(sql), {"id": lot["id"]})
        db.rollback()


def test_composite_fk_lot_of_same_article_only(
    world: World, lot_tracking_open: None, db: Session, owner_db: Session
) -> None:
    _track(world, 0)
    _track(world, 1)
    received = _receive(world, [_line(world, 0, "3", lot="L1", expiry=_iso(30))])
    lot = _lot(world, "L1")
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM stock_lots WHERE id = :id"), {"id": lot["id"]}
    ).scalar_one()
    set_db_context(db, tenant_id=tenant)
    # Le lot de l'article 0 ne peut pas être rattaché à une ligne, un solde ou un mouvement de
    # l'article 1 (FK composite (tenant, article, lot)).
    statements = (
        "UPDATE stock_entry_lines SET article_id = :b WHERE id = :line",
        "INSERT INTO stock_lot_levels (id, tenant_id, site_id, article_id, lot_id, quantity) "
        "VALUES (gen_random_uuid(), :t, :s, :b, :lot, 1)",
    )
    for sql in statements:
        with pytest.raises(IntegrityError, match="stock_lots"):
            db.execute(
                text(sql),
                {
                    "b": world.articles[1],
                    "line": received["lines"][0]["id"],
                    "t": tenant,
                    "s": world.site2,
                    "lot": lot["id"],
                },
            )
        db.rollback()
        set_db_context(db, tenant_id=tenant)
    with pytest.raises(IntegrityError, match="stock_lots"):
        owner_db.execute(
            text(
                "INSERT INTO stock_movements (id, tenant_id, site_id, article_id, movement_type, "
                "quantity, quantity_before, quantity_after, average_cost_before, "
                "average_cost_after, source_type, source_id, source_line_id, lot_id) "
                "VALUES (gen_random_uuid(), :t, :s, :b, 'ENTRY', 1, 0, 1, 0, 0, 'test', "
                "gen_random_uuid(), gen_random_uuid(), :lot)"
            ),
            {"t": tenant, "s": world.site, "b": world.articles[1], "lot": lot["id"]},
        )
    owner_db.rollback()


# --- 21-23. Sites, permissions, coûts -------------------------------------------------------------


def test_site_scope(world: World, lot_tracking_open: None, client: TestClient) -> None:
    _track(world, 0)
    _receive(world, [_line(world, 0, "3", lot="ONLY-1", expiry=_iso(30))])
    _receive(world, [_line(world, 0, "4", lot="BOTH", expiry=_iso(30))])
    _receive(world, [_line(world, 0, "6", lot="BOTH", expiry=_iso(30))], site=world.site2)
    depot = _member(
        world,
        client,
        "depot@alpha.example.com",
        ["stock.level.view", "stock.entry.view", "stock.movement.view", "stock.threshold.manage"],
        all_sites=False,
        site_ids=[world.site2],
    )
    lots = {lot["number"]: lot for lot in _lots(depot)}
    assert set(lots) == {"BOTH"}
    assert (lots["BOTH"]["quantity"], lots["BOTH"]["site_count"]) == ("6.000", 1)
    only = _lot(world, "ONLY-1")
    assert _code(depot.get(f"/stock/lots/{only['id']}")) == (404, "stock_lot_not_found")
    detail = _ok(depot.get(f"/stock/lots/{lots['BOTH']['id']}"))
    assert [b["site_id"] for b in detail["balances"]] == [world.site2]
    owner_detail = _ok(world.owner.get(f"/stock/lots/{lots['BOTH']['id']}"))
    assert {b["site_id"]: b["quantity"] for b in owner_detail["balances"]} == {
        world.site: "4.000",
        world.site2: "6.000",
    }
    assert _ok(depot.get("/stock/movements", params={"lot_id": only["id"]}))["total"] == 0
    assert _ok(depot.get("/stock/entries", params={"lot_id": only["id"]}))["total"] == 0
    # Seuil commun à toute l'entreprise : refusé à un membre limité à un site.
    assert _code(depot.put("/stock/settings", json={"expiry_warning_days": 5})) == (
        403,
        "tenant_wide_access_required",
    )
    # Réception par lot sur un site non accessible : refusée.
    clerk = _member(
        world,
        client,
        "clerk@alpha.example.com",
        ["stock.entry.view", "stock.entry.create"],
        all_sites=False,
        site_ids=[world.site2],
    )
    assert _code(_draft(world, [_line(world, 0, "1", lot="L9", expiry=_iso(5))], api=clerk)) == (
        403,
        "site_access_denied",
    )


def test_permissions(world: World, lot_tracking_open: None, client: TestClient) -> None:
    _track(world, 0)
    _receive(world, [_line(world, 0, "3", lot="L1", expiry=_iso(30))])
    editor = _member(world, client, "editeur@alpha.example.com", ["catalog.article.view"])
    viewer = _member(world, client, "lecteur@alpha.example.com", ["stock.level.view"])
    # Consultation des lots et du seuil : ``stock.level.view``.
    assert editor.get("/stock/lots").status_code == 403
    assert editor.get("/stock/settings").status_code == 403
    assert _lots(viewer)[0]["number"] == "L1"
    assert _ok(viewer.get("/stock/settings")) == {"expiry_warning_days": 30}
    assert viewer.put("/stock/settings", json={"expiry_warning_days": 3}).status_code == 403
    # Réception : permissions des entrées ; réglages de l'article : ``catalog.article.update``.
    assert (
        _draft(world, [_line(world, 0, "1", lot="L2", expiry=_iso(5))], api=viewer).status_code
        == 403
    )
    assert (
        editor.patch(
            f"/catalog/articles/{world.articles[1]}", json={"lot_tracked": True}
        ).status_code
        == 403
    )
    manager = sh.member(world, client, "gestion@alpha.example.com", "manager", all_sites=True)
    _ok(manager.put("/stock/settings", json={"expiry_warning_days": 14}))


def test_costs_hidden_without_cost_view(
    world: World, lot_tracking_open: None, client: TestClient
) -> None:
    _track(world, 0)
    received = _receive(world, [_line(world, 0, "3", "250", lot="L1", expiry=_iso(30))])
    lot = _lot(world, "L1")
    viewer = _member(
        world,
        client,
        "sanscout@alpha.example.com",
        ["stock.level.view", "stock.entry.view", "stock.movement.view"],
    )
    # Les lots ne portent aucun coût (C1).
    assert not {"unit_cost", "average_cost", "amount", "stock_value"} & set(_lots(viewer)[0])
    assert "unit_cost" not in _ok(viewer.get(f"/stock/lots/{lot['id']}"))
    line = _ok(viewer.get(f"/stock/entries/{received['id']}"))["lines"][0]
    assert line["lot_number"] == "L1"
    assert "unit_cost" not in line and "amount" not in line
    movement = _ok(viewer.get("/stock/movements", params={"lot_id": lot["id"]}))["items"][0]
    assert movement["lot_number"] == "L1"
    assert not {"unit_cost", "average_cost_before", "average_cost_after"} & set(movement)
    # Avec ``cost_view`` (propriétaire) : coût de réception visible.
    assert (
        _ok(world.owner.get(f"/stock/entries/{received['id']}"))["lines"][0]["unit_cost"]
        == "250.00"
    )


# --- 24. Concurrence ------------------------------------------------------------------------------


def test_concurrent_receptions_of_the_same_new_lot(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    drafts = [
        _ok(_draft(world, [_line(world, 0, str(q), lot="RACE", expiry=_iso(45))], site=site), 201)
        for q, site in ((3, world.site), (4, world.site), (5, world.site2))
    ]
    barrier = threading.Barrier(len(drafts))
    results: list[int] = []

    def validate(entry: dict[str, Any]) -> None:
        barrier.wait()
        results.append(world.owner.post(f"/stock/entries/{entry['id']}/validate").status_code)

    threads = [threading.Thread(target=validate, args=(d,)) for d in drafts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [200, 200, 200]
    lots = _lots(world.owner)
    assert [(lot["number"], lot["quantity"]) for lot in lots] == [("RACE", "12.000")]
    assert _lot_levels(owner_db, lots[0]["id"]) == {world.site: "7.000", world.site2: "5.000"}
    _invariant(owner_db, world, 0)
    _invariant(owner_db, world, 0, world.site2)
    assert len(_audit(world, "stock_lot.created")) == 1
