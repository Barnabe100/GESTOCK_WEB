"""Migrations 0047 (libération, délai entre prises 5 ou 10 minutes) et 0048 (livraison des
commandes et des tables).

- 0047 : valeurs existantes du délai ramenées à 5 ou 10 sans jamais assouplir la règle (0 à 5 →
  5, au-delà → 10) ; contrainte ``claim_cooldown_choice`` ; type d'évènement ``RELEASED`` ;
  descente refusée si elle perdait une libération enregistrée.
- 0048 : activations héritées de ``restaurant.orders`` et ``restaurant.tables`` remises à
  ``false`` sur les sites EXISTANTS, toutes les autres activations inchangées ; données conservées.
"""

from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.test_restaurant_menu_migration import _activations, _alembic, at_head  # noqa: F401
from tests.test_restaurant_orders import _api, _line, _menu, _ok, _order

DELIVERED = ("restaurant.orders", "restaurant.tables")


def _cooldowns(conn: Any) -> dict[str, int]:
    conn.execute(text("ALTER TABLE restaurant_site_settings NO FORCE ROW LEVEL SECURITY"))
    rows = conn.execute(
        text("SELECT site_id::text, claim_cooldown_minutes FROM restaurant_site_settings")
    ).all()
    conn.execute(text("ALTER TABLE restaurant_site_settings FORCE ROW LEVEL SECURITY"))
    return {site: minutes for site, minutes in rows}


def _set_cooldown(conn: Any, site: str, minutes: int) -> None:
    conn.execute(text("ALTER TABLE restaurant_site_settings NO FORCE ROW LEVEL SECURITY"))
    conn.execute(
        text("UPDATE restaurant_site_settings SET claim_cooldown_minutes = :m WHERE site_id = :s"),
        {"m": minutes, "s": site},
    )
    conn.execute(text("ALTER TABLE restaurant_site_settings FORCE ROW LEVEL SECURITY"))


def test_migration_0047_maps_cooldowns_to_5_or_10_never_looser(
    provision: Any,
    oclient: TestClient,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    sites = []
    for slug in ("ro-cd-a", "ro-cd-b", "ro-cd-c", "ro-cd-d"):
        t = provision(slug, profile="restaurant.maquis", plan="ENTREPRISE")
        owner = _api(oclient, f"owner@{slug}.example.com")
        assert _ok(owner.get(f"/restaurant/settings/{t.site_id}"))["claim_cooldown_minutes"] == 5
        sites.append(str(t.site_id))
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0046")
    with owner_engine.begin() as conn:
        for site, minutes in zip(sites, (0, 3, 7, 30), strict=True):
            _set_cooldown(conn, site, minutes)
    owner_engine.dispose()
    command.upgrade(_alembic(), "0047")
    with owner_engine.begin() as conn:
        mapped = _cooldowns(conn)
        assert [mapped[s] for s in sites] == [5, 5, 10, 10]
        definition = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_restaurant_site_settings_claim_cooldown_choice'"
            )
        ).scalar_one()
        assert "5" in definition and "10" in definition
        events = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_restaurant_order_events_restaurant_order_event_type'"
            )
        ).scalar_one()
        assert "RELEASED" in events and "TABLE_CHANGED" in events
    # Toute autre valeur est refusée par la base, même hors de l'API.
    with pytest.raises(IntegrityError), owner_engine.begin() as conn:
        _set_cooldown(conn, sites[0], 0)


def test_migration_0047_downgrade_refuses_to_lose_a_release(
    provision: Any,
    oclient: TestClient,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    t = provision("ro-rel-mig", profile="restaurant.maquis", plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-rel-mig.example.com")
    site = str(t.site_id)
    order = _ok(_order(owner, site, [_line(_menu(owner, site, "REL").unit)]), 201)["id"]
    _ok(owner.post(f"/restaurant/orders/{order}/claim"))
    _ok(owner.post(f"/restaurant/orders/{order}/release", json={"reason": "Pause"}))
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="libération"):
        command.downgrade(_alembic(), "0046")
    with owner_engine.begin() as conn:
        conn.execute(text("ALTER TABLE restaurant_order_events NO FORCE ROW LEVEL SECURITY"))
        kept = conn.execute(
            text("SELECT count(*) FROM restaurant_order_events WHERE event_type = 'RELEASED'")
        ).scalar_one()
        conn.execute(text("ALTER TABLE restaurant_order_events FORCE ROW LEVEL SECURITY"))
        assert kept == 1


def test_migration_0048_disables_orders_and_tables_on_existing_sites_only(
    provision: Any,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    site = provision("ro-deliv", profile="restaurant.restaurant", plan="ENTREPRISE").site_id
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0047")
    with owner_engine.begin() as conn:
        # Période « Bientôt disponible » : activations héritées des défauts du profil, inertes.
        conn.execute(
            text(
                "UPDATE site_modules SET enabled = true "
                "WHERE site_id = :s AND module_code IN ('restaurant.orders', 'restaurant.tables')"
            ),
            {"s": site},
        )
        before = _activations(conn, site)
        assert all(before[code] for code in DELIVERED)
    owner_engine.dispose()
    command.upgrade(_alembic(), "0048")
    with owner_engine.begin() as conn:
        after = _activations(conn, site)
        assert [after[code] for code in DELIVERED] == [False, False]
        # Rien d'autre ne change (menu, ventes, modules encore planifiés…).
        assert {k: v for k, v in after.items() if k not in DELIVERED} == {
            k: v for k, v in before.items() if k not in DELIVERED
        }
