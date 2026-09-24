"""Moteur de stock (STK-01 à STK-09) : calcul du CMUP, non-négativité, verrouillage en
concurrence, immutabilité du journal, numérotation."""

import threading
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.core.errors import BusinessRuleError
from app.modules.stock.models import MovementType, StockLevel, StockMovement
from app.modules.stock.stock_service import (
    MovementRequest,
    StockService,
    compute_average_cost,
)
from app.platform.sequences.service import next_number
from app.shared.clock import utcnow
from tests.conftest import Api

D = Decimal


# --- Formule du CMUP ----------------------------------------------------------------------------


def test_average_cost_formula_with_four_decimals() -> None:
    # Stock nul : le CMUP vaut le coût d'entrée.
    assert compute_average_cost(D("0"), D("0"), D("10"), D("1500")) == D("1500.0000")
    # ((10 × 1500) + (5 × 1800)) / 15 = 1600
    assert compute_average_cost(D("10"), D("1500"), D("5"), D("1800")) == D("1600.0000")
    # 4 décimales, demi supérieur : (1 × 1 + 2 × 2) / 3 = 1.66666… → 1.6667
    assert compute_average_cost(D("1"), D("1"), D("2"), D("2")) == D("1.6667")
    with pytest.raises(ValueError):
        compute_average_cost(D("0"), D("0"), D("0"), D("1"))


def test_no_rounding_drift_over_many_entries() -> None:
    """Le Desktop arrondissait à 2 décimales à chaque entrée ; 4 décimales limitent la dérive."""
    quantity, cost = D("0"), D("0")
    for _ in range(100):
        cost = compute_average_cost(quantity, cost, D("3"), D("10.01"))
        quantity += 3
    assert cost == D("10.0100")


# --- Accès base : fixtures ----------------------------------------------------------------------


@pytest.fixture
def setup(provision: Any, api_for: Any) -> dict[str, Any]:
    t = provision("alpha", plan="ENTREPRISE")
    api: Api = api_for("owner@alpha.example.com")
    category = api.post("/catalog/categories", json={"name": "Divers"}).json()
    articles = [
        api.post(
            "/catalog/articles",
            json={
                "reference": f"A-{i}",
                "designation": f"Article {i}",
                "category_id": category["id"],
                "unit": "u",
                "purchase_price": "100",
                "sale_price": "150",
            },
        ).json()["id"]
        for i in range(2)
    ]
    return {
        "tenant_id": t.tenant_id,
        "site_id": t.site_id,
        "user_id": t.owner_user_id,
        "articles": [uuid.UUID(a) for a in articles],
    }


def _session(app_engine: Engine, setup: dict[str, Any]) -> Session:
    session = create_session_factory(app_engine)()
    set_db_context(session, tenant_id=setup["tenant_id"], user_id=setup["user_id"])
    return session


def _service(session: Session, setup: dict[str, Any]) -> StockService:
    return StockService(session, setup["tenant_id"], setup["user_id"], utcnow())


def _request(
    article_id: uuid.UUID, movement_type: MovementType, quantity: str, cost: str | None = None
) -> MovementRequest:
    return MovementRequest(
        article_id=article_id,
        movement_type=movement_type,
        quantity=D(quantity),
        unit_cost=D(cost) if cost else None,
        source_type="test",
        source_id=uuid.uuid4(),
        source_line_id=uuid.uuid4(),
    )


def _level(session: Session, setup: dict[str, Any], article_id: uuid.UUID) -> StockLevel:
    session.expire_all()
    return session.scalars(
        select(StockLevel).where(
            StockLevel.site_id == setup["site_id"], StockLevel.article_id == article_id
        )
    ).one()


# --- Application des mouvements -----------------------------------------------------------------


def test_entry_and_exit_update_stock_and_cost(app_engine: Engine, setup: dict[str, Any]) -> None:
    a = setup["articles"][0]
    with _session(app_engine, setup) as db:
        service = _service(db, setup)
        service.apply(setup["site_id"], [_request(a, MovementType.ENTRY, "10", "1500")])
        service.apply(setup["site_id"], [_request(a, MovementType.ENTRY, "5", "1800")])
        [exit_movement] = service.apply(setup["site_id"], [_request(a, MovementType.EXIT, "-4")])
        db.commit()

        level = _level(db, setup, a)
        assert (level.quantity, level.average_cost) == (D("11.000"), D("1600.0000"))
        # La sortie utilise le CMUP du site et ne le recalcule pas (Q1).
        assert exit_movement.unit_cost == D("1600.0000")
        assert exit_movement.average_cost_after == D("1600.0000")
        assert (exit_movement.quantity_before, exit_movement.quantity_after) == (D("15"), D("11"))


def test_negative_stock_refused_without_partial_write(
    app_engine: Engine, setup: dict[str, Any]
) -> None:
    a, b = setup["articles"]
    with _session(app_engine, setup) as db:
        _service(db, setup).apply(
            setup["site_id"],
            [
                _request(a, MovementType.ENTRY, "5", "100"),
                _request(b, MovementType.ENTRY, "1", "10"),
            ],
        )
        db.commit()
    with _session(app_engine, setup) as db:
        with pytest.raises(BusinessRuleError) as error:
            _service(db, setup).apply(
                setup["site_id"],
                [_request(a, MovementType.EXIT, "-2"), _request(b, MovementType.EXIT, "-3")],
            )
        assert error.value.code == "insufficient_stock"
        assert error.value.extra["articles"][0]["reference"] == "A-1"
        db.rollback()
    with _session(app_engine, setup) as db:
        assert _level(db, setup, a).quantity == D("5")  # la ligne valide n'a rien écrit non plus
        count = db.scalar(select(text("count(*)")).select_from(StockMovement))
        assert count == 2


def test_database_refuses_negative_stock_even_if_service_bypassed(
    app_engine: Engine, setup: dict[str, Any]
) -> None:
    with _session(app_engine, setup) as db:
        level = _service(db, setup).lock_levels(setup["site_id"], {setup["articles"][0]})
        level[setup["articles"][0]].quantity = D("-1")
        with pytest.raises(DBAPIError, match="quantity_non_negative"):
            db.flush()


def test_movements_are_immutable(app_engine: Engine, setup: dict[str, Any]) -> None:
    with _session(app_engine, setup) as db:
        _service(db, setup).apply(
            setup["site_id"], [_request(setup["articles"][0], MovementType.ENTRY, "1", "1")]
        )
        db.commit()
    for statement in ("UPDATE stock_movements SET quantity = 99", "DELETE FROM stock_movements"):
        with _session(app_engine, setup) as db:
            db.execute(text("SELECT 1"))
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(statement))


def test_same_source_line_cannot_be_applied_twice(
    app_engine: Engine, setup: dict[str, Any]
) -> None:
    request = _request(setup["articles"][0], MovementType.ENTRY, "1", "1")
    with _session(app_engine, setup) as db:
        _service(db, setup).apply(setup["site_id"], [request])
        db.commit()
    with (
        _session(app_engine, setup) as db,
        pytest.raises(DBAPIError, match="uq_stock_movements"),
    ):
        _service(db, setup).apply(setup["site_id"], [request])


# --- Concurrence ------------------------------------------------------------------------------


def _run_concurrently(workers: list[Callable[[], None]]) -> list[BaseException | None]:
    barrier = threading.Barrier(len(workers))
    results: list[BaseException | None] = [None] * len(workers)

    def run(index: int, work: Callable[[], None]) -> None:
        barrier.wait()
        try:
            work()
        except BaseException as exc:  # noqa: BLE001 - résultat collecté pour l'assertion
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i, w)) for i, w in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def test_concurrent_exits_never_oversell(app_engine: Engine, setup: dict[str, Any]) -> None:
    """Deux sorties simultanées de 4 sur un stock de 5 : l'une passe, l'autre est refusée."""
    a = setup["articles"][0]
    with _session(app_engine, setup) as db:
        _service(db, setup).apply(setup["site_id"], [_request(a, MovementType.ENTRY, "5", "10")])
        db.commit()

    def exit_four() -> None:
        with _session(app_engine, setup) as db:
            _service(db, setup).apply(setup["site_id"], [_request(a, MovementType.EXIT, "-4")])
            db.commit()

    results = _run_concurrently([exit_four, exit_four])
    failures = [r for r in results if r is not None]
    assert len(failures) == 1
    assert isinstance(failures[0], BusinessRuleError)
    with _session(app_engine, setup) as db:
        assert _level(db, setup, a).quantity == D("1")


def test_concurrent_entries_keep_cost_consistent(app_engine: Engine, setup: dict[str, Any]) -> None:
    a = setup["articles"][0]

    def entry(cost: str) -> Callable[[], None]:
        def work() -> None:
            with _session(app_engine, setup) as db:
                _service(db, setup).apply(
                    setup["site_id"], [_request(a, MovementType.ENTRY, "10", cost)]
                )
                db.commit()

        return work

    assert _run_concurrently([entry("100"), entry("200"), entry("300")]) == [None] * 3
    with _session(app_engine, setup) as db:
        level = _level(db, setup, a)
        assert (level.quantity, level.average_cost) == (D("30.000"), D("200.0000"))
        movements = db.scalars(select(StockMovement).order_by(StockMovement.occurred_at)).all()
        # Chaînage : chaque mouvement part du stock laissé par le précédent.
        befores = sorted(m.quantity_before for m in movements)
        assert befores == [D("0"), D("10"), D("20")]


def test_concurrent_numbering_has_no_duplicates(app_engine: Engine, setup: dict[str, Any]) -> None:
    numbers: list[str] = []
    lock = threading.Lock()

    def take() -> None:
        with _session(app_engine, setup) as db:
            number = next_number(db, setup["tenant_id"], "stock_entry", "ENT")
            db.commit()
            with lock:
                numbers.append(number)

    assert _run_concurrently([take] * 8) == [None] * 8
    assert sorted(numbers) == [f"ENT-{i:06d}" for i in range(1, 9)]


def test_rolled_back_number_is_reused(app_engine: Engine, setup: dict[str, Any]) -> None:
    with _session(app_engine, setup) as db:
        assert next_number(db, setup["tenant_id"], "stock_exit", "SOR") == "SOR-000001"
        db.rollback()
    with _session(app_engine, setup) as db:
        assert next_number(db, setup["tenant_id"], "stock_exit", "SOR") == "SOR-000001"
