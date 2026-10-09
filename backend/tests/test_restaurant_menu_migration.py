"""Palier R1 — migration 0041 (menu des sites de restauration, ADR-0049).

Livraison du module : ses activations héritées de la période « Bientôt disponible » (défaut du
profil, inertes) sont remises à ``false`` sur les sites EXISTANTS (D10) ; les autres
activations, dont celles des modules encore planifiés, ne sont pas touchées. Tables avec RLS
forcée, unicité ``NULLS NOT DISTINCT`` ; retour arrière refusé s'il perdait un menu saisi."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.conftest import OWNER_URL


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


def _activations(conn: Any, site: uuid.UUID) -> dict[str, bool]:
    rows = conn.execute(
        text("SELECT module_code, enabled FROM site_modules WHERE site_id = :s"), {"s": site}
    ).all()
    return {code: bool(enabled) for code, enabled in rows}


def test_migration_0041_disables_the_menu_on_existing_sites_only(
    provision: Any, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    site = provision("menu-mig", profile="restaurant.maquis", plan="ENTREPRISE").site_id
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0040")
    with owner_engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name LIKE 'restaurant_menu_%'"
            )
        ).scalar_one()
        assert tables == 0
        # État d'un site créé pendant la période « Bientôt disponible » : menu, commandes et
        # recettes activés par défaut (inertes).
        before = _activations(conn, site)
        assert before["restaurant.menu"] and before["restaurant.orders"]
    owner_engine.dispose()
    command.upgrade(_alembic(), "head")
    with owner_engine.begin() as conn:
        after = _activations(conn, site)
        assert after["restaurant.menu"] is False
        # Seul le module livré est remis à ``false`` ; rien d'autre ne change.
        assert {k: v for k, v in after.items() if k != "restaurant.menu"} == {
            k: v for k, v in before.items() if k != "restaurant.menu"
        }
        for table in ("restaurant_menu_sections", "restaurant_menu_items"):
            flags = conn.execute(
                text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
                {"t": table},
            ).one()
            assert tuple(flags) == (True, True)
        assert (
            conn.execute(
                text(
                    "SELECT i.indnullsnotdistinct FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = 'uq_restaurant_menu_items_site_presentation'"
                )
            ).scalar_one()
            is True
        )


def test_migration_0041_downgrade_refuses_to_lose_a_menu(
    provision: Any, api_for: Any, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    t = provision("menu-mig-data", profile="restaurant.maquis", plan="ENTREPRISE")
    owner = api_for("owner@menu-mig-data.example.com")
    created = owner.post(
        "/restaurant/menu/sections", json={"site_id": str(t.site_id), "name": "Boissons"}
    )
    assert created.status_code == 201, created.text
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="menu saisis"):
        command.downgrade(_alembic(), "0040")
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE restaurant_menu_sections NO FORCE ROW LEVEL SECURITY"))
        kept = conn.execute(text("SELECT count(*) FROM restaurant_menu_sections")).scalar_one()
        conn.execute(text("ALTER TABLE restaurant_menu_sections FORCE ROW LEVEL SECURITY"))
        assert kept == 1
