"""Palier R5-A (C3) — licences et ``restaurant.tables``.

Une licence fige à son émission les modules du plan (ADR-0034) : les plans STANDARD et
ENTREPRISE contiennent ``restaurant.tables`` depuis la Phase 1, toute licence émise le contient
donc (aucun renouvellement ni aucune réémission requis). Vérifié : une licence en vigueur qui le
contient le rend effectif sur son site ; une licence émise SANS lui (plan modifié le temps de
l'émission, cas construit) le rend indisponible, même si le plan le contient de nouveau.
Registre de test (``tclient``) : le module reste planifié en production (A2).
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Api
from tests.test_licenses import (  # noqa: F401  (fixtures)
    _confirmed_payment,
    _generate,
    admin,
    signing,
)
from tests.test_restaurant_orders import _api, _code, _ok

MODULE = "restaurant.tables"


@pytest.fixture
def standard(provision: Any, tclient: TestClient) -> Any:
    t = provision("rt-lic", profile="restaurant.restaurant", plan="STANDARD")
    return t, _api(tclient, "owner@rt-lic.example.com")


@pytest.fixture
def plan_without_tables(owner_db: Session) -> Iterator[None]:
    """Plan STANDARD privé de ``restaurant.tables`` le temps de l'émission d'une licence."""
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


def _site_modules(owner: Api, site: Any) -> dict[str, Any]:
    """Capacités du site : codes des modules effectifs et permissions."""
    caps = Api(owner.client, owner.token, site).get("/me/capabilities").json()
    return {"modules": {m["code"] for m in caps["modules"]}, "permissions": caps["permissions"]}


def test_licence_of_the_plan_keeps_tables_effective(
    standard: Any,
    admin: TestClient,  # noqa: F811
    signing: Any,  # noqa: F811
) -> None:
    t, owner = standard
    licence = _ok(_generate(admin, _confirmed_payment(owner, admin, t.subscription_id)), 201)
    assert MODULE in licence["modules"]
    caps = _site_modules(owner, t.site_id)
    assert MODULE in caps["modules"]
    assert "restaurant.tables.table.manage" in caps["permissions"]
    _ok(owner.post("/restaurant/tables", json={"site_id": str(t.site_id), "label": "T1"}), 201)


def test_licence_issued_without_tables_makes_them_unavailable(
    standard: Any,
    admin: TestClient,  # noqa: F811
    signing: Any,  # noqa: F811
    owner_db: Session,
    plan_without_tables: None,
) -> None:
    t, owner = standard
    licence = _ok(_generate(admin, _confirmed_payment(owner, admin, t.subscription_id)), 201)
    assert MODULE not in licence["modules"]
    # Plan rétabli : la licence en vigueur fait foi (modules figés à son émission).
    owner_db.execute(
        text("INSERT INTO plan_modules (plan_code, module_code) VALUES ('STANDARD', :m)"),
        {"m": MODULE},
    )
    owner_db.commit()
    caps = _site_modules(owner, t.site_id)
    assert MODULE not in caps["modules"]
    assert "restaurant.orders" in caps["modules"]  # le reste de l'offre est inchangé
    assert not [p for p in caps["permissions"] if p.startswith("restaurant.tables.")]
    selected = Api(owner.client, owner.token, t.site_id)
    assert _code(selected.get("/restaurant/tables")) == (403, "module_unavailable")
