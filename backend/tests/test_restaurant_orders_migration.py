"""Palier R2-B — migration 0042 (commandes de restauration, ADR-0049 D14).

- Montée : activations héritées de ``restaurant.orders`` remises à ``false`` sur les sites
  EXISTANTS (D10), rien d'autre ; tables à RLS forcée ; déclencheur d'états finaux ; colonne
  ``business_profiles.module_settings``.
- Descente refusée dès qu'une commande OU une ligne de réglages existe (Q7, N5), y compris des
  réglages conservés après une désactivation ; permise sans l'un ni l'autre.
"""

import uuid
from collections.abc import Iterator
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
from tests.conftest import OWNER_URL, set_site_module
from tests.test_restaurant_orders import _api, _line, _menu, _ok, _order

ORDERS = "restaurant.orders"
MAQUIS = "restaurant.maquis"


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
        # La colonne ``module_settings`` a pu être recréée vide : catalogue resynchronisé.
        with Session(owner_engine) as session:
            sync_catalog(session, load_catalog(get_registry()))
            session.commit()


def _activations(conn: Any, site: uuid.UUID) -> dict[str, bool]:
    rows = conn.execute(
        text("SELECT module_code, enabled FROM site_modules WHERE site_id = :s"), {"s": site}
    ).all()
    return {code: bool(enabled) for code, enabled in rows}


def _count(conn: Any, table: str) -> int:
    conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    count = int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
    conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    return count


def test_migration_0042_disables_orders_on_existing_sites_only(
    provision: Any, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    # Registre de production : commandes planifiées, activées par défaut (inertes), aucun
    # réglage créé (``site_setup`` jamais appelé pour un module planifié).
    site = provision("ro-mig", profile=MAQUIS, plan="ENTREPRISE").site_id
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0041")  # aucune commande ni aucun réglage : permise
    with owner_engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('restaurant_orders', 'restaurant_order_lines', "
                "'restaurant_order_events', 'restaurant_site_settings')"
            )
        ).scalar_one()
        assert tables == 0
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'business_profiles' AND column_name = 'module_settings'"
                )
            ).scalar_one()
            == 0
        )
        before = _activations(conn, site)
        assert before[ORDERS] is True
    owner_engine.dispose()
    command.upgrade(_alembic(), "head")
    with owner_engine.begin() as conn:
        after = _activations(conn, site)
        assert after[ORDERS] is False
        # Seul le module livré par la migration change ; rien d'autre.
        assert {k: v for k, v in after.items() if k != ORDERS} == {
            k: v for k, v in before.items() if k != ORDERS
        }
        triggers = set(
            conn.execute(
                text(
                    "SELECT event_object_table FROM information_schema.triggers "
                    "WHERE trigger_name = 'trg_restaurant_final_state'"
                )
            ).scalars()
        )
        assert triggers == {"restaurant_orders", "restaurant_order_lines"}


def test_migration_0042_downgrade_refuses_to_lose_an_order(
    provision: Any, oclient: TestClient, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    t = provision("ro-mig-order", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-mig-order.example.com")
    menu = _menu(owner, str(t.site_id), "M")
    _ok(_order(owner, str(t.site_id), [_line(menu.unit)]), 201)
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="1 commande"):
        command.downgrade(_alembic(), "0041")
    with owner_engine.begin() as conn:
        assert _count(conn, "restaurant_orders") == 1
        assert _count(conn, "restaurant_order_lines") == 1


def test_migration_0042_downgrade_refuses_to_lose_kept_settings(
    provision: Any, oclient: TestClient, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    # Réglages seuls, conservés après une désactivation du module (N5).
    t = provision("ro-mig-settings", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-mig-settings.example.com")
    assert set_site_module(owner, t.site_id, ORDERS, False).status_code == 204
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="0 commande\\(s\\) et 1 réglage"):
        command.downgrade(_alembic(), "0041")
    with owner_engine.begin() as conn:
        assert _count(conn, "restaurant_site_settings") == 1
        assert _count(conn, "restaurant_orders") == 0
