"""Lot 3-H (finalisation, ADR-0045) — inventaires par lot et changement de suivi.

- Mode de suivi FIGÉ au démarrage (``inventory_lines.lot_tracked``), relu à la validation
  (``409 inventory_lot_mode_changed``).
- Lots attendus (solde non nul au démarrage), non saisis = 0 ; lots découverts (règles 3-G,
  rattachés au lot existant, créés à la validation seulement, jamais en double) ; lot apparu
  pendant le comptage : ``409 inventory_lots_changed`` puis « Actualiser les lots ».
- Validation : écart par lot = physique − solde COURANT ; un ``ADJUSTMENT`` par lot avec
  écart, même si l'écart de l'article est nul ; invariant Σ lots = stock ; CMUP inchangé.
- Changement de suivi refusé si un document deviendrait incohérent (inventaire ouvert,
  brouillon portant des lots, historique validé annulable sans lot à l'activation).
- P1-b levée (clôture du Lot 3-H) : la fixture ``lot_tracking_open`` reste explicite, sans effet.
"""

import threading
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.modules.catalog import lot_tracking
from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World
from tests.test_stock_lots_lot3g import (
    _audit,
    _carton,
    _code,
    _invariant,
    _iso,
    _line,
    _member,
    _ok,
    _receive,
    _track,
)
from tests.test_stock_lots_lot3ha import _balance, _exit, _lot_id, _pos_body, _stock

BASE = "/inventories"

# --- Aides ----------------------------------------------------------------------------------------


def _inventory(w: World, articles: list[int], site: str | None = None) -> dict[str, Any]:
    """Inventaire ciblé, démarré (comptage en cours)."""
    created = _ok(
        w.owner.post(
            BASE,
            json={
                "site_id": site or w.site,
                "inventory_type": "TARGETED",
                "article_ids": [w.articles[i] for i in articles],
            },
        ),
        201,
    )
    return dict(_ok(w.owner.post(f"{BASE}/{created['id']}/start")))


def _line_of(w: World, inv: dict[str, Any], index: int) -> dict[str, Any]:
    items = _ok(w.owner.get(f"{BASE}/{inv['id']}/lines", params={"limit": 100}))["items"]
    return next(line for line in items if line["article_id"] == w.articles[index])


def _rows(line: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {lot["lot_number"]: lot for lot in line["lots"]}


def _put_lots(
    w: World,
    inv: dict[str, Any],
    index: int,
    counts: dict[str, Any],
    api: Api | None = None,
) -> Any:
    """``counts`` : numéro → quantité (chaîne) ou dict de comptage (conditionnement)."""
    line = _line_of(w, inv, index)
    rows = _rows(line)
    body = []
    for number, value in counts.items():
        entry: dict[str, Any] = {"lot_row_id": rows[number]["id"]}
        entry.update(value if isinstance(value, dict) else {"quantity_physical": value})
        body.append(entry)
    return (api or w.owner).put(
        f"{BASE}/{inv['id']}/lines/{line['id']}/lots", json={"counts": body}
    )


def _discover(
    w: World, inv: dict[str, Any], index: int, number: str, api: Api | None = None, **body: Any
) -> Any:
    line = _line_of(w, inv, index)
    return (api or w.owner).post(
        f"{BASE}/{inv['id']}/lines/{line['id']}/lots", json={"lot_number": number, **body}
    )


def _complete(w: World, inv: dict[str, Any]) -> None:
    _ok(w.owner.post(f"{BASE}/{inv['id']}/complete-counting"))


def _validate(w: World, inv: dict[str, Any], api: Api | None = None) -> Any:
    return (api or w.owner).post(f"{BASE}/{inv['id']}/validate")


def _adjustments(owner_db: Session, inv: dict[str, Any]) -> list[tuple[str, str]]:
    """(numéro du lot, quantité) des ajustements de l'inventaire, dans l'ordre d'écriture."""
    owner_db.expire_all()
    return [
        (row[0] or "-", row[1])
        for row in owner_db.execute(
            text(
                "SELECT s.number, m.quantity::text FROM stock_movements m "
                "LEFT JOIN stock_lots s ON s.id = m.lot_id WHERE m.source_id = :id "
                "AND m.movement_type = 'ADJUSTMENT' ORDER BY s.number"
            ),
            {"id": inv["id"]},
        )
    ]


def _lots_named(owner_db: Session, number: str) -> int:
    return sh.count(owner_db, f"SELECT count(*) FROM stock_lots WHERE number = '{number}'")


def _ab(w: World) -> None:
    """Article 0 suivi : lot A 100 (J+30) et lot B 50 (J+60) sur le site principal."""
    _track(w, 0)
    _stock(w, 0, [("A", "100", _iso(30)), ("B", "50", _iso(60))])


# --- Mode figé, lots attendus ---------------------------------------------------------------------


def test_untracked_article_inventory_unchanged(world: World, owner_db: Session) -> None:
    """Article non suivi : aucune ligne de lot, comptage de la ligne, un ajustement sans lot —
    suivi disponible (P1-b levée, aucune fixture) mais non activé sur l'article."""
    assert lot_tracking.LOT_TRACKING_AVAILABLE is True
    sh.validated_entry(world, [(1, "10", "100")])
    inv = _inventory(world, [1])
    line = _line_of(world, inv, 1)
    assert (line["lot_tracked"], line["lots"]) == (False, [])
    assert inv["lot_tracked_count"] == 0
    assert _code(_put_lots(world, inv, 1, {})) == (422, "inventory_line_not_lot_tracked")
    ids = {line["article_id"]: line["id"]}
    _ok(
        world.owner.patch(
            f"{BASE}/{inv['id']}/lines",
            json={"counts": [{"line_id": ids[world.articles[1]], "quantity_physical": "8"}]},
        )
    )
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("-", "-2.000")]
    assert sh.count(owner_db, "SELECT count(*) FROM inventory_line_lots") == 0


def test_expected_lots_created_at_start_with_initial_balance(
    world: World, lot_tracking_open: None
) -> None:
    _ab(world)
    inv = _inventory(world, [0, 1])
    # Lignes suivies par lot comptées pour l'action « Actualiser les lots » de l'interface.
    assert inv["lot_tracked_count"] == 1
    line = _line_of(world, inv, 0)
    assert line["lot_tracked"] is True
    assert _line_of(world, inv, 1)["lot_tracked"] is False
    rows = _rows(line)
    assert {n: (r["stock_theoretical_initial"], r["discovered"]) for n, r in rows.items()} == {
        "A": ("100.000", False),
        "B": ("50.000", False),
    }
    assert rows["A"]["expiry_date"] == _iso(30)
    assert rows["A"]["stock_current"] == "100.000"
    # Aucun coût dans les lots.
    assert "unit_cost" not in rows["A"] and "adjustment_value" not in rows["A"]


def test_mono_lot_inventory(world: World, lot_tracking_open: None, owner_db: Session) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "8"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("A", "-2.000")]
    assert (_balance(owner_db, world, "A"), sh.level(owner_db, world, 0)[0]) == ("8.000", "8.000")
    _invariant(owner_db, world, 0)


def test_crossed_lot_variances_with_zero_article_variance(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """CRITIQUE : A −5 et B +5 → écart de l'article nul, mais DEUX ajustements par lot."""
    _ab(world)
    inv = _inventory(world, [0])
    line = _ok(_put_lots(world, inv, 0, {"A": "95", "B": "55"}))["lines"][0]
    assert line["quantity_physical"] == "150.000"
    assert {n: r["quantity_variance"] for n, r in _rows(line).items()} == {
        "A": "-5.000",
        "B": "5.000",
    }
    _complete(world, inv)
    validated = _ok(_validate(world, inv))
    assert validated["status"] == "VALIDATED"
    assert _adjustments(owner_db, inv) == [("A", "-5.000"), ("B", "5.000")]
    assert [_balance(owner_db, world, n) for n in "AB"] == ["95.000", "55.000"]
    assert sh.level(owner_db, world, 0)[0] == "150.000"
    _invariant(owner_db, world, 0)
    final = _line_of(world, inv, 0)
    assert final["quantity_variance"] == "0.000"
    assert {n: r["quantity_variance"] for n, r in _rows(final).items()} == {
        "A": "-5.000",
        "B": "5.000",
    }
    audit = _audit(world, "inventory.validated")[-1]["data"]
    assert sorted((lot["lot_number"], lot["variance"]) for lot in audit["lots"]) == [
        ("A", "-5.000"),
        ("B", "5.000"),
    ]
    assert audit["movements"] == 2


def test_expected_lot_not_counted_is_zero(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30)), ("B", "5", _iso(60))])
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "10"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("B", "-5.000")]
    assert _balance(owner_db, world, "B") == "0.000"
    _invariant(owner_db, world, 0)


def test_tracked_line_counted_by_lot_only(world: World, lot_tracking_open: None) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    line = _line_of(world, inv, 0)
    response = world.owner.patch(
        f"{BASE}/{inv['id']}/lines",
        json={"counts": [{"line_id": line["id"], "quantity_physical": "150"}]},
    )
    assert _code(response) == (422, "inventory_line_lot_tracked")
    # Ligne non comptée tant qu'aucun comptage par lot n'est enregistré.
    assert _code(world.owner.post(f"{BASE}/{inv['id']}/complete-counting")) == (
        422,
        "inventory_not_fully_counted",
    )
    # Liste vide : ligne comptée, aucun lot présent (tous à 0).
    counted = _ok(_put_lots(world, inv, 0, {}))["lines"][0]
    assert counted["quantity_physical"] == "0.000"
    _complete(world, inv)


def test_current_stock_differs_from_initial(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Début A 100 ; vente de 20 pendant le comptage (FEFO sur A) ; physique 78 → écart −2."""
    _track(world, 0)
    _stock(world, 0, [("A", "100", _iso(30))])
    inv = _inventory(world, [0])
    _ok(world.owner.post("/pos/checkout", json=_pos_body(world, 0, "20")), 201)
    line = _ok(_put_lots(world, inv, 0, {"A": "78"}))["lines"][0]
    assert _rows(line)["A"]["stock_current"] == "80.000"
    assert _rows(line)["A"]["quantity_variance"] == "-2.000"
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("A", "-2.000")]
    final = _rows(_line_of(world, inv, 0))["A"]
    assert (final["stock_theoretical_initial"], final["stock_theoretical_at_validation"]) == (
        "100.000",
        "80.000",
    )
    _invariant(owner_db, world, 0)


# --- Lots découverts ------------------------------------------------------------------------------


def test_discovered_new_lot_created_at_validation_only(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    line = _ok(_discover(world, inv, 0, "C", expiry_date=_iso(200), quantity_physical="20"), 201)
    rows = _rows(line["lines"][0])
    assert (rows["C"]["discovered"], rows["C"]["lot_id"], rows["C"]["quantity_physical"]) == (
        True,
        None,
        "20.000",
    )
    assert _lots_named(owner_db, "C") == 0  # rien n'est créé avant la validation
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50", "C": "20"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _lots_named(owner_db, "C") == 1
    assert _adjustments(owner_db, inv) == [("C", "20.000")]
    assert (_balance(owner_db, world, "C"), sh.level(owner_db, world, 0)[0]) == (
        "20.000",
        "170.000",
    )
    _invariant(owner_db, world, 0)
    created = _audit(world, "stock_lot.created")[-1]["data"]
    assert (created["lot_number"], created["source_number"]) == ("C", inv["number"])
    assert _audit(world, "inventory.validated")[-1]["data"]["lots_created"] == ["C"]


def test_discovered_lot_known_elsewhere_is_attached(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    _stock(world, 0, [("DEPOT", "7", _iso(90))], site=world.site2)
    depot = _lot_id(world, "DEPOT")
    inv = _inventory(world, [0])
    line = _ok(_discover(world, inv, 0, "depot", expiry_date=_iso(90), quantity_physical="3"), 201)
    assert _rows(line["lines"][0])["DEPOT"]["lot_id"] == depot  # même lot (casse ignorée)
    _ok(_put_lots(world, inv, 0, {"A": "10", "DEPOT": "3"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _lots_named(owner_db, "DEPOT") == 1
    assert (
        _balance(owner_db, world, "DEPOT"),
        _balance(owner_db, world, "DEPOT", world.site2),
    ) == (
        "3.000",
        "7.000",
    )
    _invariant(owner_db, world, 0)


def test_discovered_lot_at_zero_on_same_site_is_attached(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Lot épuisé sur le site (solde nul) : non attendu, mais rattaché s'il est retrouvé."""
    _track(world, 0)
    _stock(world, 0, [("OLD", "5", _iso(30)), ("NEW", "5", _iso(60))])
    _ok(world.owner.post("/pos/checkout", json=_pos_body(world, 0, "5")), 201)  # OLD épuisé
    inv = _inventory(world, [0])
    assert set(_rows(_line_of(world, inv, 0))) == {"NEW"}
    line = _ok(_discover(world, inv, 0, "OLD", expiry_date=_iso(30), quantity_physical="2"), 201)
    assert _rows(line["lines"][0])["OLD"]["lot_id"] == _lot_id(world, "OLD")
    _ok(_put_lots(world, inv, 0, {"NEW": "5", "OLD": "2"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _lots_named(owner_db, "OLD") == 1
    assert _balance(owner_db, world, "OLD") == "2.000"
    _invariant(owner_db, world, 0)


def test_discovery_rules_and_duplicates(world: World, lot_tracking_open: None) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    # Lot attendu saisi comme découvert : doublon.
    assert _code(_discover(world, inv, 0, "a", expiry_date=_iso(30))) == (
        409,
        "duplicate_lot_in_inventory",
    )
    # Règles 3-G : péremption obligatoire, fabrication ≤ péremption, péremption du lot connu.
    assert _code(_discover(world, inv, 0, "X")) == (422, "lot_expiry_required")
    assert _code(
        _discover(world, inv, 0, "X", expiry_date=_iso(10), manufacturing_date=_iso(20))
    ) == (422, "lot_dates_invalid")
    assert _code(_discover(world, inv, 0, "B", expiry_date=_iso(99))) == (
        422,
        "lot_expiry_mismatch",
    )
    _ok(_discover(world, inv, 0, "X", expiry_date=_iso(10)), 201)
    assert _code(_discover(world, inv, 0, "x", expiry_date=_iso(10))) == (
        409,
        "duplicate_lot_in_inventory",
    )
    assert _code(_discover(world, inv, 0, "   ")) == (422, "validation_error")


def test_remove_discovered_lot_only(world: World, lot_tracking_open: None) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    line = _ok(_discover(world, inv, 0, "X", expiry_date=_iso(10), quantity_physical="4"), 201)
    rows = _rows(line["lines"][0])
    base = f"{BASE}/{inv['id']}/lines/{line['lines'][0]['id']}/lots"
    assert _code(world.owner.delete(f"{base}/{rows['A']['id']}")) == (
        422,
        "inventory_lot_not_discovered",
    )
    after = _ok(world.owner.delete(f"{base}/{rows['X']['id']}"))["lines"][0]
    assert set(_rows(after)) == {"A", "B"}
    assert after["quantity_physical"] == "0.000"
    assert _audit(world, "inventory.lot_discovery_removed")[-1]["data"]["lot_number"] == "X"


def test_cancelled_inventory_creates_no_lot(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    _ok(_discover(world, inv, 0, "GHOST", expiry_date=_iso(10), quantity_physical="1"), 201)
    _ok(world.owner.post(f"{BASE}/{inv['id']}/cancel", json={"reason": "Comptage abandonné"}))
    assert _lots_named(owner_db, "GHOST") == 0
    assert sh.level(owner_db, world, 0)[0] == "150.000"


def test_discovered_lot_created_meanwhile_is_attached_not_duplicated(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Cas D : lot découvert NOUVEAU, puis reçu sur un autre site avant la validation — le lot
    créé par la réception est rattaché, jamais dupliqué ; autre péremption : refus."""
    _ab(world)
    inv = _inventory(world, [0])
    _ok(_discover(world, inv, 0, "LATE", expiry_date=_iso(120), quantity_physical="6"), 201)
    _receive(world, [_line(world, 0, "4", lot="LATE", expiry=_iso(120))], world.site2)
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50", "LATE": "6"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _lots_named(owner_db, "LATE") == 1
    assert (_balance(owner_db, world, "LATE"), _balance(owner_db, world, "LATE", world.site2)) == (
        "6.000",
        "4.000",
    )
    _invariant(owner_db, world, 0)


def test_discovered_lot_expiry_mismatch_at_validation(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    _ok(_discover(world, inv, 0, "MIS", expiry_date=_iso(120), quantity_physical="1"), 201)
    _receive(world, [_line(world, 0, "4", lot="MIS", expiry=_iso(150))], world.site2)
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50", "MIS": "1"}))
    _complete(world, inv)
    assert _code(_validate(world, inv)) == (422, "lot_expiry_mismatch")
    assert _lots_named(owner_db, "MIS") == 1
    assert sh.level(owner_db, world, 0)[0] == "150.000"


# --- Lots apparus, actualisation -----------------------------------------------------------------


def test_lot_appeared_during_counting_then_refresh(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50"}))
    _complete(world, inv)
    # Réception d'un nouveau lot sur le site pendant le comptage.
    _receive(world, [_line(world, 0, "12", lot="D", expiry=_iso(90))])
    response = _validate(world, inv)
    assert _code(response) == (409, "inventory_lots_changed")
    assert [lot["lot_number"] for lot in response.json()["lots"]] == ["D"]
    assert sh.level(owner_db, world, 0)[0] == "162.000"  # rien n'est écrit
    refreshed = _ok(world.owner.post(f"{BASE}/{inv['id']}/refresh-lots"))
    assert refreshed["status"] == "COUNTING"  # retour au comptage : D est à compter
    rows = _rows(_line_of(world, inv, 0))
    assert (rows["D"]["stock_theoretical_initial"], rows["D"]["discovered"]) == ("12.000", False)
    assert _audit(world, "inventory.lots_refreshed")[-1]["data"]["lots"][0]["lot_number"] == "D"
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50", "D": "11"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("D", "-1.000")]
    _invariant(owner_db, world, 0)


def test_lot_appeared_by_incoming_transfer(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    _stock(world, 0, [("T", "8", _iso(45))], site=world.site2)
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "10"}))
    lot = _lot_id(world, "T")
    draft = _ok(
        world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site2,
                "destination_site_id": world.site,
                "lines": [
                    {
                        "article_id": world.articles[0],
                        "quantity": "3",
                        "lots": [{"lot_id": lot, "quantity": "3"}],
                    }
                ],
            },
        ),
        201,
    )
    _ok(world.owner.post(f"/stock/transfers/{draft['id']}/validate"))
    _complete(world, inv)
    assert _code(_validate(world, inv)) == (409, "inventory_lots_changed")
    _ok(world.owner.post(f"{BASE}/{inv['id']}/refresh-lots"))
    _ok(_put_lots(world, inv, 0, {"A": "10", "T": "3"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    _invariant(owner_db, world, 0)


# --- Péremption, conditionnements, quantités -----------------------------------------------------


def test_expired_lot_visible_counted_and_adjusted(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _ab(world)
    owner_db.execute(
        text("UPDATE stock_lots SET expiry_date = :d WHERE number = 'A'"), {"d": _iso(-3)}
    )
    owner_db.commit()
    inv = _inventory(world, [0])
    assert _rows(_line_of(world, inv, 0))["A"]["state"] == "expired"
    _ok(_put_lots(world, inv, 0, {"A": "90", "B": "50"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("A", "-10.000")]
    _invariant(owner_db, world, 0)


def test_lot_count_in_packaging_plus_loose_units(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    carton = _carton(world)
    _track(world, 0)
    _stock(world, 0, [("A", "200", _iso(30))])
    inv = _inventory(world, [0])
    line = _ok(
        _put_lots(
            world,
            inv,
            0,
            {"A": {"packaging_id": carton, "packaging_quantity": "8", "unit_quantity": "5"}},
        )
    )["lines"][0]
    row = _rows(line)["A"]
    assert (row["quantity_physical"], row["count_packaging_name"], row["count_unit_quantity"]) == (
        "197.000",
        "Carton 24",
        "5.000",
    )
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("A", "-3.000")]
    _invariant(owner_db, world, 0)


def test_decimal_and_whole_rules_per_lot(world: World, lot_tracking_open: None) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    assert _code(_put_lots(world, inv, 0, {"A": "2.5"})) == (422, "quantity_not_whole")


def test_decimal_article_lot_counts(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    sh.allow_decimals(world, 0)
    _track(world, 0)
    _stock(world, 0, [("A", "3.5", _iso(30))])
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "3.25"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("A", "-0.250")]


def test_packaging_deactivated_or_conversion_changed_before_validation(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    carton = _carton(world)
    _track(world, 0)
    _stock(world, 0, [("A", "48", _iso(30))])
    inv = _inventory(world, [0])
    count = {"A": {"packaging_id": carton, "packaging_quantity": "2", "unit_quantity": "0"}}
    _ok(_put_lots(world, inv, 0, count))
    _complete(world, inv)
    owner_db.execute(
        text("UPDATE catalog_packagings SET conversion = 12 WHERE id = :p"), {"p": carton}
    )
    owner_db.commit()
    assert _code(_validate(world, inv)) == (409, "packaging_conversion_changed")
    owner_db.execute(
        text("UPDATE catalog_packagings SET conversion = 24, is_active = false WHERE id = :p"),
        {"p": carton},
    )
    owner_db.commit()
    assert _code(_validate(world, inv)) == (422, "packaging_inactive")
    assert sh.level(owner_db, world, 0)[0] == "48.000"


# --- Mode figé, invariant, CMUP ------------------------------------------------------------------


def test_lot_mode_changed_since_start_refused(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50"}))
    _complete(world, inv)
    # Par l'API : refusé tant que l'inventaire est ouvert.
    response = world.owner.patch(
        f"/catalog/articles/{world.articles[0]}",
        json={"lot_tracked": False, "expiry_tracked": False},
    )
    assert _code(response)[0] == 409
    # Hors API (défense en profondeur) : la validation refuse le mode changé.
    owner_db.execute(
        text(
            "UPDATE catalog_articles SET lot_tracked = false, expiry_tracked = false WHERE id = :a"
        ),
        {"a": world.articles[0]},
    )
    owner_db.commit()
    assert _code(_validate(world, inv)) == (409, "inventory_lot_mode_changed")
    assert sh.level(owner_db, world, 0)[0] == "150.000"


def test_broken_invariant_refused_nothing_written(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "99", "B": "50"}))
    _complete(world, inv)
    owner_db.execute(
        text("UPDATE stock_levels SET quantity = 151 WHERE site_id = :s AND article_id = :a"),
        {"s": world.site, "a": world.articles[0]},
    )
    owner_db.commit()
    assert _code(_validate(world, inv)) == (422, "lot_invariant_broken")
    assert _adjustments(owner_db, inv) == []
    assert _ok(world.owner.get(f"{BASE}/{inv['id']}"))["status"] == "READY_TO_VALIDATE"


def test_cmup_unchanged_and_values_at_current_cost(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _receive(world, [_line(world, 0, "20", "500", lot="A", expiry=_iso(30))])
    _receive(world, [_line(world, 0, "10", "600", lot="B", expiry=_iso(60))])
    cost = sh.level(owner_db, world, 0)[1]
    assert cost == "533.3333"
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "18", "B": "13"}))  # −2 et +3 : écart article +1
    _complete(world, inv)
    _ok(_validate(world, inv))
    owner_db.expire_all()
    costs = owner_db.execute(
        text(
            "SELECT DISTINCT unit_cost::text FROM stock_movements "
            "WHERE source_id = :id AND movement_type = 'ADJUSTMENT'"
        ),
        {"id": inv["id"]},
    ).all()
    assert [row[0] for row in costs] == ["533.3333"]
    assert sh.level(owner_db, world, 0) == ("31.000", "533.3333")
    line = _line_of(world, inv, 0)
    assert (line["quantity_variance"], line["unit_cost"], line["adjustment_value"]) == (
        "1.000",
        "533.3333",
        "533.33",
    )


def test_discovered_lot_on_article_without_stock(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Stock initial nul : seul un lot découvert est compté — entrée au CMUP courant (0),
    CMUP inchangé (un ajustement ne recalcule jamais le CMUP)."""
    _track(world, 0)
    inv = _inventory(world, [0])
    assert _line_of(world, inv, 0)["lots"] == []
    _ok(_discover(world, inv, 0, "FOUND", expiry_date=_iso(40), quantity_physical="5"), 201)
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _adjustments(owner_db, inv) == [("FOUND", "5.000")]
    assert sh.level(owner_db, world, 0)[0] == "5.000"
    _invariant(owner_db, world, 0)


def test_validated_inventory_is_immutable(world: World, lot_tracking_open: None) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50"}))
    _complete(world, inv)
    _ok(_validate(world, inv))
    assert _code(_put_lots(world, inv, 0, {"A": "1"})) == (409, "inventory_invalid_transition")
    assert _code(_discover(world, inv, 0, "Z", expiry_date=_iso(5))) == (
        409,
        "inventory_invalid_transition",
    )
    assert _code(world.owner.post(f"{BASE}/{inv['id']}/refresh-lots")) == (
        409,
        "inventory_invalid_transition",
    )
    assert _code(_validate(world, inv))[0] == 409


# --- Concurrence ----------------------------------------------------------------------------------


def _race(app: Any, token: str, calls: list[tuple[str, Any]]) -> list[int]:
    barrier = threading.Barrier(len(calls))
    statuses: list[int] = []

    def run(path: str, body: Any) -> None:
        with TestClient(app) as client:
            barrier.wait()
            statuses.append(Api(client, token).post(path, json=body).status_code)

    threads = [threading.Thread(target=run, args=call) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return sorted(statuses)


def test_validation_and_sale_at_the_same_time(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    """Inventaire + vente simultanés : sérialisés sur le niveau ; l'écart est calculé sur le
    solde relu sous verrou, quel que soit l'ordre ; Σ lots = stock."""
    _track(world, 0)
    _stock(world, 0, [("A", "100", _iso(30))])
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "90"}))
    _complete(world, inv)
    statuses = _race(
        app,
        world.owner.token,
        [(f"{BASE}/{inv['id']}/validate", None), ("/pos/checkout", _pos_body(world, 0, "5"))],
    )
    assert statuses == [200, 201]
    owner_db.expire_all()
    # Vente avant : écart −5 puis A = 90 ; vente après : écart −10 puis A = 85.
    assert _balance(owner_db, world, "A") in {"85.000", "90.000"}
    _invariant(owner_db, world, 0)


def test_validation_and_exit_at_the_same_time(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "100", _iso(30))])
    lot = _lot_id(world, "A")
    inv = _inventory(world, [0])
    _ok(_put_lots(world, inv, 0, {"A": "100"}))
    _complete(world, inv)
    exit_doc = _ok(_exit(world, [(0, "4", [(lot, "4")])]), 201)
    statuses = _race(
        app,
        world.owner.token,
        [(f"{BASE}/{inv['id']}/validate", None), (f"/stock/exits/{exit_doc['id']}/validate", None)],
    )
    assert statuses == [200, 200]
    assert _balance(owner_db, world, "A") in {"96.000", "100.000"}
    _invariant(owner_db, world, 0)


def test_concurrent_discovery_of_the_same_new_lot_on_two_sites(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    """Le même lot NOUVEAU découvert sur deux sites, validations simultanées : un seul lot
    créé (``INSERT … ON CONFLICT`` + relecture), rattaché des deux côtés."""
    _track(world, 0)
    first = _inventory(world, [0])
    second = _inventory(world, [0], site=world.site2)
    for inv in (first, second):
        _ok(_discover(world, inv, 0, "TWIN", expiry_date=_iso(30), quantity_physical="2"), 201)
        _complete(world, inv)
    statuses = _race(
        app,
        world.owner.token,
        [(f"{BASE}/{first['id']}/validate", None), (f"{BASE}/{second['id']}/validate", None)],
    )
    assert statuses == [200, 200]
    assert _lots_named(owner_db, "TWIN") == 1
    assert (_balance(owner_db, world, "TWIN"), _balance(owner_db, world, "TWIN", world.site2)) == (
        "2.000",
        "2.000",
    )


# --- Changement de suivi (documents ouverts, historique) ------------------------------------------


def test_lot_flags_change_refused_with_open_inventory(
    world: World, lot_tracking_open: None
) -> None:
    """Inventaire ouvert (même brouillon) : le suivi de l'article ne change pas ; après
    annulation de l'inventaire, l'activation est permise."""
    created = _ok(
        world.owner.post(
            BASE,
            json={
                "site_id": world.site,
                "inventory_type": "TARGETED",
                "article_ids": [world.articles[2]],
            },
        ),
        201,
    )
    article = f"/catalog/articles/{world.articles[2]}"
    response = world.owner.patch(article, json={"lot_tracked": True})
    assert _code(response) == (409, "article_in_open_documents")
    assert created["number"] in response.json()["documents"]
    _ok(world.owner.post(f"{BASE}/{created['id']}/cancel", json={"reason": "Pour le test"}))
    assert _ok(world.owner.patch(article, json={"lot_tracked": True}))["lot_tracked"] is True


def test_lot_flags_change_refused_by_draft_with_lot_allocations(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(30))])
    lot = _lot_id(world, "A")
    draft = _ok(_exit(world, [(0, "5", [(lot, "5")])]), 201)
    # Stock remis à zéro hors du brouillon (sortie validée d'un autre document).
    other = _ok(_exit(world, [(0, "5", [(lot, "5")])]), 201)
    _ok(world.owner.post(f"/stock/exits/{other['id']}/validate"))
    response = world.owner.patch(
        f"/catalog/articles/{world.articles[0]}",
        json={"lot_tracked": False, "expiry_tracked": False},
    )
    assert _code(response) == (409, "article_in_open_documents")
    assert draft["number"] in response.json()["documents"]
    # Brouillon de sortie : répartition retirée (une sortie brouillon ne s'annule pas, elle se
    # modifie) — le changement de suivi est alors permis.
    _ok(
        world.owner.put(
            f"/stock/exits/{draft['id']}",
            json={
                "reason_id": world.reasons["PERTE"],
                "lines": [{"article_id": world.articles[0], "quantity": "5", "lots": []}],
            },
        )
    )
    _ok(
        world.owner.patch(
            f"/catalog/articles/{world.articles[0]}",
            json={"lot_tracked": False, "expiry_tracked": False},
        )
    )


def test_enabling_refused_with_untracked_cancellable_history(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Limitation 3-H-A fermée : une vente validée sans lot (encore annulable) empêche
    l'activation du suivi — son annulation exigerait un lot inconnu. Une fois annulée (et le
    stock ramené à zéro sans document annulable), l'activation est permise."""
    entry = sh.validated_entry(world, [(1, "5", "100")])
    sale = _ok(world.owner.post("/pos/checkout", json=_pos_body(world, 1, "5")), 201)["sale"]
    article = f"/catalog/articles/{world.articles[1]}"
    response = world.owner.patch(article, json={"lot_tracked": True})
    assert _code(response) == (409, "article_has_untracked_history")
    assert sale["number"] in response.json()["documents"]
    # Annulations : vente puis réception — plus aucun document annulable, stock nul.
    _ok(world.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur de caisse"}))
    _ok(world.owner.post(f"/stock/entries/{entry['id']}/cancel", json={"reason": "Erreur"}))
    assert sh.level(owner_db, world, 1)[0] == "0.000"
    assert _ok(world.owner.patch(article, json={"lot_tracked": True}))["lot_tracked"] is True


# --- Sécurité -------------------------------------------------------------------------------------


def test_rls_and_composite_fks(
    world: World, lot_tracking_open: None, provision: Any, db: Session, owner_db: Session
) -> None:
    _ab(world)
    _track(world, 1)
    _stock(world, 1, [("X", "3", _iso(30))])
    inv = _inventory(world, [0])
    line = _line_of(world, inv, 0)
    alpha = owner_db.execute(
        text("SELECT tenant_id FROM inventories WHERE id = :id"), {"id": inv["id"]}
    ).scalar_one()
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    set_db_context(db, tenant_id=beta.tenant_id)
    assert db.execute(text("SELECT count(*) FROM inventory_line_lots")).scalar_one() == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO inventory_line_lots (id, tenant_id, inventory_line_id, article_id,"
                " lot_id, discovered, stock_theoretical_initial)"
                " VALUES (gen_random_uuid(), :t, :l, :a, :lot, false, 0)"
            ),
            {"t": alpha, "l": line["id"], "a": world.articles[0], "lot": _lot_id(world, "A")},
        )
    db.rollback()
    set_db_context(db, tenant_id=alpha)
    for article, lot in (
        (world.articles[1], _lot_id(world, "X")),  # article ≠ article de la ligne
        (world.articles[0], _lot_id(world, "X")),  # lot d'un autre article
    ):
        with pytest.raises(DBAPIError, match="foreign key"):
            db.execute(
                text(
                    "INSERT INTO inventory_line_lots (id, tenant_id, inventory_line_id,"
                    " article_id, lot_id, discovered, stock_theoretical_initial)"
                    " VALUES (gen_random_uuid(), :t, :l, :a, :lot, false, 0)"
                ),
                {"t": alpha, "l": line["id"], "a": article, "lot": lot},
            )
        db.rollback()
        set_db_context(db, tenant_id=alpha)
    owner_db.expire_all()
    assert owner_db.execute(
        text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
            " WHERE relname = 'inventory_line_lots'"
        )
    ).scalar_one()
    assert not owner_db.execute(
        text("SELECT has_table_privilege('stockmanager_platform', 'inventory_line_lots', 'SELECT')")
    ).scalar_one()
    assert (
        owner_db.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'stockmanager_app'")
        ).scalar_one()
        is False
    )


def test_tenant_and_site_isolation_api(
    world: World, lot_tracking_open: None, provision: Any, api_for: Any, client: TestClient
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    assert _code(_put_lots(world, inv, 0, {"A": "1"}, api=other))[0] == 404
    local = _member(
        world,
        client,
        "local-inv@alpha.example.com",
        ["inventory_count.inventory.view", "inventory_count.inventory.count"],
        all_sites=False,
        site_ids=[world.site2],
    )
    assert _code(local.get(f"{BASE}/{inv['id']}"))[0] == 404


def test_permissions_count_and_validate(
    world: World, lot_tracking_open: None, client: TestClient
) -> None:
    _ab(world)
    inv = _inventory(world, [0])
    viewer = _member(
        world, client, "inv-view@alpha.example.com", ["inventory_count.inventory.view"]
    )
    assert _put_lots(world, inv, 0, {"A": "1"}, api=viewer).status_code == 403
    assert _discover(world, inv, 0, "Y", api=viewer, expiry_date=_iso(5)).status_code == 403
    assert viewer.post(f"{BASE}/{inv['id']}/refresh-lots").status_code == 403
    counter = _member(
        world,
        client,
        "inv-count@alpha.example.com",
        ["inventory_count.inventory.view", "inventory_count.inventory.count"],
    )
    _ok(_discover(world, inv, 0, "Y", api=counter, expiry_date=_iso(5), quantity_physical="1"), 201)
    _ok(_put_lots(world, inv, 0, {"A": "100", "B": "50", "Y": "1"}, api=counter))
    _ok(counter.post(f"{BASE}/{inv['id']}/complete-counting"))
    assert _validate(world, inv, api=counter).status_code == 403


# --- Flux complet et chemins d'écriture -----------------------------------------------------------


def test_full_flow_reception_sale_transfer_inventory(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "30", _iso(20)), ("B", "30", _iso(50))])
    _ok(world.owner.post("/pos/checkout", json=_pos_body(world, 0, "10")), 201)  # FEFO : A −10
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    transfer = _ok(
        world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site,
                "destination_site_id": world.site2,
                "lines": [
                    {
                        "article_id": world.articles[0],
                        "quantity": "15",
                        "lots": [{"lot_id": a, "quantity": "5"}, {"lot_id": b, "quantity": "10"}],
                    }
                ],
            },
        ),
        201,
    )
    _ok(world.owner.post(f"/stock/transfers/{transfer['id']}/validate"))
    assert [_balance(owner_db, world, n) for n in "AB"] == ["15.000", "20.000"]
    for site, counts in ((world.site, {"A": "14", "B": "21"}), (world.site2, {"A": "5", "B": "9"})):
        inv = _inventory(world, [0], site=site)
        _ok(_put_lots(world, inv, 0, counts))
        _complete(world, inv)
        _ok(_validate(world, inv))
        _invariant(owner_db, world, 0, site)
    assert [_balance(owner_db, world, n) for n in "AB"] == ["14.000", "21.000"]
    assert [_balance(owner_db, world, n, world.site2) for n in "AB"] == ["5.000", "9.000"]


def test_static_every_stock_writer_is_reviewed_for_lots() -> None:
    """Test statique (P1-b) : tout appel d'écriture au moteur de stock hors ``StockService``
    est recensé ici — un nouvel appel doit être audité (lot obligatoire pour un article suivi)
    avant d'être ajouté. Le garde-fou serveur (``lot_required``) reste la protection finale."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    writers = (".apply(", ".apply_many(", ".transfer(", ".transfer_lots(", ".consume(", "._write(")
    found = set()
    for path in app_dir.rglob("*.py"):
        if path.name == "stock_service.py":
            continue
        source = path.read_text(encoding="utf-8")
        for writer in writers:
            if writer in source:
                found.add((str(path.relative_to(app_dir)), writer))
    assert found == {
        # Réceptions (lot de la ligne) et annulations (lot du mouvement d'origine).
        ("modules/stock/document_service.py", ".apply("),
        # Sorties : répartition manuelle par lot.
        ("modules/stock/document_service.py", ".consume("),
        # Transferts : paire par lot ; annulations : lot du mouvement d'origine.
        ("modules/stock/transfer_service.py", ".transfer_lots("),
        ("modules/stock/transfer_service.py", ".apply_many("),
        # Ventes / POS : FEFO ; annulations : lot du mouvement d'origine.
        ("modules/sales/service.py", ".consume("),
        ("modules/sales/service.py", ".apply("),
        # Inventaires : un ajustement par lot.
        ("modules/inventory_count/service.py", ".apply("),
    }, found


def test_guard_still_refuses_adjustment_without_lot(
    world: World, lot_tracking_open: None, db: Session, owner_db: Session
) -> None:
    from app.core.errors import BusinessRuleError
    from app.modules.stock.models import MovementType
    from app.modules.stock.stock_service import MovementRequest, StockService
    from app.shared.clock import utcnow

    _ab(world)
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": world.site}
    ).scalar_one()
    set_db_context(db, tenant_id=tenant)
    with pytest.raises(BusinessRuleError) as refused:
        StockService(db, tenant, None, utcnow()).apply(
            uuid.UUID(world.site),
            [
                MovementRequest(
                    article_id=uuid.UUID(world.articles[0]),
                    movement_type=MovementType.ADJUSTMENT,
                    quantity=Decimal("1"),
                    source_type="test",
                    source_id=uuid.uuid4(),
                    source_line_id=uuid.uuid4(),
                )
            ],
        )
    assert refused.value.code == "lot_required"
    db.rollback()
