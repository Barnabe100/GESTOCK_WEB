"""Palier R7-F (ADR-0052) — livraison de ``restaurant.qr`` : registre de production, offres
STANDARD et ENTREPRISE (Q1), règles de licence existantes (modules figés à l'émission ; un site
STANDARD déjà licencié l'obtient à sa licence SUIVANTE, à sa date), migration 0058."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import create_session_factory, set_db_context
from app.main import create_app
from app.platform.capabilities.service import CapabilityService
from app.platform.catalog.loader import load_catalog
from app.platform.registry import ModuleStatus, get_registry
from app.platform.tenancy.models import Tenant
from tests.conftest import Api, set_site_module
from tests.test_licenses import (  # noqa: F401  (fixtures)
    _confirmed_payment,
    _generate,
    admin,
    signing,
)
from tests.test_restaurant_menu_migration import _activations, _alembic, at_head  # noqa: F401
from tests.test_restaurant_orders import _api, _code, _ok
from tests.test_restaurant_qr_points import POINTS

MODULE = "restaurant.qr"
PUBLIC_MENU = "/api/v1/public/restaurant/qr/menu"


def test_production_registry_delivers_qr(
    provision: Any, settings: Settings, migrated: None
) -> None:
    assert get_registry().get(MODULE).status is ModuleStatus.AVAILABLE
    with TestClient(create_app(settings)) as client:
        t = provision("rqd-prod", profile="restaurant.restaurant", plan="ENTREPRISE")
        owner = _api(client, "owner@rqd-prod.example.com")
        # Facultatif pour ce profil : rien n'est activé sans décision explicite.
        selected = Api(owner.client, owner.token, t.site_id)
        assert _code(selected.get(POINTS)) == (403, "module_unavailable")
        assert set_site_module(owner, t.site_id, MODULE, True).status_code == 204
        _ok(selected.get(POINTS))
        # Routes publiques montées : jeton inconnu → refus générique (et non 404 de route).
        response = client.get(PUBLIC_MENU, headers={"X-QR-Token": "a" * 43})
        assert _code(response) == (404, "qr_unavailable")


def test_both_plans_include_qr_and_nothing_else_changed(owner_db: Session) -> None:
    catalog = load_catalog(get_registry())
    plans = catalog.plans
    assert MODULE in plans["STANDARD"].modules and MODULE in plans["ENTREPRISE"].modules
    in_db = {
        plan: set(
            owner_db.execute(
                text("SELECT module_code FROM plan_modules WHERE plan_code = :p"), {"p": plan}
            ).scalars()
        )
        for plan in ("STANDARD", "ENTREPRISE")
    }
    assert MODULE in in_db["STANDARD"] and MODULE in in_db["ENTREPRISE"]
    # Seul ``restaurant.qr`` a été ajouté à STANDARD : ses autres modules, limites et
    # fonctionnalités sont ceux d'avant R7.
    assert in_db["STANDARD"] - {MODULE} == {
        "catalog",
        "suppliers",
        "customers",
        "stock",
        "inventory_count",
        "sales",
        "receivables",
        "payments",
        "cash_register",
        "pos",
        "reports",
        "alerts",
        "restaurant.menu",
        "restaurant.tables",
        "restaurant.orders",
        "restaurant.kitchen",
        "restaurant.recipes",
    }
    assert plans["STANDARD"].features == ()
    assert dict(plans["STANDARD"].limits) == {"max_sites": 1, "max_users": 5}
    assert list(plans["ENTREPRISE"].features) == ["stock.transfers"]


@pytest.mark.parametrize("plan", ["STANDARD", "ENTREPRISE"])
def test_qr_is_available_on_unlicensed_and_licensed_sites_of_both_plans(
    plan: str,
    provision: Any,
    qrclient: TestClient,
    admin: TestClient,  # noqa: F811
    signing: Any,  # noqa: F811
) -> None:
    slug = f"rqd-{plan.lower()}"
    t = provision(slug, profile="restaurant.restaurant", plan=plan)
    owner = _api(qrclient, f"owner@{slug}.example.com")
    assert set_site_module(owner, t.site_id, MODULE, True).status_code == 204
    caps = Api(owner.client, owner.token, t.site_id).get("/me/capabilities").json()
    assert MODULE in {m["code"] for m in caps["modules"]}
    licence = _ok(_generate(admin, _confirmed_payment(owner, admin, t.subscription_id)), 201)
    assert MODULE in licence["modules"]  # licence éligible : le module y est figé
    caps = Api(owner.client, owner.token, t.site_id).get("/me/capabilities").json()
    assert MODULE in {m["code"] for m in caps["modules"]}
    assert "restaurant.qr.point.manage" in caps["permissions"]


@pytest.fixture
def standard_without_qr(owner_db: Session) -> Iterator[None]:
    """Offre STANDARD d'AVANT R7 (sans ``restaurant.qr``) le temps d'émettre une licence."""
    owner_db.execute(
        text("DELETE FROM plan_modules WHERE plan_code = 'STANDARD' AND module_code = :m"),
        {"m": MODULE},
    )
    owner_db.commit()
    try:
        yield
    finally:
        owner_db.execute(
            text(
                "INSERT INTO plan_modules (plan_code, module_code) VALUES ('STANDARD', :m) "
                "ON CONFLICT DO NOTHING"
            ),
            {"m": MODULE},
        )
        owner_db.commit()


def test_licensed_standard_site_gets_qr_only_from_its_next_licence(
    provision: Any,
    qrclient: TestClient,
    admin: TestClient,  # noqa: F811
    signing: Any,  # noqa: F811
    owner_db: Session,
    app_engine: Engine,
    standard_without_qr: None,
) -> None:
    t = provision("rqd-renew", profile="restaurant.restaurant", plan="STANDARD")
    owner = _api(qrclient, "owner@rqd-renew.example.com")
    first = _ok(_generate(admin, _confirmed_payment(owner, admin, t.subscription_id)), 201)
    assert MODULE not in first["modules"]
    # Offre STANDARD complétée (R7) : la licence EN VIGUEUR fait foi jusqu'à son terme.
    owner_db.execute(
        text("INSERT INTO plan_modules (plan_code, module_code) VALUES ('STANDARD', :m)"),
        {"m": MODULE},
    )
    owner_db.commit()
    assert _code(set_site_module(owner, t.site_id, MODULE, True))[0] in (409, 422)
    caps = Api(owner.client, owner.token, t.site_id).get("/me/capabilities").json()
    assert MODULE not in {m["code"] for m in caps["modules"]}
    # Renouvellement : la licence suivante contient le module, à SA date de début.
    second = _ok(_generate(admin, _confirmed_payment(owner, admin, t.subscription_id, "V2")), 201)
    assert MODULE in second["modules"] and second["state"] == "NOT_YET_VALID"
    start = date.fromisoformat(second["valid_from"])
    assert start == date.fromisoformat(first["valid_until"]) + timedelta(days=1)
    registry = get_registry()
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=t.tenant_id)
        tenant = db.get(Tenant, t.tenant_id)
        assert tenant is not None
        service = CapabilityService(db, registry)
        before = datetime.combine(start - timedelta(days=1), time(12), tzinfo=UTC)
        after = datetime.combine(start + timedelta(days=1), time(12), tzinfo=UTC)
        grant_before = service.grants(tenant, t.site_id, before)[0]
        grant_after = service.grants(tenant, t.site_id, after)[0]
        assert MODULE not in grant_before.terms.modules
        assert MODULE in grant_after.terms.modules
        db.rollback()


def test_migration_0058_disables_qr_on_existing_sites_only(
    provision: Any,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    site = provision("rqd-mig", profile="restaurant.restaurant", plan="ENTREPRISE").site_id
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0057")
    with owner_engine.begin() as conn:
        # Activation inerte héritée de la période « Bientôt disponible » (cas construit).
        conn.execute(
            text("UPDATE site_modules SET enabled = true WHERE site_id = :s AND module_code = :m"),
            {"s": site, "m": MODULE},
        )
        before = _activations(conn, site)
        assert before[MODULE] is True
    owner_engine.dispose()
    command.upgrade(_alembic(), "0058")
    with owner_engine.begin() as conn:
        after = _activations(conn, site)
        assert after[MODULE] is False
        assert {k: v for k, v in after.items() if k != MODULE} == {
            k: v for k, v in before.items() if k != MODULE
        }
