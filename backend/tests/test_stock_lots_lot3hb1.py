"""Lot 3-H-B1 (ADR-0045) — transferts inter-sites par lot.

- Répartition MANUELLE d'une ligne d'article suivi sur ses lots (brouillon incomplet admis,
  somme exacte à la validation) ; ``StockService.transfer_lots`` : une paire ``TRANSFER_OUT`` /
  ``TRANSFER_IN`` par lot, sous le MÊME lot des deux côtés, jamais un nouveau lot ; Σ lots =
  stock sur les deux sites ; tout ou rien.
- Lot périmé : transfert refusé (``lot_expired_not_transferable``, D-1, sans dérogation) ;
  l'annulation d'un transfert validé reste possible (H-D10).
- CMUP (T-3, D-2) : CMUP source lu une fois, même coût en sortie et en entrée (valeur sortie =
  valeur entrée exactement) ; CMUP destination calculé une fois par ligne, identique à un
  transfert non réparti.
- Annulation : un inverse par mouvement d'origine, même lot ; refus total si un lot destination
  ne suffit plus (``insufficient_lot_stock``).
- Aucune permission nouvelle ; lots disponibles d'un transfert : ``stock.transfer.create``.
- P1-b reste active : les tests l'ouvrent avec la fixture ``lot_tracking_open``.
"""

import inspect
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
from app.core.errors import BusinessRuleError
from app.modules.catalog import lot_tracking
from app.modules.stock import transfer_service
from app.modules.stock.stock_service import (
    LotPick,
    StockService,
    TransferItem,
    compute_average_cost,
    round_money,
)
from app.shared.clock import utcnow
from tests import stock_helpers as sh
from tests.conftest import Api, add_site
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

TRANSFERS = "/stock/transfers"

# --- Aides ----------------------------------------------------------------------------------------


def _tline(
    w: World,
    index: int,
    quantity: str,
    lots: list[tuple[str, str]] | None = None,
    packaging: str | None = None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "article_id": w.articles[index],
        "quantity": quantity,
        "lots": [{"lot_id": lot, "quantity": q} for lot, q in (lots or [])],
    }
    if packaging is not None:
        line["packaging_id"] = packaging
    return line


def _draft(
    w: World,
    lines: list[dict[str, Any]],
    api: Api | None = None,
    source: str | None = None,
    destination: str | None = None,
) -> Any:
    return (api or w.owner).post(
        TRANSFERS,
        json={
            "source_site_id": source or w.site,
            "destination_site_id": destination or w.site2,
            "lines": lines,
        },
    )


def _validate(w: World, transfer: dict[str, Any], api: Api | None = None) -> Any:
    return (api or w.owner).post(f"{TRANSFERS}/{transfer['id']}/validate")


def _transfer(w: World, lines: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    """Brouillon créé puis validé (succès attendu)."""
    draft = _ok(_draft(w, lines, **kw), 201)
    return dict(_ok(_validate(w, draft)))


def _cancel(w: World, transfer: dict[str, Any], api: Api | None = None) -> Any:
    return (api or w.owner).post(
        f"{TRANSFERS}/{transfer['id']}/cancel", json={"reason": "Erreur de saisie"}
    )


def _rows(owner_db: Session, transfer_id: str) -> list[dict[str, Any]]:
    """Mouvements d'un transfert, dans l'ordre d'écriture."""
    owner_db.expire_all()
    return [
        dict(row._mapping)
        for row in owner_db.execute(
            text(
                "SELECT m.movement_type AS type, m.site_id::text AS site, s.number AS lot,"
                " m.lot_id::text AS lot_id, m.quantity::text AS quantity,"
                " m.unit_cost AS unit_cost, m.average_cost_before AS before,"
                " m.average_cost_after AS after, m.packaging_quantity::text AS packaging,"
                " m.origin_movement_id::text AS origin, m.id::text AS id"
                " FROM stock_movements m LEFT JOIN stock_lots s ON s.id = m.lot_id"
                " WHERE m.source_id = :id ORDER BY m.occurred_at, m.id"
            ),
            {"id": transfer_id},
        )
    ]


def _of(rows: list[dict[str, Any]], kind: str) -> list[tuple[str, str]]:
    return [(r["lot"] or "-", r["quantity"]) for r in rows if r["type"] == kind]


def _lots_count(owner_db: Session) -> int:
    return sh.count(owner_db, "SELECT count(*) FROM stock_lots")


def _both_invariants(owner_db: Session, w: World, index: int) -> None:
    """Σ lots = stock sur les deux sites (un site sans niveau n'a aucun solde de lot)."""
    for site in (w.site, w.site2):
        if sh.level(owner_db, w, index, site)[0] == "none":
            assert (
                sh.count(
                    owner_db,
                    "SELECT count(*) FROM stock_lot_levels"
                    f" WHERE site_id = '{site}' AND quantity <> 0",
                )
                == 0
            )
        else:
            _invariant(owner_db, w, index, site)


def _expire(owner_db: Session, number: str) -> None:
    owner_db.execute(
        text("UPDATE stock_lots SET expiry_date = :d WHERE number = :n"),
        {"d": _iso(-1), "n": number},
    )
    owner_db.commit()


def _ab(w: World) -> tuple[str, str]:
    """Article 0 suivi : lot A 100 (péremption J+30) et lot B 50 (J+60) sur le site principal."""
    _track(w, 0)
    _stock(w, 0, [("A", "100", _iso(30)), ("B", "50", _iso(60))])
    return _lot_id(w, "A"), _lot_id(w, "B")


# --- 1-7. Mono-lot, multi-lots, identité du lot, soldes, invariant -------------------------------


def test_single_lot_transfer(world: World, lot_tracking_open: None, owner_db: Session) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    lot = _lot_id(world, "A")
    transfer = _transfer(world, [_tline(world, 0, "4", [(lot, "4")])])
    rows = _rows(owner_db, transfer["id"])
    assert _of(rows, "TRANSFER_OUT") == [("A", "-4.000")]
    assert _of(rows, "TRANSFER_IN") == [("A", "4.000")]
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "A", world.site2)) == (
        "6.000",
        "4.000",
    )
    assert (sh.level(owner_db, world, 0)[0], sh.level(owner_db, world, 0, world.site2)[0]) == (
        "6.000",
        "4.000",
    )
    line = transfer["lines"][0]
    assert [(lot["lot_number"], lot["quantity"], lot["expiry_date"]) for lot in line["lots"]] == [
        ("A", "4.000", _iso(30))
    ]
    _both_invariants(owner_db, world, 0)


def test_multi_lot_transfer_keeps_the_same_lot_on_both_sites(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """70 = A 50 + B 20 : une paire par lot, MÊME ``lot_id`` des deux côtés, aucun lot créé."""
    a, b = _ab(world)
    lots_before = _lots_count(owner_db)
    transfer = _transfer(world, [_tline(world, 0, "70", [(a, "50"), (b, "20")])])
    rows = _rows(owner_db, transfer["id"])
    # Ordre : paire du lot A, puis paire du lot B.
    assert [(r["type"], r["lot"], r["quantity"]) for r in rows] == [
        ("TRANSFER_OUT", "A", "-50.000"),
        ("TRANSFER_IN", "A", "50.000"),
        ("TRANSFER_OUT", "B", "-20.000"),
        ("TRANSFER_IN", "B", "20.000"),
    ]
    assert {r["site"] for r in rows if r["type"] == "TRANSFER_OUT"} == {world.site}
    assert {r["site"] for r in rows if r["type"] == "TRANSFER_IN"} == {world.site2}
    for lot in (a, b):
        assert {r["lot_id"] for r in rows if r["lot_id"] == lot} == {lot}
        assert sum(1 for r in rows if r["lot_id"] == lot) == 2
    assert _lots_count(owner_db) == lots_before
    assert [_balance(owner_db, world, n) for n in "AB"] == ["50.000", "30.000"]
    assert [_balance(owner_db, world, n, world.site2) for n in "AB"] == ["50.000", "20.000"]
    assert sh.level(owner_db, world, 0)[0] == "80.000"
    assert sh.level(owner_db, world, 0, world.site2)[0] == "70.000"
    _both_invariants(owner_db, world, 0)
    # Le lot devient visible sur le site destination (même identité, mêmes dates).
    lot = _ok(world.owner.get(f"/stock/lots/{a}"))
    assert {str(b["site_id"]): b["quantity"] for b in lot["balances"]} == {
        world.site: "50.000",
        world.site2: "50.000",
    }
    assert lot["expiry_date"] == _iso(30)


def test_several_lines_of_the_same_article_cmup_chained(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Deux lignes du même article (unité de base + carton) : un calcul de CMUP par ligne,
    enchaînés ; chaque ligne garde ses lots."""
    carton = _carton(world)
    a, b = _ab(world)
    transfer = _transfer(
        world,
        [
            _tline(world, 0, "26", [(a, "26")]),
            _tline(world, 0, "1", [(b, "24")], packaging=carton),
        ],
    )
    assert [[lot["lot_number"] for lot in line["lots"]] for line in transfer["lines"]] == [
        ["A"],
        ["B"],
    ]
    assert sh.level(owner_db, world, 0, world.site2)[0] == "50.000"
    rows = _rows(owner_db, transfer["id"])
    incoming = [r for r in rows if r["type"] == "TRANSFER_IN"]
    assert incoming[1]["packaging"] == "1.000"
    _both_invariants(owner_db, world, 0)


# --- 8-16. Refus : stock, lots, péremption, répartition ------------------------------------------


def _nothing_moved(owner_db: Session, w: World, transfer: dict[str, Any], site_qty: str) -> None:
    assert _rows(owner_db, transfer["id"]) == []
    assert sh.level(owner_db, w, 0)[0] == site_qty
    assert sh.level(owner_db, w, 0, w.site2)[0] in {"none", "0.000"}
    assert _ok(w.owner.get(f"{TRANSFERS}/{transfer['id']}"))["status"] == "DRAFT"


def test_insufficient_stock_refused(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    draft = _ok(_draft(world, [_tline(world, 0, "15", [(_lot_id(world, "A"), "15")])]), 201)
    assert _code(_validate(world, draft)) == (422, "insufficient_stock")
    _nothing_moved(owner_db, world, draft, "10.000")


def test_insufficient_lot_refused_atomically(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(30)), ("B", "10", _iso(60))])
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    draft = _ok(_draft(world, [_tline(world, 0, "9", [(b, "1"), (a, "8")])]), 201)
    response = _validate(world, draft)
    assert _code(response) == (422, "insufficient_lot_stock")
    assert response.json()["lots"][0]["lot_number"] == "A"
    _nothing_moved(owner_db, world, draft, "15.000")
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "B")) == ("5.000", "10.000")


def test_lot_of_another_article_refused(
    world: World, lot_tracking_open: None, owner_db: Session, db: Session
) -> None:
    _track(world, 0)
    _track(world, 1)
    _stock(world, 0, [("A", "10", _iso(30))])
    _stock(world, 1, [("X", "10", _iso(30))])
    other = _lot_id(world, "X")
    assert _code(_draft(world, [_tline(world, 0, "1", [(other, "1")])])) == (
        422,
        "lot_not_available",
    )
    # Défense en profondeur au moteur (relu sous verrou) : même refus, rien n'est écrit.
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": world.site}
    ).scalar_one()
    set_db_context(db, tenant_id=tenant)
    with pytest.raises(BusinessRuleError) as refused:
        StockService(db, tenant, None, utcnow()).transfer_lots(
            uuid.UUID(world.site),
            uuid.UUID(world.site2),
            [
                TransferItem(
                    line_id=uuid.uuid4(),
                    article_id=uuid.UUID(world.articles[0]),
                    quantity=Decimal("1"),
                    picks=(LotPick(uuid.UUID(other), Decimal("1")),),
                )
            ],
            source_type="test",
            source_id=uuid.uuid4(),
            source_number="T-1",
            today=utcnow().date(),
        )
    assert refused.value.code == "lot_not_available"
    db.rollback()


def test_lot_of_another_tenant_refused(
    world: World, lot_tracking_open: None, provision: Any, api_for: Any
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    lot = _lot_id(world, "A")
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    depot = _ok(add_site(other, "Dépôt B", "DEPB", "warehouse"), 201)
    category = _ok(other.post("/catalog/categories", json={"name": "Divers"}), 201)
    article = _ok(
        other.post(
            "/catalog/articles",
            json={
                "reference": "B-1",
                "designation": "Article B",
                "category_id": category["id"],
                "unit": "u",
                "sale_price": "10",
            },
        ),
        201,
    )
    _ok(other.patch(f"/catalog/articles/{article['id']}", json={"lot_tracked": True}))
    response = other.post(
        TRANSFERS,
        json={
            "source_site_id": str(beta.site_id),
            "destination_site_id": depot["id"],
            "lines": [
                {
                    "article_id": article["id"],
                    "quantity": "1",
                    "lots": [{"lot_id": lot, "quantity": "1"}],
                }
            ],
        },
    )
    assert _code(response) == (422, "lot_not_available")
    # Lots disponibles d'un article d'un autre tenant : introuvable.
    assert _code(
        other.get(
            f"{TRANSFERS}/available-lots",
            params={"article_id": world.articles[0], "site_id": str(beta.site_id)},
        )
    ) == (404, "article_not_found")


def test_lot_absent_from_source_site_refused(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Lot présent sur le seul site destination : indisponible au site source."""
    _track(world, 0)
    _stock(world, 0, [("SRC", "10", _iso(30))])
    _stock(world, 0, [("DEPOT", "5", _iso(30))], site=world.site2)
    draft = _ok(_draft(world, [_tline(world, 0, "1", [(_lot_id(world, "DEPOT"), "1")])]), 201)
    assert _code(_validate(world, draft)) == (422, "lot_not_available")
    assert _rows(owner_db, draft["id"]) == []
    assert sh.level(owner_db, world, 0, world.site2)[0] == "5.000"


def test_expired_lot_never_transferred(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    a, b = _ab(world)
    draft = _ok(_draft(world, [_tline(world, 0, "30", [(b, "10"), (a, "20")])]), 201)
    _expire(owner_db, "A")
    response = _validate(world, draft)
    assert _code(response) == (422, "lot_expired_not_transferable")
    assert response.json()["lots"] == ["A"]
    _nothing_moved(owner_db, world, draft, "150.000")
    # Lots disponibles : le lot périmé est signalé (jamais sélectionnable côté interface).
    lots = _ok(
        world.owner.get(
            f"{TRANSFERS}/available-lots",
            params={"article_id": world.articles[0], "site_id": world.site},
        )
    )["lots"]
    assert {lot["number"]: lot["expired"] for lot in lots} == {"A": True, "B": False}


def test_incomplete_allocation_allowed_in_draft_refused_at_validation(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    a, _ = _ab(world)
    draft = _ok(_draft(world, [_tline(world, 0, "70", [(a, "40")])]), 201)
    assert [(lot["lot_number"], lot["quantity"]) for lot in draft["lines"][0]["lots"]] == [
        ("A", "40.000")
    ]
    response = _validate(world, draft)
    assert _code(response) == (422, "lot_allocation_incomplete")
    assert (response.json()["requested"], response.json()["allocated"]) == ("70.000", "40.000")
    # Aucune répartition du tout : même refus (un article suivi exige ses lots).
    empty = _ok(_draft(world, [_tline(world, 0, "5")]), 201)
    assert _code(_validate(world, empty)) == (422, "lot_allocation_incomplete")
    _nothing_moved(owner_db, world, draft, "150.000")


def test_excessive_and_duplicate_allocations_refused(world: World, lot_tracking_open: None) -> None:
    a, b = _ab(world)
    assert _code(_draft(world, [_tline(world, 0, "10", [(a, "8"), (b, "3")])])) == (
        422,
        "lot_allocation_exceeds",
    )
    assert _code(_draft(world, [_tline(world, 0, "10", [(a, "5"), (a, "5")])])) == (
        422,
        "duplicate_lot_allocation",
    )
    # Mise à jour d'un brouillon : mêmes contrôles.
    draft = _ok(_draft(world, [_tline(world, 0, "10", [(a, "5")])]), 201)
    response = world.owner.put(
        f"{TRANSFERS}/{draft['id']}",
        json={
            "destination_site_id": world.site2,
            "lines": [_tline(world, 0, "10", [(a, "11")])],
        },
    )
    assert _code(response) == (422, "lot_allocation_exceeds")


def test_lot_invariant_broken_refused(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Σ lots ≠ stock avant l'opération (état incohérent) : refus, aucune écriture."""
    a, _ = _ab(world)
    draft = _ok(_draft(world, [_tline(world, 0, "5", [(a, "5")])]), 201)
    owner_db.execute(
        text("UPDATE stock_levels SET quantity = 151 WHERE site_id = :s AND article_id = :a"),
        {"s": world.site, "a": world.articles[0]},
    )
    owner_db.commit()
    assert _code(_validate(world, draft)) == (422, "lot_invariant_broken")
    assert _rows(owner_db, draft["id"]) == []
    assert _balance(owner_db, world, "A") == "100.000"


def test_lots_refused_on_untracked_article(world: World, lot_tracking_open: None) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    assert _code(_draft(world, [_tline(world, 1, "1", [(_lot_id(world, "A"), "1")])])) == (
        422,
        "article_not_lot_tracked",
    )


# --- 17-22. Quantités et conditionnements --------------------------------------------------------


def test_whole_article_refuses_decimal_lot_quantity(world: World, lot_tracking_open: None) -> None:
    a, _ = _ab(world)
    assert _code(_draft(world, [_tline(world, 0, "2", [(a, "1.5")])])) == (
        422,
        "quantity_not_whole",
    )


def test_decimal_article_lot_quantities(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    sh.allow_decimals(world, 0)
    _track(world, 0)
    _stock(world, 0, [("A", "3.5", _iso(30)), ("B", "2.25", _iso(60))])
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    assert _code(_draft(world, [_tline(world, 0, "1", [(a, "0.0001")])]))[0] == 422
    transfer = _transfer(world, [_tline(world, 0, "2.75", [(a, "1.5"), (b, "1.25")])])
    assert _of(_rows(owner_db, transfer["id"]), "TRANSFER_IN") == [
        ("A", "1.500"),
        ("B", "1.250"),
    ]
    assert sh.level(owner_db, world, 0)[0] == "3.000"
    _both_invariants(owner_db, world, 0)


def test_packaging_exact_split_keeps_presentation(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    carton = _carton(world)
    a, b = _ab(world)
    transfer = _transfer(world, [_tline(world, 0, "2", [(a, "24"), (b, "24")], packaging=carton)])
    line = transfer["lines"][0]
    assert (line["packaging_name"], line["quantity"], line["base_quantity"]) == (
        "Carton 24",
        "2.000",
        "48.000",
    )
    assert [r["packaging"] for r in _rows(owner_db, transfer["id"])] == ["1.000"] * 4


def test_packaging_not_representable_falls_back_to_base_unit(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    carton = _carton(world)
    a, b = _ab(world)
    transfer = _transfer(world, [_tline(world, 0, "2", [(a, "30"), (b, "18")], packaging=carton)])
    rows = _rows(owner_db, transfer["id"])
    assert [r["packaging"] for r in rows] == [None] * 4
    assert _of(rows, "TRANSFER_OUT") == [("A", "-30.000"), ("B", "-18.000")]
    # La ligne garde la présentation saisie.
    assert transfer["lines"][0]["packaging_name"] == "Carton 24"


def test_packaging_deactivated_between_draft_and_validation(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    carton = _carton(world)
    a, _ = _ab(world)
    draft = _ok(_draft(world, [_tline(world, 0, "1", [(a, "24")], packaging=carton)]), 201)
    _ok(world.owner.post(f"/catalog/packagings/{carton}/deactivate"))
    assert _code(_validate(world, draft)) == (422, "packaging_inactive")
    _nothing_moved(owner_db, world, draft, "150.000")


def test_packaging_conversion_changed_between_draft_and_validation(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    carton = _carton(world)
    a, _ = _ab(world)
    draft = _ok(_draft(world, [_tline(world, 0, "1", [(a, "24")], packaging=carton)]), 201)
    owner_db.execute(
        text("UPDATE catalog_packagings SET conversion = 12 WHERE id = :p"), {"p": carton}
    )
    owner_db.commit()
    assert _code(_validate(world, draft)) == (409, "packaging_conversion_changed")
    _nothing_moved(owner_db, world, draft, "150.000")


# --- 23-26. Concurrence ---------------------------------------------------------------------------


def _race(app: Any, token: str, calls: list[tuple[str, Any]]) -> list[int]:
    """Requêtes POST lancées en même temps (barrière), chacune avec sa propre connexion."""
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


def test_opposite_transfers_at_the_same_time(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    """A → B et B → A simultanés sur le même lot : sérialisés (ordre global des verrous),
    aucun interblocage, soldes exacts."""
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    _stock(world, 0, [("A", "10", _iso(30))], site=world.site2)
    lot = _lot_id(world, "A")
    forward = _ok(_draft(world, [_tline(world, 0, "6", [(lot, "6")])]), 201)
    backward = _ok(
        _draft(
            world,
            [_tline(world, 0, "7", [(lot, "7")])],
            source=world.site2,
            destination=world.site,
        ),
        201,
    )
    statuses = _race(
        app,
        world.owner.token,
        [
            (f"{TRANSFERS}/{forward['id']}/validate", None),
            (f"{TRANSFERS}/{backward['id']}/validate", None),
        ],
    )
    assert statuses == [200, 200]
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "A", world.site2)) == (
        "11.000",
        "9.000",
    )
    _both_invariants(owner_db, world, 0)


def test_transfer_and_sale_at_the_same_time(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    draft = _ok(_draft(world, [_tline(world, 0, "8", [(_lot_id(world, "A"), "8")])]), 201)
    statuses = _race(
        app,
        world.owner.token,
        [
            (f"{TRANSFERS}/{draft['id']}/validate", None),
            ("/pos/checkout", _pos_body(world, 0, "5")),
        ],
    )
    # Une seule des deux opérations passe ; jamais de double consommation du lot.
    assert sorted(s // 100 for s in statuses) == [2, 4]
    assert _balance(owner_db, world, "A") in {"2.000", "5.000"}
    _both_invariants(owner_db, world, 0)


def test_transfer_and_exit_at_the_same_time(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    lot = _lot_id(world, "A")
    draft = _ok(_draft(world, [_tline(world, 0, "8", [(lot, "8")])]), 201)
    exit_doc = _ok(_exit(world, [(0, "5", [(lot, "5")])]), 201)
    statuses = _race(
        app,
        world.owner.token,
        [
            (f"{TRANSFERS}/{draft['id']}/validate", None),
            (f"/stock/exits/{exit_doc['id']}/validate", None),
        ],
    )
    assert statuses == [200, 422]
    assert _balance(owner_db, world, "A") in {"2.000", "5.000"}
    _both_invariants(owner_db, world, 0)


# --- 27-29. CMUP (T-3, D-2) -----------------------------------------------------------------------


def _cmup_world(w: World) -> None:
    """Site source : 20 à 500 et 10 à 600 → CMUP 533,3333 ; site destination : 7 à 450.
    Article 0 suivi (lots A, B, D) ; article 2 NON suivi, mêmes réceptions (témoin)."""
    _track(w, 0)
    _receive(w, [_line(w, 0, "20", "500", lot="A", expiry=_iso(30))])
    _receive(w, [_line(w, 0, "10", "600", lot="B", expiry=_iso(60))])
    _receive(w, [_line(w, 0, "7", "450", lot="D", expiry=_iso(90))], w.site2)
    _receive(w, [_line(w, 2, "20", "500")])
    _receive(w, [_line(w, 2, "10", "600")])
    _receive(w, [_line(w, 2, "7", "450")], w.site2)


def test_split_transfer_cmup_same_cost_out_and_in(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _cmup_world(world)
    assert sh.level(owner_db, world, 0)[1] == "533.3333"
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    transfer = _transfer(world, [_tline(world, 0, "30", [(a, "20"), (b, "10")])])
    rows = _rows(owner_db, transfer["id"])
    cost = Decimal("533.3333")
    assert {r["unit_cost"] for r in rows} == {cost}
    value_out = sum(
        -Decimal(r["quantity"]) * r["unit_cost"] for r in rows if r["type"] == "TRANSFER_OUT"
    )
    value_in = sum(
        Decimal(r["quantity"]) * r["unit_cost"] for r in rows if r["type"] == "TRANSFER_IN"
    )
    assert value_out == value_in == Decimal("30") * cost
    line = transfer["lines"][0]
    assert (line["unit_cost"], line["amount"]) == ("533.3333", str(round_money(30 * cost)))
    # CMUP source inchangé ; CMUP destination calculé UNE fois sur 30 (et non 20 puis 10).
    expected = compute_average_cost(Decimal("7"), Decimal("450"), Decimal("30"), cost)
    chained = compute_average_cost(
        Decimal("27"),
        compute_average_cost(Decimal("7"), Decimal("450"), Decimal("20"), cost),
        Decimal("10"),
        cost,
    )
    assert expected != chained  # le découpage aurait changé le résultat
    assert sh.level(owner_db, world, 0)[1] == "533.3333"
    assert Decimal(sh.level(owner_db, world, 0, world.site2)[1]) == expected
    incoming = [r for r in rows if r["type"] == "TRANSFER_IN"]
    assert [(r["before"], r["after"]) for r in incoming] == [
        (Decimal("450.0000"), expected),
        (expected, expected),
    ]
    _both_invariants(owner_db, world, 0)


def test_split_transfer_cmup_identical_to_unsplit_transfer(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Article suivi réparti sur deux lots et article témoin NON suivi (une paire) : mêmes
    coûts, mêmes CMUP destination, mêmes montants."""
    _cmup_world(world)
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    transfer = _transfer(
        world, [_tline(world, 0, "30", [(a, "20"), (b, "10")]), _tline(world, 2, "30")]
    )
    tracked, witness = transfer["lines"]
    assert (tracked["unit_cost"], tracked["amount"]) == (witness["unit_cost"], witness["amount"])
    assert sh.level(owner_db, world, 0, world.site2) == sh.level(owner_db, world, 2, world.site2)
    assert sh.level(owner_db, world, 0) == sh.level(owner_db, world, 2)
    rows = _rows(owner_db, transfer["id"])
    assert [(r["type"], r["lot"]) for r in rows if r["lot"] is None] == [
        ("TRANSFER_OUT", None),
        ("TRANSFER_IN", None),
    ]


# --- 30-33. Annulation ----------------------------------------------------------------------------


def test_cancellation_restores_each_lot_exactly(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    a, b = _ab(world)
    cost_before = sh.level(owner_db, world, 0)[1]
    transfer = _transfer(world, [_tline(world, 0, "70", [(a, "50"), (b, "20")])])
    cancelled = _ok(_cancel(world, transfer))
    assert cancelled["status"] == "CANCELLED"
    rows = _rows(owner_db, transfer["id"])
    origins = {r["id"]: r for r in rows if r["type"] != "CANCELLATION"}
    inverses = [r for r in rows if r["type"] == "CANCELLATION"]
    assert len(inverses) == 4
    for inverse in inverses:
        origin = origins[inverse["origin"]]
        assert (inverse["lot_id"], inverse["site"], inverse["unit_cost"]) == (
            origin["lot_id"],
            origin["site"],
            origin["unit_cost"],
        )
        assert Decimal(inverse["quantity"]) == -Decimal(origin["quantity"])
    assert [_balance(owner_db, world, n) for n in "AB"] == ["100.000", "50.000"]
    assert [_balance(owner_db, world, n, world.site2) for n in "AB"] == ["0.000", "0.000"]
    assert sh.level(owner_db, world, 0) == ("150.000", cost_before)
    assert sh.level(owner_db, world, 0, world.site2)[0] == "0.000"
    _both_invariants(owner_db, world, 0)
    lots = _audit(world, "stock_transfer.cancelled")[-1]["data"]["lots"]
    assert sorted((lot["lot_number"], lot["base_quantity"]) for lot in lots) == [
        ("A", "-50.000"),
        ("A", "50.000"),
        ("B", "-20.000"),
        ("B", "20.000"),
    ]
    # Détail d'un transfert annulé : répartition réelle conservée.
    detail = _ok(world.owner.get(f"{TRANSFERS}/{transfer['id']}"))
    assert [(lot["lot_number"], lot["quantity"]) for lot in detail["lines"][0]["lots"]] == [
        ("A", "50.000"),
        ("B", "20.000"),
    ]


def test_cancellation_allowed_on_a_lot_expired_since(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    a, b = _ab(world)
    transfer = _transfer(world, [_tline(world, 0, "30", [(a, "20"), (b, "10")])])
    _expire(owner_db, "A")
    _ok(_cancel(world, transfer))
    assert [_balance(owner_db, world, n) for n in "AB"] == ["100.000", "50.000"]
    _both_invariants(owner_db, world, 0)


def test_cancellation_refused_entirely_when_destination_lot_is_short(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Lot A +50 au dépôt, puis 40 sortis du dépôt : il en faut 50, il en reste 10 — refus
    total, aucune restauration partielle."""
    a, b = _ab(world)
    transfer = _transfer(world, [_tline(world, 0, "70", [(a, "50"), (b, "20")])])
    exit_doc = _ok(
        world.owner.post(
            "/stock/exits",
            json={
                "site_id": world.site2,
                "reason_id": world.reasons["PERTE"],
                "lines": [
                    {
                        "article_id": world.articles[0],
                        "quantity": "40",
                        "lots": [{"lot_id": a, "quantity": "40"}],
                    }
                ],
            },
        ),
        201,
    )
    _ok(world.owner.post(f"/stock/exits/{exit_doc['id']}/validate"))
    response = _cancel(world, transfer)
    assert _code(response) == (422, "insufficient_lot_stock")
    assert response.json()["lots"][0]["lot_number"] == "A"
    assert response.json()["lots"][0]["available"] == "10.000"
    assert [_balance(owner_db, world, n, world.site2) for n in "AB"] == ["10.000", "20.000"]
    assert [_balance(owner_db, world, n) for n in "AB"] == ["50.000", "30.000"]
    assert sum(1 for r in _rows(owner_db, transfer["id"]) if r["type"] == "CANCELLATION") == 0
    assert _ok(world.owner.get(f"{TRANSFERS}/{transfer['id']}"))["status"] == "VALIDATED"


def test_double_cancellation_refused(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    a, b = _ab(world)
    transfer = _transfer(world, [_tline(world, 0, "30", [(a, "20"), (b, "10")])])
    _ok(_cancel(world, transfer))
    assert _code(_cancel(world, transfer)) == (409, "transfer_already_cancelled")
    assert sum(1 for r in _rows(owner_db, transfer["id"]) if r["type"] == "CANCELLATION") == 4
    assert [_balance(owner_db, world, n) for n in "AB"] == ["100.000", "50.000"]


# --- 22. Historique et audit ----------------------------------------------------------------------


def test_validation_audit_and_draft_snapshot(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    a, b = _ab(world)
    draft = _ok(_draft(world, [_tline(world, 0, "70", [(a, "50"), (b, "20")])]), 201)
    created = _audit(world, "stock_transfer.created")[-1]["data"]
    assert created["lines"][0]["lots"] == [
        {"lot_id": a, "base_quantity": "50.000"},
        {"lot_id": b, "base_quantity": "20.000"},
    ]
    _ok(_validate(world, draft))
    lots = _audit(world, "stock_transfer.validated")[-1]["data"]["lots"]
    assert [(lot["movement_type"], lot["lot_number"], lot["base_quantity"]) for lot in lots] == [
        ("TRANSFER_OUT", "A", "-50.000"),
        ("TRANSFER_IN", "A", "50.000"),
        ("TRANSFER_OUT", "B", "-20.000"),
        ("TRANSFER_IN", "B", "20.000"),
    ]
    # Journal des mouvements filtré par lot : les deux sites.
    movements = _ok(world.owner.get("/stock/movements", params={"lot_id": a, "limit": 50}))
    assert {m["movement_type"] for m in movements["items"]} >= {"TRANSFER_OUT", "TRANSFER_IN"}


# --- 34-35. Sécurité ------------------------------------------------------------------------------


def test_tenant_isolation_rls_and_composite_fk(
    world: World,
    lot_tracking_open: None,
    provision: Any,
    db: Session,
    owner_db: Session,
) -> None:
    _track(world, 0)
    _track(world, 1)
    _stock(world, 0, [("A", "10", _iso(30))])
    _stock(world, 1, [("X", "10", _iso(30))])
    lot = _lot_id(world, "A")
    draft = _ok(_draft(world, [_tline(world, 0, "5", [(lot, "5")])]), 201)
    alpha = owner_db.execute(
        text("SELECT tenant_id FROM stock_transfers WHERE id = :id"), {"id": draft["id"]}
    ).scalar_one()
    line = draft["lines"][0]["id"]
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    set_db_context(db, tenant_id=beta.tenant_id)
    assert db.execute(text("SELECT count(*) FROM stock_transfer_line_lots")).scalar_one() == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO stock_transfer_line_lots (id, tenant_id, transfer_line_id,"
                " article_id, lot_id, position, quantity)"
                " VALUES (gen_random_uuid(), :t, :l, :a, :lot, 9, 1)"
            ),
            {"t": alpha, "l": line, "a": world.articles[0], "lot": lot},
        )
    db.rollback()
    # FK composites : lot d'un autre article ; article différent de celui de la ligne.
    set_db_context(db, tenant_id=alpha)
    for article, other in (
        (world.articles[1], _lot_id(world, "X")),
        (world.articles[0], _lot_id(world, "X")),
    ):
        with pytest.raises(DBAPIError, match="foreign key"):
            db.execute(
                text(
                    "INSERT INTO stock_transfer_line_lots (id, tenant_id, transfer_line_id,"
                    " article_id, lot_id, position, quantity)"
                    " VALUES (gen_random_uuid(), :t, :l, :a, :lot, 9, 1)"
                ),
                {"t": alpha, "l": line, "a": article, "lot": other},
            )
        db.rollback()
        set_db_context(db, tenant_id=alpha)
    owner_db.expire_all()
    assert owner_db.execute(
        text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
            " WHERE relname = 'stock_transfer_line_lots'"
        )
    ).scalar_one()
    assert (
        owner_db.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'stockmanager_app'")
        ).scalar_one()
        is False
    )
    # Rôle de la console : aucun droit sur la table.
    assert not owner_db.execute(
        text(
            "SELECT has_table_privilege('stockmanager_platform', 'stock_transfer_line_lots',"
            " 'SELECT')"
        )
    ).scalar_one()


def test_permissions_and_site_scope(
    world: World, lot_tracking_open: None, client: TestClient
) -> None:
    """Aucune permission nouvelle : lots disponibles d'un transfert = ``stock.transfer.create``
    (le point d'accès des sorties n'est pas élargi) ; site source accessible et permission
    détenue sur lui ; validation = ``stock.transfer.validate`` sur les deux sites."""
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    lot = _lot_id(world, "A")
    params = {"article_id": world.articles[0], "site_id": world.site}
    exits_only = _member(world, client, "sorties@alpha.example.com", ["stock.exit.create"])
    assert exits_only.get(f"{TRANSFERS}/available-lots", params=params).status_code == 403
    transfers = _member(
        world,
        client,
        "transferts@alpha.example.com",
        ["stock.transfer.view", "stock.transfer.create"],
    )
    available = _ok(transfers.get(f"{TRANSFERS}/available-lots", params=params))
    assert [lot["number"] for lot in available["lots"]] == ["A"]
    # Aucun coût exposé.
    assert set(available["lots"][0]) == {
        "lot_id",
        "number",
        "quantity",
        "expiry_date",
        "manufacturing_date",
        "state",
        "expired",
        "created_at",
    }
    assert transfers.get("/stock/available-lots", params=params).status_code == 403
    # Sans ``stock.transfer.validate`` : brouillon possible, validation refusée.
    draft = _ok(_draft(world, [_tline(world, 0, "2", [(lot, "2")])], api=transfers), 201)
    assert _validate(world, draft, api=transfers).status_code == 403
    # Membre limité au site principal : dépôt inaccessible.
    local = _member(
        world,
        client,
        "local@alpha.example.com",
        ["stock.transfer.view", "stock.transfer.create"],
        all_sites=False,
        site_ids=[world.site],
    )
    assert _code(
        local.get(
            f"{TRANSFERS}/available-lots",
            params={"article_id": world.articles[0], "site_id": world.site2},
        )
    ) == (403, "site_access_denied")
    assert _code(_draft(world, [_tline(world, 0, "1", [(lot, "1")])], api=local)) == (
        403,
        "site_access_denied",
    )


# --- 36. Non-régression, garde-fou, P1-b ---------------------------------------------------------


def test_untracked_transfer_unchanged(world: World, owner_db: Session) -> None:
    """Article non suivi : une paire par ligne, sans lot ni répartition — P1-b fermée."""
    assert lot_tracking.LOT_TRACKING_AVAILABLE is False
    sh.validated_entry(world, [(1, "10", "100")])
    transfer = _transfer(world, [_tline(world, 1, "4")])
    rows = _rows(owner_db, transfer["id"])
    assert [(r["type"], r["lot"], r["quantity"]) for r in rows] == [
        ("TRANSFER_OUT", None, "-4.000"),
        ("TRANSFER_IN", None, "4.000"),
    ]
    assert transfer["lines"][0]["lots"] == []
    assert sh.level(owner_db, world, 1, world.site2) == ("4.000", "100.0000")
    assert sh.count(owner_db, "SELECT count(*) FROM stock_transfer_line_lots") == 0
    assert sh.count(owner_db, "SELECT count(*) FROM stock_lot_levels") == 0
    _ok(_cancel(world, transfer))
    assert sh.level(owner_db, world, 1)[0] == "10.000"


def test_guard_still_refuses_a_tracked_transfer_without_lot(
    world: World, lot_tracking_open: None, db: Session, owner_db: Session
) -> None:
    """Garde-fou (O-6) : l'ancien chemin sans lot (paire unique) reste refusé pour un article
    suivi ; seul ``transfer_lots`` transfère un article suivi."""
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(30))])
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": world.site}
    ).scalar_one()
    set_db_context(db, tenant_id=tenant)
    with pytest.raises(BusinessRuleError) as refused:
        StockService(db, tenant, None, utcnow()).transfer(
            uuid.UUID(world.site),
            uuid.UUID(world.site2),
            [TransferItem(uuid.uuid4(), uuid.UUID(world.articles[0]), Decimal("1"))],
            source_type="test",
            source_id=uuid.uuid4(),
            source_number="T-1",
        )
    assert refused.value.code == "lot_required"
    db.rollback()
    assert sh.level(owner_db, world, 0)[0] == "5.000"


def test_transfer_service_uses_the_lot_engine_only() -> None:
    """Test statique : le service des transferts passe par ``transfer_lots`` ; aucun module
    n'appelle l'ancien chemin sans lot."""
    source = inspect.getsource(transfer_service)
    assert ".transfer_lots(" in source
    assert ".transfer(" not in source
    app_dir = Path(transfer_service.__file__).resolve().parents[2]
    callers = [
        path
        for path in app_dir.rglob("*.py")
        if ".transfer(" in path.read_text(encoding="utf-8") and path.name != "stock_service.py"
    ]
    assert callers == []
