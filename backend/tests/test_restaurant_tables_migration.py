"""Palier R5-A — migration 0046 (zones et tables des sites de restauration, ADR-0049).

- ``restaurant.tables`` remis à ``false`` sur les sites EXISTANTS seulement (A11) ; les autres
  activations, dont celles des commandes, ne sont pas touchées (politique E.1 conservée) ;
- tables avec RLS forcée, clé étrangère et contrainte des commandes, type d'évènement
  ``TABLE_CHANGED`` ;
- montée refusée si une commande portait déjà un ``table_id`` ; descente refusée si elle perdait
  une zone ou une table ; montée → descente → montée.
"""

import uuid
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.test_restaurant_menu_migration import _activations, _alembic, at_head  # noqa: F401
from tests.test_restaurant_orders import _api, _line, _menu, _ok, _order

MODULE = "restaurant.tables"


def _flags(conn: Any, table: str) -> tuple[bool, bool]:
    row = conn.execute(
        text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
        {"t": table},
    ).one()
    return bool(row[0]), bool(row[1])


def test_migration_0046_disables_tables_on_existing_sites_only(
    provision: Any,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    site = provision("rt-mig", profile="restaurant.restaurant", plan="ENTREPRISE").site_id
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0045")
    with owner_engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('restaurant_zones', 'restaurant_tables')"
            )
        ).scalar_one()
        assert tables == 0
        # Site créé pendant la période « Bientôt disponible » : tables activées par défaut
        # (profil Restaurant, D11), inertes.
        before = _activations(conn, site)
        assert before[MODULE] is True and before["restaurant.orders"] is True
    owner_engine.dispose()
    command.upgrade(_alembic(), "0046")
    with owner_engine.begin() as conn:
        after = _activations(conn, site)
        assert after[MODULE] is False
        # Seul ``restaurant.tables`` est remis à ``false`` ; rien d'autre ne change.
        assert {k: v for k, v in after.items() if k != MODULE} == {
            k: v for k, v in before.items() if k != MODULE
        }
        assert _flags(conn, "restaurant_zones") == (True, True)
        assert _flags(conn, "restaurant_tables") == (True, True)
        constraints = set(
            conn.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'restaurant_orders'::regclass"
                )
            ).scalars()
        )
        assert "ck_restaurant_orders_table_only_on_site" in constraints
        assert any(
            c.startswith("fk_restaurant_orders_tenant_id_site_id_table_id") for c in constraints
        )
        event_check = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_restaurant_order_events_restaurant_order_event_type'"
            )
        ).scalar_one()
        assert "TABLE_CHANGED" in event_check and "CUSTOMER_SET" in event_check


def test_migration_0046_refuses_orders_already_carrying_a_table(
    provision: Any,
    oclient: TestClient,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    t = provision("rt-mig-order", profile="restaurant.maquis", plan="ENTREPRISE")
    owner = _api(oclient, "owner@rt-mig-order.example.com")
    site = str(t.site_id)
    menu = _menu(owner, site, "MIG")
    order = _ok(_order(owner, site, [_line(menu.unit)]), 201)["id"]
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0045")
    with owner_engine.begin() as conn:
        # Avant 0046 : aucune clé étrangère, une valeur quelconque était possible en base.
        conn.execute(
            text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"),
            {"t": uuid.uuid4(), "o": order},
        )
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="portent déjà une table"):
        command.upgrade(_alembic(), "0046")
    with owner_engine.begin() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'restaurant_tables'"
                )
            ).scalar_one()
            == 0
        )
        conn.execute(
            text("UPDATE restaurant_orders SET table_id = NULL WHERE id = :o"), {"o": order}
        )


def test_migration_0046_downgrade_refuses_to_lose_tables(
    provision: Any,
    tclient: TestClient,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    t = provision("rt-mig-data", profile="restaurant.restaurant", plan="ENTREPRISE")
    owner = _api(tclient, "owner@rt-mig-data.example.com")
    created = owner.post(
        "/restaurant/tables/zones", json={"site_id": str(t.site_id), "name": "Salle"}
    )
    assert created.status_code == 201, created.text
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="seraient perdus"):
        command.downgrade(_alembic(), "0045")
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE restaurant_zones NO FORCE ROW LEVEL SECURITY"))
        kept = conn.execute(text("SELECT count(*) FROM restaurant_zones")).scalar_one()
        conn.execute(text("ALTER TABLE restaurant_zones FORCE ROW LEVEL SECURITY"))
        assert kept == 1
