"""Palier R2-D — migration 0043 (origine des ventes, ADR-0049 D14).

- Montée : colonnes d'origine nulles pour les ventes existantes, canal ``RESTAURANT`` accepté,
  index partiel et déclencheur d'immuabilité en place.
- Descente REFUSÉE dès qu'une vente issue d'une commande existe (Q7) ; permise sinon (ventes
  ordinaires conservées, contrainte du canal rétablie).
"""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.platform.catalog.loader import load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.registry import get_registry
from tests.conftest import OWNER_URL
from tests.test_restaurant_orders import MAQUIS, _api, _line, _menu, _ok, _order
from tests.test_restaurant_orders_settlement import _cash, _receive, _settle


def _alembic() -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = OWNER_URL
    return config


@pytest.fixture
def at_head(owner_engine: Engine) -> Iterator[None]:
    try:
        yield
    finally:
        owner_engine.dispose()
        command.upgrade(_alembic(), "head")
        with Session(owner_engine) as session:
            sync_catalog(session, load_catalog(get_registry()))
            session.commit()


def _world(provision: Any, oclient: TestClient, slug: str) -> Any:
    t = provision(slug, profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, f"owner@{slug}.example.com")
    site = str(t.site_id)
    supplier = _ok(owner.post("/suppliers", json={"name": "Brasserie"}), 201)["id"]
    r = SimpleNamespace(site=site, owner=owner, supplier=supplier, menu=_menu(owner, site, "M"))
    _receive(r, r.menu.article, "10")
    return r


def _columns(conn: Any) -> set[str]:
    return set(
        conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'sales' AND column_name LIKE 'origin_%'"
            )
        ).scalars()
    )


def test_migration_0043_downgrade_refuses_to_lose_a_restaurant_sale(
    provision: Any, oclient: TestClient, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    r = _world(provision, oclient, "ro-mig-sale")
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    settled = _ok(_settle(r.owner, order["id"], payments=_cash("700")), 201)
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="1 vente"):
        command.downgrade(_alembic(), "0042")
    with owner_engine.begin() as conn:
        assert _columns(conn) == {"origin_type", "origin_id"}
        origin = conn.execute(
            text("SELECT origin_type, origin_id::text FROM sales WHERE id = :i"),
            {"i": settled["sale_id"]},
        ).one()
        assert tuple(origin) == ("restaurant_order", order["id"])


def test_migration_0043_downgrade_keeps_ordinary_sales(
    provision: Any, oclient: TestClient, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    r = _world(provision, oclient, "ro-mig-pos")
    pos = _ok(
        r.owner.post(
            "/pos/checkout",
            json={
                "site_id": r.site,
                "lines": [{"article_id": r.menu.article, "quantity": "1"}],
                "payments": _cash("700"),
                "idempotency_key": str(uuid.uuid4()),
            },
        ),
        201,
    )
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0042")
    with owner_engine.begin() as conn:
        assert _columns(conn) == set()
        assert (
            conn.execute(
                text("SELECT channel FROM sales WHERE id = :i"), {"i": pos["sale"]["id"]}
            ).scalar_one()
            == "POS"
        )
        check = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_sales_sale_channel'"
            )
        ).scalar_one()
        assert "RESTAURANT" not in check
        assert (
            conn.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = 'sales_origin_immutable'")
            ).scalar_one()
            == 0
        )
    owner_engine.dispose()
    command.upgrade(_alembic(), "head")
    with owner_engine.begin() as conn:
        assert _columns(conn) == {"origin_type", "origin_id"}
        assert (
            conn.execute(
                text("SELECT origin_id FROM sales WHERE id = :i"), {"i": pos["sale"]["id"]}
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_sales_active_origin'")
            ).scalar_one()
            == 1
        )
