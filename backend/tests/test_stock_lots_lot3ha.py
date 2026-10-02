"""Lot 3-H-A (ADR-0045, H-D1 à H-D18, O-1 à O-6) — consommation des lots : ventes, POS, sorties
et annulations.

- Moteur UNIQUE (``StockService.consume``) : FEFO (lots non périmés, péremption croissante, puis
  création, puis numéro ; sans date en dernier) ou FIFO (sans suivi de péremption) ; plusieurs
  lots par ligne, un mouvement par lot (M1) ; tout ou rien ; Σ lots = stock.
- Lots périmés : jamais consommés automatiquement ; vente refusée si le stock non périmé ne
  suffit pas (``insufficient_unexpired_stock``) ; dérogation explicite (permission
  ``sales.sale.expired_lot_override``, lots désignés, motif, audit).
- Sorties : choix manuel, brouillon incomplet admis, somme exacte exigée à la validation.
- Annulations : restauration exacte par mouvement d'origine, même sur un lot devenu périmé.
- Garde-fou (O-6) : aucun mouvement sans lot pour un article suivi.
- P1-b reste active : les tests l'ouvrent avec la fixture ``lot_tracking_open`` (réservée aux
  tests, ``monkeypatch`` dans ce processus).
"""

import threading
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.core.errors import BusinessRuleError
from app.modules.catalog import lot_tracking
from app.modules.stock.models import MovementType
from app.modules.stock.stock_service import (
    MovementRequest,
    PackagingSnapshot,
    StockService,
    split_packaging,
)
from app.shared.clock import utcnow
from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World
from tests.test_stock_lots_lot3g import (
    _audit,
    _code,
    _invariant,
    _iso,
    _line,
    _lot,
    _member,
    _ok,
    _receive,
    _track,
)

# --- Aides ----------------------------------------------------------------------------------------


def _stock(w: World, index: int, lots: list[tuple[str, str, str | None]], site: str | None = None):
    """Réceptions SÉPARÉES (une par lot, dans l'ordre) : dates de création distinctes."""
    for number, quantity, expiry in lots:
        _receive(w, [_line(w, index, quantity, lot=number, expiry=expiry)], site)


def _lot_id(w: World, number: str, **params: Any) -> str:
    return str(_lot(w, number, **params)["id"])


def _balance(owner_db: Session, w: World, number: str, site: str | None = None) -> str:
    owner_db.expire_all()
    value = owner_db.execute(
        text(
            "SELECT l.quantity::text FROM stock_lot_levels l JOIN stock_lots s ON s.id = l.lot_id "
            "WHERE s.number = :n AND l.site_id = :site"
        ),
        {"n": number, "site": site or w.site},
    ).scalar_one_or_none()
    return value or "none"


def _moves(owner_db: Session, source_id: str, kind: str = "SALE") -> list[tuple[str, str]]:
    """(numéro du lot, quantité) des mouvements d'un document, dans l'ordre d'écriture."""
    owner_db.expire_all()
    return [
        (row[0] or "-", row[1])
        for row in owner_db.execute(
            text(
                "SELECT s.number, m.quantity::text FROM stock_movements m "
                "LEFT JOIN stock_lots s ON s.id = m.lot_id "
                "WHERE m.source_id = :id AND m.movement_type = :k ORDER BY m.occurred_at, m.id"
            ),
            {"id": source_id, "k": kind},
        )
    ]


def _sale_body(
    w: World, lines: list[tuple[int, str]], packaging: str | None = None
) -> dict[str, Any]:
    body_lines = []
    for index, quantity in lines:
        line: dict[str, Any] = {"article_id": w.articles[index], "quantity": quantity}
        if packaging is not None:
            line["packaging_id"] = packaging
        body_lines.append(line)
    return {"site_id": w.site, "customer_id": sh.credit_customer(w), "lines": body_lines}


def _draft_sale(w: World, lines: list[tuple[int, str]], api: Api | None = None, **kw: Any):
    return _ok((api or w.owner).post("/sales", json=_sale_body(w, lines, **kw)), 201)


def _sell(
    w: World, lines: list[tuple[int, str]], api: Api | None = None, body: Any = None, **kw: Any
) -> tuple[dict[str, Any], Any]:
    sale = _draft_sale(w, lines, api, **kw)
    return sale, (api or w.owner).post(f"/sales/{sale['id']}/validate", json=body)


def _override(w: World, picks: list[tuple[int, str, str]], reason: str = "Destockage autorisé"):
    return {
        "expired_lot_override": {
            "reason": reason,
            "lots": [
                {"article_id": w.articles[i], "lot_id": lot, "quantity": q} for i, lot, q in picks
            ],
        }
    }


def _exit(
    w: World,
    lines: list[tuple[int, str, list[tuple[str, str]]]],
    api: Api | None = None,
    reason: str = "PERTE",
    **line_extra: Any,
) -> Any:
    return (api or w.owner).post(
        "/stock/exits",
        json={
            "site_id": w.site,
            "reason_id": w.reasons[reason],
            "lines": [
                {
                    "article_id": w.articles[i],
                    "quantity": q,
                    "lots": [{"lot_id": lot, "quantity": lq} for lot, lq in lots],
                    **line_extra,
                }
                for i, q, lots in lines
            ],
        },
    )


def _role_member(w: World, client: TestClient, email: str, extra: list[str]) -> Api:
    base = [
        "sales.sale.view",
        "sales.sale.create",
        "sales.sale.validate",
        "sales.sale.credit_create",
        "customers.customer.view",
    ]
    return _member(w, client, email, base + extra)


# --- 1-4. Ordre de consommation ---------------------------------------------------------------


def test_fefo_order_non_expired_by_expiry_date(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(60)), ("B", "5", _iso(10)), ("C", "10", _iso(30))])
    sale, response = _sell(world, [(0, "12")])
    _ok(response)
    assert _moves(owner_db, sale["id"]) == [("B", "-5.000"), ("C", "-7.000")]
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "C")) == ("10.000", "3.000")
    _invariant(owner_db, world, 0)


def test_fifo_without_expiry_tracking(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """H-D4 : article suivi par lot SANS suivi de péremption — ordre de création, puis numéro."""
    _track(world, 0, expiry=False)
    _stock(world, 0, [("Z-OLD", "4", None), ("A-NEW", "10", None)])
    sale, response = _sell(world, [(0, "6")])
    _ok(response)
    assert _moves(owner_db, sale["id"]) == [("Z-OLD", "-4.000"), ("A-NEW", "-2.000")]
    _invariant(owner_db, world, 0)


def test_lots_without_expiry_date_come_last(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """H-D3 : un lot sans date (antérieur au suivi de péremption) passe après les lots datés,
    même s'il est plus ancien."""
    _track(world, 0)
    _stock(world, 0, [("DATED", "5", _iso(90))])
    lot = _lot(world, "DATED")
    # Lot ancien sans date, créé directement (données antérieures), invariant conservé.
    undated = str(uuid.uuid4())
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM stock_lots WHERE id = :id"), {"id": lot["id"]}
    ).scalar_one()
    owner_db.execute(
        text(
            "INSERT INTO stock_lots (id, tenant_id, article_id, number, created_at, updated_at) "
            "VALUES (:id, :t, :a, 'UNDATED', now() - interval '1 year', now())"
        ),
        {"id": undated, "t": tenant, "a": world.articles[0]},
    )
    owner_db.execute(
        text(
            "INSERT INTO stock_lot_levels (id, tenant_id, site_id, article_id, lot_id, quantity) "
            "VALUES (gen_random_uuid(), :t, :s, :a, :l, 5)"
        ),
        {"t": tenant, "s": world.site, "a": world.articles[0], "l": undated},
    )
    owner_db.execute(
        text(
            "UPDATE stock_levels SET quantity = quantity + 5 WHERE site_id = :s AND article_id = :a"
        ),
        {"s": world.site, "a": world.articles[0]},
    )
    owner_db.commit()
    lots = _ok(
        world.owner.get(f"/sales/articles/{world.articles[0]}/lots", params={"site_id": world.site})
    )["lots"]
    assert [entry["number"] for entry in lots] == ["DATED", "UNDATED"]
    sale, response = _sell(world, [(0, "7")])
    _ok(response)
    assert _moves(owner_db, sale["id"]) == [("DATED", "-5.000"), ("UNDATED", "-2.000")]
    _invariant(owner_db, world, 0)


def test_order_is_deterministic_creation_then_number(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Même péremption, même réception (même date de création) : numéro de lot."""
    _track(world, 0)
    same = _iso(20)
    _receive(
        world,
        [
            _line(world, 0, "3", lot="LOT-B", expiry=same),
            _line(world, 0, "3", lot="LOT-A", expiry=same),
        ],
    )
    first = _ok(
        world.owner.get(f"/sales/articles/{world.articles[0]}/lots", params={"site_id": world.site})
    )["lots"]
    second = _ok(
        world.owner.get(f"/sales/articles/{world.articles[0]}/lots", params={"site_id": world.site})
    )["lots"]
    assert (
        [lot["number"] for lot in first]
        == [lot["number"] for lot in second]
        == [
            "LOT-A",
            "LOT-B",
        ]
    )
    sale, response = _sell(world, [(0, "4")])
    _ok(response)
    assert _moves(owner_db, sale["id"]) == [("LOT-A", "-3.000"), ("LOT-B", "-1.000")]


# --- 5-6. Plusieurs lots par ligne, stock insuffisant --------------------------------------------


def test_one_line_split_over_three_lots(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "30", _iso(10)), ("B", "50", _iso(20)), ("C", "40", _iso(30))])
    sale, response = _sell(world, [(0, "100")])
    validated = _ok(response)
    assert len(validated["lines"]) == 1
    assert [(lot["lot_number"], lot["quantity"]) for lot in validated["lines"][0]["lots"]] == [
        ("A", "30.000"),
        ("B", "50.000"),
        ("C", "20.000"),
    ]
    assert _moves(owner_db, sale["id"]) == [("A", "-30.000"), ("B", "-50.000"), ("C", "-20.000")]
    assert sh.level(owner_db, world, 0)[0] == "20.000"
    _invariant(owner_db, world, 0)


def test_insufficient_stock_refuses_everything(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "20", _iso(10)), ("B", "20", _iso(20))])
    sale, response = _sell(world, [(0, "50")])
    assert _code(response) == (422, "insufficient_stock")
    # Aucune consommation partielle : soldes, stock, statut et journal inchangés.
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "B")) == (
        "20.000",
        "20.000",
    )
    assert sh.level(owner_db, world, 0)[0] == "40.000"
    assert _moves(owner_db, sale["id"]) == []
    assert _ok(world.owner.get(f"/sales/{sale['id']}"))["status"] == "DRAFT"


# --- 7-10. Lots périmés et dérogation -------------------------------------------------------------


def test_expired_lot_never_chosen_and_sale_refused(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """O-1 : A 5 périmé, B 3 valide — vente de 5 refusée (A exclu, B insuffisant) ; vente de 3
    servie par B même si A expire plus tôt."""
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(-3)), ("B", "3", _iso(40))])
    sale, response = _sell(world, [(0, "5")])
    assert _code(response) == (422, "insufficient_unexpired_stock")
    detail = response.json()["articles"][0]
    assert (detail["missing"], detail["expired_available"]) == ("2.000", "5.000")
    assert [lot["lot_number"] for lot in detail["expired_lots"]] == ["A"]
    assert _moves(owner_db, sale["id"]) == []
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "B")) == ("5.000", "3.000")
    other, response = _sell(world, [(0, "3")])
    _ok(response)
    assert _moves(owner_db, other["id"]) == [("B", "-3.000")]
    _invariant(owner_db, world, 0)


def test_expired_lot_override_explicit_reason_audit(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(-3)), ("B", "3", _iso(40))])
    expired = _lot_id(world, "A")
    sale, response = _sell(world, [(0, "5")], body=_override(world, [(0, expired, "2")]))
    validated = _ok(response)
    # Lot périmé désigné d'abord, reste en FEFO sur les lots non périmés.
    assert _moves(owner_db, sale["id"]) == [("A", "-2.000"), ("B", "-3.000")]
    assert validated["expired_lot_override_reason"] == "Destockage autorisé"
    assert validated["expired_lot_override_by_name"] is not None
    assert validated["expired_lot_override_at"] is not None
    entry = _audit(world, "sale.expired_lot_overridden")[-1]
    assert entry["entity_id"] == sale["id"]
    assert entry["data"]["reason"] == "Destockage autorisé"
    assert [
        (lot["reference"], lot["lot_number"], lot["base_quantity"]) for lot in entry["data"]["lots"]
    ] == [("A-0", "A", "2.000")]
    validated_audit = _audit(world, "sale.validated")[-1]
    assert [lot["lot_number"] for lot in validated_audit["data"]["lots"]] == ["A", "B"]
    _invariant(owner_db, world, 0)


def test_override_rules_reason_and_expired_lots_only(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(-3)), ("B", "3", _iso(40))])
    expired, valid = _lot_id(world, "A"), _lot_id(world, "B")
    sale = _draft_sale(world, [(0, "5")])
    path = f"/sales/{sale['id']}/validate"
    # Motif obligatoire (au moins 5 caractères).
    for reason in ("", "abc"):
        body = _override(world, [(0, expired, "2")], reason=reason)
        assert world.owner.post(path, json=body).status_code == 422
    without_reason = {
        "expired_lot_override": {
            "lots": [{"article_id": world.articles[0], "lot_id": expired, "quantity": "2"}]
        }
    }
    assert world.owner.post(path, json=without_reason).status_code == 422
    # Un lot NON périmé ne se désigne jamais (le FEFO décide).
    assert _code(world.owner.post(path, json=_override(world, [(0, valid, "2")]))) == (
        422,
        "lot_not_expired",
    )
    # Plus que la quantité vendue de l'article : refus.
    assert _code(world.owner.post(path, json=_override(world, [(0, expired, "6")]))) == (
        422,
        "lot_allocation_exceeds",
    )
    # Lot d'un autre article : indisponible.
    _track(world, 1)
    _stock(world, 1, [("X", "2", _iso(-1))])
    assert _code(
        world.owner.post(path, json=_override(world, [(0, _lot_id(world, "X"), "1")]))
    ) == (422, "lot_not_available")
    # Rien n'a été écrit.
    assert _moves(owner_db, sale["id"]) == []
    assert _ok(world.owner.get(f"/sales/{sale['id']}"))["status"] == "DRAFT"


def test_override_permission(world: World, lot_tracking_open: None, client: TestClient) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(-3)), ("B", "3", _iso(40))])
    expired = _lot_id(world, "A")
    seller = _role_member(world, client, "vendeur@alpha.example.com", [])
    sale, response = _sell(
        world, [(0, "4")], api=seller, body=_override(world, [(0, expired, "1")])
    )
    assert _code(response) == (403, "expired_lot_override_not_allowed")
    # Rôle personnalisé accordant la dérogation : accepté.
    allowed = _role_member(
        world, client, "derog@alpha.example.com", ["sales.sale.expired_lot_override"]
    )
    _, response = _sell(world, [(0, "4")], api=allowed, body=_override(world, [(0, expired, "1")]))
    _ok(response)


def test_base_roles_and_permission_declaration(world: World, client: TestClient) -> None:
    """Permission d'écriture déclarée par le module Ventes ; Administrateur seulement par défaut
    (``*``) ; ni Gestionnaire, ni Vendeur, ni Consultant."""
    permissions = {p["code"]: p for p in _ok(world.owner.get("/permissions"))}
    assert permissions["sales.sale.expired_lot_override"]["access"] == "write"
    roles = {r["template_code"]: r for r in _ok(world.owner.get("/roles")) if r["template_code"]}
    assert "sales.sale.expired_lot_override" in roles["administrator"]["permission_codes"]
    for code in ("manager", "seller", "viewer"):
        assert "sales.sale.expired_lot_override" not in roles[code]["permission_codes"]


# --- 11-12. Garde-fou et articles non suivis -----------------------------------------------------


def test_guard_refuses_any_movement_without_lot_for_tracked_article(
    world: World, lot_tracking_open: None, db: Session, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "5", _iso(30))])
    # Transfert sans répartition par lot (Lot 3-H-B1) : refus du serveur, rien n'est écrit.
    transfer = _ok(
        world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site,
                "destination_site_id": world.site2,
                "lines": [{"article_id": world.articles[0], "quantity": "1"}],
            },
        ),
        201,
    )
    assert _code(world.owner.post(f"/stock/transfers/{transfer['id']}/validate")) == (
        422,
        "lot_allocation_incomplete",
    )
    assert sh.level(owner_db, world, 0)[0] == "5.000"
    # Directement au moteur : refus, avec ou sans quantité suffisante.
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": world.site}
    ).scalar_one()
    set_db_context(db, tenant_id=tenant)
    service = StockService(db, tenant, None, utcnow())
    request = MovementRequest(
        article_id=uuid.UUID(world.articles[0]),
        movement_type=MovementType.EXIT,
        quantity=Decimal("-1"),
        source_type="test",
        source_id=uuid.uuid4(),
        source_line_id=uuid.uuid4(),
    )
    with pytest.raises(BusinessRuleError) as refused:
        service.apply(uuid.UUID(world.site), [request])
    assert refused.value.code == "lot_required"
    db.rollback()
    # Un lot sur un article NON suivi est tout aussi refusé.
    set_db_context(db, tenant_id=tenant)
    with pytest.raises(BusinessRuleError) as unexpected:
        StockService(db, tenant, None, utcnow()).apply(
            uuid.UUID(world.site),
            [
                MovementRequest(
                    article_id=uuid.UUID(world.articles[1]),
                    movement_type=MovementType.ENTRY,
                    quantity=Decimal("1"),
                    unit_cost=Decimal("1"),
                    source_type="test",
                    source_id=uuid.uuid4(),
                    source_line_id=uuid.uuid4(),
                    lot_id=uuid.UUID(_lot_id(world, "A")),
                )
            ],
        )
    assert unexpected.value.code == "article_not_lot_tracked"
    db.rollback()


def test_untracked_article_unchanged(world: World, owner_db: Session) -> None:
    """Article non suivi : un mouvement par ligne, sans lot, comme avant le Lot 3-H — P1-b
    toujours fermée dans ce test (aucune fixture)."""
    assert lot_tracking.LOT_TRACKING_AVAILABLE is False
    sh.validated_entry(world, [(0, "10", "100")])
    sale, response = _sell(world, [(0, "4")])
    validated = _ok(response)
    assert validated["lines"][0]["lots"] == []
    assert _moves(owner_db, sale["id"]) == [("-", "-4.000")]
    exit_doc = sh.exit_doc(world, [(0, "2")])
    _ok(world.owner.post(f"/stock/exits/{exit_doc['id']}/validate"))
    assert _moves(owner_db, exit_doc["id"], "EXIT") == [("-", "-2.000")]
    _ok(world.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur de saisie"}))
    assert _moves(owner_db, sale["id"], "CANCELLATION") == [("-", "4.000")]
    assert sh.level(owner_db, world, 0)[0] == "8.000"
    # Lots refusés sur un article non suivi (sortie).
    assert _code(_exit(world, [(0, "1", [(str(uuid.uuid4()), "1")])])) == (
        422,
        "article_not_lot_tracked",
    )


# --- 13. Concurrence ------------------------------------------------------------------------------


def _concurrent(app: Any, token: str, bodies: list[dict[str, Any]]) -> list[int]:
    barrier = threading.Barrier(len(bodies))
    statuses: list[int] = []

    def run(body: dict[str, Any]) -> None:
        with TestClient(app) as client:
            barrier.wait()
            statuses.append(Api(client, token).post("/pos/checkout", json=body).status_code)

    threads = [threading.Thread(target=run, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return sorted(statuses)


def _pos_body(w: World, index: int, quantity: str, **extra: Any) -> dict[str, Any]:
    return {
        "site_id": w.site,
        "customer_id": sh.credit_customer(w),
        "lines": [{"article_id": w.articles[index], "quantity": quantity}],
        "idempotency_key": str(uuid.uuid4()),
        **extra,
    }


def test_concurrent_sales_continue_on_next_lot(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(10)), ("B", "10", _iso(20))])
    statuses = _concurrent(
        app, world.owner.token, [_pos_body(world, 0, "8"), _pos_body(world, 0, "5")]
    )
    assert statuses == [201, 201]
    # Quel que soit l'ordre : A épuisé, B entamé de 3 ; aucune double consommation.
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "B")) == ("0.000", "7.000")
    assert sh.level(owner_db, world, 0)[0] == "7.000"
    _invariant(owner_db, world, 0)


def test_concurrent_sales_on_a_single_lot_never_oversell(
    world: World, lot_tracking_open: None, owner_db: Session, app: Any
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(10))])
    statuses = _concurrent(
        app, world.owner.token, [_pos_body(world, 0, "8"), _pos_body(world, 0, "5")]
    )
    assert statuses == [201, 422]
    assert _balance(owner_db, world, "A") in {"2.000", "5.000"}
    _invariant(owner_db, world, 0)
    assert sh.count(owner_db, "SELECT count(*) FROM sales WHERE status = 'VALIDATED'") == 1


# --- 14-18. Ventes : mono / multi-lots, annulation -----------------------------------------------


def test_single_lot_sale_detail_and_history(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    sale, response = _sell(world, [(0, "4")])
    line = _ok(response)["lines"][0]
    assert [(lot["lot_number"], lot["quantity"], lot["expiry_date"]) for lot in line["lots"]] == [
        ("A", "4.000", _iso(30))
    ]
    # Détail relu : même répartition (journal des mouvements).
    assert _ok(world.owner.get(f"/sales/{sale['id']}"))["lines"][0]["lots"] == line["lots"]
    movements = _ok(world.owner.get("/stock/movements", params={"source_id": sale["id"]}))
    assert [(m["lot_number"], m["quantity"]) for m in movements["items"]] == [("A", "-4.000")]


def test_cancel_multi_lot_sale_restores_exact_lots_once(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "20", _iso(10)), ("B", "50", _iso(20))])
    sale, response = _sell(world, [(0, "30")])
    _ok(response)
    assert _moves(owner_db, sale["id"]) == [("A", "-20.000"), ("B", "-10.000")]
    _ok(world.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur de caisse"}))
    assert _moves(owner_db, sale["id"], "CANCELLATION") == [("A", "20.000"), ("B", "10.000")]
    # Chaque inverse est lié à son mouvement d'origine, du même lot.
    owner_db.expire_all()
    pairs = owner_db.execute(
        text(
            "SELECT c.lot_id = o.lot_id AND c.quantity = -o.quantity FROM stock_movements c "
            "JOIN stock_movements o ON o.id = c.origin_movement_id WHERE c.source_id = :id"
        ),
        {"id": sale["id"]},
    ).scalars()
    assert list(pairs) == [True, True]
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "B")) == (
        "20.000",
        "50.000",
    )
    # Seconde annulation : refusée, rien ne bouge.
    assert _code(world.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Encore"})) == (
        409,
        "sale_already_cancelled",
    )
    assert _moves(owner_db, sale["id"], "CANCELLATION") == [("A", "20.000"), ("B", "10.000")]
    lots = _audit(world, "sale.cancelled")[-1]["data"]["lots"]
    assert [(lot["lot_number"], lot["base_quantity"]) for lot in lots] == [
        ("A", "20.000"),
        ("B", "10.000"),
    ]
    _invariant(owner_db, world, 0)


def test_cancel_restores_lot_even_if_expired_since(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "6", _iso(5))])
    sale, response = _sell(world, [(0, "4")])
    _ok(response)
    # Le temps passe : le lot est désormais périmé (date simulée par le rôle propriétaire).
    owner_db.execute(
        text("UPDATE stock_lots SET expiry_date = :d WHERE number = 'A'"), {"d": _iso(-1)}
    )
    owner_db.commit()
    assert _lot(world, "A")["state"] == "expired"
    _ok(world.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Retour comptoir"}))
    assert _moves(owner_db, sale["id"], "CANCELLATION") == [("A", "4.000")]
    assert _balance(owner_db, world, "A") == "6.000"
    _invariant(owner_db, world, 0)


def test_pos_checkout_fefo_lots_and_dedicated_endpoint(
    world: World, lot_tracking_open: None, owner_db: Session, client: TestClient
) -> None:
    _track(world, 0)
    _stock(world, 0, [("LATE", "10", _iso(50)), ("SOON", "2", _iso(5)), ("OLD", "4", _iso(-1))])
    available = _ok(
        world.owner.get(f"/pos/articles/{world.articles[0]}/lots", params={"site_id": world.site})
    )
    assert (available["lot_tracked"], available["expiry_tracked"]) == (True, True)
    assert [(lot["number"], lot["expired"], lot["state"]) for lot in available["lots"]] == [
        ("SOON", False, "expiring_soon"),
        ("LATE", False, "ok"),
        ("OLD", True, "expired"),
    ]
    assert not {"unit_cost", "average_cost", "cost"} & set(available["lots"][0])
    checkout = _ok(world.owner.post("/pos/checkout", json=_pos_body(world, 0, "5")), 201)
    lots = checkout["sale"]["lines"][0]["lots"]
    assert [(lot["lot_number"], lot["quantity"]) for lot in lots] == [
        ("SOON", "2.000"),
        ("LATE", "3.000"),
    ]
    # Dérogation au POS : même règle, lot périmé désigné explicitement.
    body = _pos_body(world, 0, "1", **_override(world, [(0, _lot_id(world, "OLD"), "1")]))
    overridden = _ok(world.owner.post("/pos/checkout", json=body), 201)
    assert overridden["sale"]["lines"][0]["lots"][0]["lot_number"] == "OLD"
    # Point d'accès du POS : ``pos.terminal.use`` exigé.
    outsider = _member(world, client, "sanspos@alpha.example.com", ["sales.sale.view"])
    assert outsider.get(f"/pos/articles/{world.articles[0]}/lots").status_code == 403
    _invariant(owner_db, world, 0)


# --- 19-23. Sorties -------------------------------------------------------------------------------


def test_exit_single_and_multi_lot(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "20", _iso(30)), ("B", "30", _iso(60))])
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    single = _ok(_exit(world, [(0, "5", [(b, "5")])]), 201)
    _ok(world.owner.post(f"/stock/exits/{single['id']}/validate"))
    assert _moves(owner_db, single["id"], "EXIT") == [("B", "-5.000")]
    multi = _ok(_exit(world, [(0, "40", [(a, "20"), (b, "20")])]), 201)
    validated = _ok(world.owner.post(f"/stock/exits/{multi['id']}/validate"))
    assert _moves(owner_db, multi["id"], "EXIT") == [("A", "-20.000"), ("B", "-20.000")]
    line = validated["lines"][0]
    assert [(lot["lot_number"], lot["quantity"]) for lot in line["lots"]] == [
        ("A", "20.000"),
        ("B", "20.000"),
    ]
    # Coût figé = CMUP du site (identique pour les deux mouvements), montant de la ligne.
    assert line["unit_cost"] == "100.0000" and line["amount"] == "4000.00"
    entry = _audit(world, "stock_exit.validated")[-1]
    assert [lot["lot_number"] for lot in entry["data"]["lots"]] == ["A", "B"]
    _invariant(owner_db, world, 0)


def test_exit_draft_may_be_incomplete_but_validation_requires_exact_sum(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "60", _iso(30)), ("B", "60", _iso(60))])
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    draft = _ok(_exit(world, [(0, "100", [(a, "60")])]), 201)
    assert [(lot["lot_number"], lot["quantity"]) for lot in draft["lines"][0]["lots"]] == [
        ("A", "60.000")
    ]
    assert _code(world.owner.post(f"/stock/exits/{draft['id']}/validate")) == (
        422,
        "lot_allocation_incomplete",
    )
    assert _moves(owner_db, draft["id"], "EXIT") == []
    # Sans aucun choix : même refus.
    empty = _ok(_exit(world, [(0, "10", [])]), 201)
    assert _code(world.owner.post(f"/stock/exits/{empty['id']}/validate")) == (
        422,
        "lot_allocation_incomplete",
    )
    # Brouillon complété (lignes et choix remplacés), puis validé.
    body = {
        "reason_id": world.reasons["PERTE"],
        "lines": [
            {
                "article_id": world.articles[0],
                "quantity": "100",
                "lots": [{"lot_id": a, "quantity": "60"}, {"lot_id": b, "quantity": "40"}],
            }
        ],
    }
    _ok(world.owner.put(f"/stock/exits/{draft['id']}", json=body))
    _ok(world.owner.post(f"/stock/exits/{draft['id']}/validate"))
    assert _moves(owner_db, draft["id"], "EXIT") == [("A", "-60.000"), ("B", "-40.000")]
    _invariant(owner_db, world, 0)


def test_exit_choice_rules(world: World, lot_tracking_open: None) -> None:
    _track(world, 0)
    _track(world, 1)
    _stock(world, 0, [("A", "10", _iso(30))])
    _stock(world, 1, [("X", "10", _iso(30))])
    a, x = _lot_id(world, "A"), _lot_id(world, "X")
    assert _code(_exit(world, [(0, "5", [(a, "3"), (a, "2")])])) == (
        422,
        "duplicate_lot_allocation",
    )
    assert _code(_exit(world, [(0, "5", [(x, "5")])])) == (422, "lot_not_available")
    assert _code(_exit(world, [(0, "5", [(str(uuid.uuid4()), "5")])])) == (
        422,
        "lot_not_available",
    )
    assert _code(_exit(world, [(0, "5", [(a, "6")])])) == (422, "lot_allocation_exceeds")
    # Choix supérieur au solde : accepté au brouillon, refusé à la validation (solde relu).
    over = _ok(_exit(world, [(0, "12", [(a, "12")])]), 201)
    assert _code(world.owner.post(f"/stock/exits/{over['id']}/validate"))[1] in {
        "insufficient_stock",
        "insufficient_lot_stock",
    }


def test_exit_of_expired_lot_allowed(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """H-D5 : une sortie (destruction, mise au rebut) peut viser un lot périmé."""
    _track(world, 0)
    _stock(world, 0, [("OLD", "4", _iso(-10))])
    draft = _ok(_exit(world, [(0, "4", [(_lot_id(world, "OLD"), "4")])]), 201)
    assert draft["lines"][0]["lots"][0]["state"] == "expired"
    _ok(world.owner.post(f"/stock/exits/{draft['id']}/validate"))
    assert _balance(owner_db, world, "OLD") == "0.000"
    _invariant(owner_db, world, 0)


def test_cancel_multi_lot_exit(world: World, lot_tracking_open: None, owner_db: Session) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "20", _iso(30)), ("B", "30", _iso(60))])
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    document = _ok(_exit(world, [(0, "25", [(a, "15"), (b, "10")])]), 201)
    _ok(world.owner.post(f"/stock/exits/{document['id']}/validate"))
    _ok(world.owner.post(f"/stock/exits/{document['id']}/cancel", json={"reason": "Erreur"}))
    assert _moves(owner_db, document["id"], "CANCELLATION") == [("A", "15.000"), ("B", "10.000")]
    assert (_balance(owner_db, world, "A"), _balance(owner_db, world, "B")) == (
        "20.000",
        "30.000",
    )
    assert _code(
        world.owner.post(f"/stock/exits/{document['id']}/cancel", json={"reason": "Encore"})
    ) == (409, "document_not_validated")
    _invariant(owner_db, world, 0)


# --- 24-28. Conditionnements ------------------------------------------------------------------


def _packaging(w: World, index: int, name: str, conversion: str, price: str = "3000") -> str:
    created = _ok(
        w.owner.post(
            f"/catalog/articles/{w.articles[index]}/packagings",
            json={"name": name, "conversion": conversion, "sale_price": price},
        ),
        201,
    )
    return str(created["id"])


def _packaged_moves(owner_db: Session, source_id: str) -> list[tuple[str, str, str | None]]:
    owner_db.expire_all()
    return [
        (row[0], row[1], row[2])
        for row in owner_db.execute(
            text(
                "SELECT s.number, m.quantity::text, m.packaging_quantity::text "
                "FROM stock_movements m JOIN stock_lots s ON s.id = m.lot_id "
                "WHERE m.source_id = :id AND m.movement_type = 'SALE' ORDER BY m.occurred_at, m.id"
            ),
            {"id": source_id},
        )
    ]


def test_sale_in_packaging_single_lot(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    _track(world, 0)
    carton = _packaging(world, 0, "Carton 24", "24")
    _stock(world, 0, [("A", "100", _iso(30))])
    sale, response = _sell(world, [(0, "2")], packaging=carton)
    _ok(response)
    assert _packaged_moves(owner_db, sale["id"]) == [("A", "-48.000", "2.000")]


def test_split_packaging_kept_only_when_exact(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """O-3 : 2 cartons de 24 = 48. A 24 / B 30 → 1 carton + 1 carton ; A 30 / B 30 → 30 et 18
    bouteilles sans présentation (jamais « 1,25 carton » pour un article entier)."""
    _track(world, 0)
    carton = _packaging(world, 0, "Carton 24", "24")
    _stock(world, 0, [("A", "24", _iso(10)), ("B", "30", _iso(20))])
    sale, response = _sell(world, [(0, "2")], packaging=carton)
    _ok(response)
    assert _packaged_moves(owner_db, sale["id"]) == [
        ("A", "-24.000", "1.000"),
        ("B", "-24.000", "1.000"),
    ]
    _stock(world, 0, [("C", "24", _iso(30))])  # B 6, C 24 restants
    _stock(world, 0, [("D", "30", _iso(40))])
    sale, response = _sell(world, [(0, "2")], packaging=carton)
    _ok(response)
    assert _packaged_moves(owner_db, sale["id"]) == [
        ("B", "-6.000", None),
        ("C", "-24.000", "1.000"),
        ("D", "-18.000", None),
    ]
    _invariant(owner_db, world, 0)


def test_whole_quantities_required_for_integer_article(
    world: World, lot_tracking_open: None
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    response = _exit(world, [(0, "2", [(_lot_id(world, "A"), "1.5")])])
    assert _code(response) == (422, "quantity_not_whole")


def test_decimal_article_keeps_fractional_packaging(
    world: World, lot_tracking_open: None, owner_db: Session
) -> None:
    """Article décimal : une quantité exacte à 3 décimales reste représentable (1,25 carton)."""
    sh.allow_decimals(world, 0)
    _track(world, 0)
    carton = _packaging(world, 0, "Carton 24", "24")
    _stock(world, 0, [("A", "30", _iso(10)), ("B", "30", _iso(20))])
    sale, response = _sell(world, [(0, "2")], packaging=carton)
    _ok(response)
    assert _packaged_moves(owner_db, sale["id"]) == [
        ("A", "-30.000", "1.250"),
        ("B", "-18.000", "0.750"),
    ]


def test_no_false_rounding(world: World, lot_tracking_open: None, owner_db: Session) -> None:
    """Pack de 3 : 6 packs = 18 sur A 10 / B 10 — 10 et 8 bouteilles exactes, aucune quantité
    de pack arrondie (3,333…)."""
    sh.allow_decimals(world, 0)
    _track(world, 0)
    pack = _packaging(world, 0, "Pack 3", "3")
    _stock(world, 0, [("A", "10", _iso(10)), ("B", "10", _iso(20))])
    sale, response = _sell(world, [(0, "6")], packaging=pack)
    _ok(response)
    assert _packaged_moves(owner_db, sale["id"]) == [("A", "-10.000", None), ("B", "-8.000", None)]
    # Unité : la règle de représentation elle-même.
    snapshot = PackagingSnapshot(uuid.uuid4(), "Carton 24", Decimal("24"), Decimal("2"))
    assert split_packaging(snapshot, Decimal("48"), False) == snapshot
    assert split_packaging(snapshot, Decimal("24"), False).quantity == Decimal("1.000")
    assert split_packaging(snapshot, Decimal("30"), False) is None
    assert split_packaging(snapshot, Decimal("30"), True).quantity == Decimal("1.250")
    assert split_packaging(snapshot, Decimal("0.001"), True) is None


# --- 29-32. Sécurité ------------------------------------------------------------------------------


def test_tenant_isolation(
    world: World,
    lot_tracking_open: None,
    provision: Any,
    api_for: Any,
    db: Session,
    owner_db: Session,
) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    lot = _lot_id(world, "A")
    draft = _ok(_exit(world, [(0, "5", [(lot, "5")])]), 201)
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    # Article et lot d'un autre tenant : introuvables.
    assert _code(
        other.get(
            f"/sales/articles/{world.articles[0]}/lots", params={"site_id": str(beta.site_id)}
        )
    ) == (404, "article_not_found")
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
    assert _ok(other.patch(f"/catalog/articles/{article['id']}", json={"lot_tracked": True}))[
        "lot_tracked"
    ]
    reasons = {
        r["code"] or r["label"]: r["id"] for r in _ok(other.get("/stock/exit-reasons"))["items"]
    }
    response = other.post(
        "/stock/exits",
        json={
            "site_id": str(beta.site_id),
            "reason_id": reasons["PERTE"],
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
    # RLS : les choix de lots d'alpha sont invisibles et non insérables pour beta.
    set_db_context(db, tenant_id=beta.tenant_id)
    assert db.execute(text("SELECT count(*) FROM stock_exit_line_lots")).scalar_one() == 0
    alpha = owner_db.execute(
        text("SELECT tenant_id FROM stock_exits WHERE id = :id"), {"id": draft["id"]}
    ).scalar_one()
    line = draft["lines"][0]["id"]
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO stock_exit_line_lots (id, tenant_id, exit_line_id, article_id, lot_id,"
                " position, quantity) VALUES (gen_random_uuid(), :t, :l, :a, :lot, 9, 1)"
            ),
            {"t": alpha, "l": line, "a": world.articles[0], "lot": lot},
        )
    db.rollback()
    # Rôle applicatif : FORCE RLS, aucun BYPASSRLS ; FK composite : lot d'un autre article refusé.
    owner_db.expire_all()
    forced = owner_db.execute(
        text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'stock_exit_line_lots'")
    ).scalar_one()
    assert forced is True
    assert (
        owner_db.execute(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'stockmanager_app'")
        ).scalar_one()
        is False
    )


def test_composite_fk_lot_of_line_article_only(
    world: World, lot_tracking_open: None, db: Session, owner_db: Session
) -> None:
    _track(world, 0)
    _track(world, 1)
    _stock(world, 0, [("A", "10", _iso(30))])
    _stock(world, 1, [("X", "10", _iso(30))])
    draft = _ok(_exit(world, [(0, "5", [(_lot_id(world, "A"), "5")])]), 201)
    tenant = owner_db.execute(
        text("SELECT tenant_id FROM stock_exits WHERE id = :id"), {"id": draft["id"]}
    ).scalar_one()
    set_db_context(db, tenant_id=tenant)
    for article, lot in (
        (world.articles[1], _lot_id(world, "X")),  # article ≠ article de la ligne
        (world.articles[0], _lot_id(world, "X")),  # lot d'un autre article
    ):
        with pytest.raises(DBAPIError, match="foreign key"):
            db.execute(
                text(
                    "INSERT INTO stock_exit_line_lots (id, tenant_id, exit_line_id, article_id,"
                    " lot_id, position, quantity)"
                    " VALUES (gen_random_uuid(), :t, :l, :a, :lot, 9, 1)"
                ),
                {"t": tenant, "l": draft["lines"][0]["id"], "a": article, "lot": lot},
            )
        db.rollback()
        set_db_context(db, tenant_id=tenant)


def test_site_isolation(world: World, lot_tracking_open: None, client: TestClient) -> None:
    _track(world, 0)
    _stock(world, 0, [("DEPOT", "10", _iso(30))], site=world.site2)
    lot = _lot_id(world, "DEPOT")
    # Le FEFO du site de vente n'utilise jamais un lot d'un autre site.
    _, response = _sell(world, [(0, "1")])
    assert _code(response) == (422, "insufficient_stock")
    assert (
        _ok(
            world.owner.get(
                f"/sales/articles/{world.articles[0]}/lots", params={"site_id": world.site}
            )
        )["lots"]
        == []
    )
    # Choix d'un lot sans solde sur le site de la sortie : refusé à la validation.
    draft = _ok(_exit(world, [(0, "1", [(lot, "1")])]), 201)
    assert _code(world.owner.post(f"/stock/exits/{draft['id']}/validate"))[1] in {
        "insufficient_stock",
        "lot_not_available",
    }
    # Membre limité au site principal : lots disponibles du dépôt refusés.
    local = _member(
        world,
        client,
        "local@alpha.example.com",
        ["stock.exit.view", "stock.exit.create", "sales.sale.validate"],
        all_sites=False,
        site_ids=[world.site],
    )
    assert _code(
        local.get(
            "/stock/available-lots",
            params={"article_id": world.articles[0], "site_id": world.site2},
        )
    ) == (403, "site_access_denied")
    assert (
        _ok(
            local.get(
                "/stock/available-lots",
                params={"article_id": world.articles[0], "site_id": world.site},
            )
        )["lots"]
        == []
    )


def test_no_cost_leak(world: World, lot_tracking_open: None, client: TestClient) -> None:
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30)), ("B", "10", _iso(60))])
    a, b = _lot_id(world, "A"), _lot_id(world, "B")
    document = _ok(_exit(world, [(0, "12", [(a, "10"), (b, "2")])]), 201)
    _ok(world.owner.post(f"/stock/exits/{document['id']}/validate"))
    viewer = _member(
        world,
        client,
        "sanscout@alpha.example.com",
        ["stock.exit.view", "stock.exit.create", "sales.sale.view", "sales.sale.validate"],
    )
    line = _ok(viewer.get(f"/stock/exits/{document['id']}"))["lines"][0]
    assert [lot["lot_number"] for lot in line["lots"]] == ["A", "B"]
    assert "unit_cost" not in line and "amount" not in line
    assert not {"unit_cost", "amount", "average_cost"} & set(line["lots"][0])
    for path, params in (
        ("/stock/available-lots", {"article_id": world.articles[0]}),
        (f"/sales/articles/{world.articles[0]}/lots", {}),
    ):
        lots = _ok(viewer.get(path, params={"site_id": world.site, **params}))["lots"]
        assert lots and not {"unit_cost", "average_cost", "cost", "value"} & set(lots[0])


# --- Divers ---------------------------------------------------------------------------------------


def test_p1b_still_closed_in_production(world: World) -> None:
    assert lot_tracking.LOT_TRACKING_AVAILABLE is False
    assert _code(
        world.owner.patch(f"/catalog/articles/{world.articles[0]}", json={"lot_tracked": True})
    ) == (422, "lot_tracking_unavailable")


def test_movement_uniqueness_includes_lot(
    world: World, lot_tracking_open: None, db: Session, owner_db: Session
) -> None:
    """Une ligne peut produire un mouvement par lot, jamais deux fois le même (ligne, type,
    site, lot) ; sans lot, toujours un seul par ligne (``NULLS NOT DISTINCT``)."""
    _track(world, 0)
    _stock(world, 0, [("A", "10", _iso(30))])
    sale, response = _sell(world, [(0, "1")])
    _ok(response)
    owner_db.expire_all()
    original = owner_db.execute(
        text(
            "SELECT tenant_id, site_id, article_id, lot_id, source_line_id FROM stock_movements "
            "WHERE source_id = :id"
        ),
        {"id": sale["id"]},
    ).one()
    insert = text(
        "INSERT INTO stock_movements (id, tenant_id, site_id, article_id, movement_type, quantity,"
        " quantity_before, quantity_after, average_cost_before, average_cost_after, source_type,"
        " source_id, source_line_id, lot_id, occurred_at) VALUES (gen_random_uuid(), :t, :s, :a,"
        " 'SALE', -1, 9, 8, 0, 0, 'sale', :src, :line, :lot, now())"
    )
    set_db_context(db, tenant_id=original[0])
    with pytest.raises(DBAPIError, match="uq_stock_movements_line_type_site_lot"):
        db.execute(
            insert,
            {
                "t": original[0],
                "s": original[1],
                "a": original[2],
                "lot": original[3],
                "src": sale["id"],
                "line": original[4],
            },
        )
    db.rollback()
    # Sans lot : la deuxième ligne identique est refusée aussi (NULLS NOT DISTINCT).
    sh.validated_entry(world, [(1, "5", "10")])
    plain, response = _sell(world, [(1, "1")])
    _ok(response)
    owner_db.expire_all()
    row = owner_db.execute(
        text(
            "SELECT tenant_id, site_id, article_id, source_line_id FROM stock_movements "
            "WHERE source_id = :id"
        ),
        {"id": plain["id"]},
    ).one()
    set_db_context(db, tenant_id=row[0])
    with pytest.raises(DBAPIError, match="uq_stock_movements_line_type_site_lot"):
        db.execute(
            insert,
            {
                "t": row[0],
                "s": row[1],
                "a": row[2],
                "lot": None,
                "src": plain["id"],
                "line": row[3],
            },
        )
    db.rollback()


def test_engine_uses_the_tenant_day_given_by_the_caller() -> None:
    """Aucune date « serveur » dans le moteur : la date du jour (péremption) est fournie par
    l'appelant, calculée dans le fuseau du tenant (``tenant_today``, ADR-0028)."""
    import inspect

    from app.modules.stock import stock_service

    source = inspect.getsource(stock_service.StockService.consume)
    assert "today" in source and "date.today" not in source and "utcnow" not in source
