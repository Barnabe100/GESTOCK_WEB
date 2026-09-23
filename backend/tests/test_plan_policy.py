import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.catalog.loader import DATA_DIR, CatalogError, load_catalog
from app.platform.catalog.models import Plan
from app.platform.registry import (
    LimitDef,
    ModuleManifest,
    ModuleRegistry,
    RegistryError,
    get_registry,
)
from app.platform.subscriptions.plan_policy import PlanPolicy


def test_limits_and_features_are_declared_by_modules() -> None:
    registry = get_registry()
    assert registry.limit_codes() == {"max_sites", "max_users"}
    with pytest.raises(RegistryError, match="préfixée"):
        ModuleRegistry([ModuleManifest(code="a", features=("b.option",))])
    with pytest.raises(RegistryError, match="limite en double"):
        ModuleRegistry(
            [
                ModuleManifest(code="a", limits=(LimitDef("max_x", lambda db: 0),)),
                ModuleManifest(code="b", limits=(LimitDef("max_x", lambda db: 0),)),
            ]
        )


def test_catalog_rejects_unknown_limit_or_feature(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    plans = data / "plans.toml"
    original = plans.read_text()
    plans.write_text(original.replace("max_users = 5", "max_users = 5\nmax_licornes = 3"))
    with pytest.raises(CatalogError, match="limite inconnue max_licornes"):
        load_catalog(get_registry(), data)
    plans.write_text(original.replace("features = []", 'features = ["stock.teleport"]', 1))
    with pytest.raises(CatalogError, match="fonctionnalité inconnue"):
        load_catalog(get_registry(), data)


def test_plan_features_only_for_effective_modules() -> None:
    registry = ModuleRegistry(
        [
            ModuleManifest(code="stock", features=("stock.transfers",)),
            ModuleManifest(code="pos", features=("pos.offline",)),
        ]
    )
    plan = Plan(code="X", name="X", limits={}, features=["stock.transfers", "pos.offline"])
    policy = PlanPolicy(None, plan, registry)  # type: ignore[arg-type]
    assert policy.features({"stock"}) == {"stock.transfers"}


def test_limits_exposed_with_usage(provision: Any, api_for: Any) -> None:
    provision("alpha", plan="STANDARD")
    api = api_for("owner@alpha.example.com")
    caps = api.get("/me/capabilities").json()
    assert caps["limits"] == {
        "max_sites": {"limit": 1, "used": 1},
        "max_users": {"limit": 5, "used": 1},
    }
    assert caps["features"] == []
    sub = api.get("/subscription").json()
    assert sub["limits"]["max_sites"] == {"limit": 1, "used": 1}


def test_limit_values_come_from_plan_data(provision: Any, api_for: Any, owner_db: Session) -> None:
    """Changer l'offre = changer les données du plan, sans toucher au code."""
    provision("alpha", plan="STANDARD")
    api = api_for("owner@alpha.example.com")
    assert api.post("/sites", json={"name": "B", "code": "B"}).json()["code"] == (
        "plan_limit_reached"
    )
    owner_db.execute(
        text(
            "UPDATE plans SET limits = jsonb_set(limits, '{max_sites}', '2') WHERE code='STANDARD'"
        )
    )
    owner_db.commit()
    try:
        assert api.post("/sites", json={"name": "B", "code": "B"}).status_code == 201
    finally:
        owner_db.execute(
            text(
                "UPDATE plans SET limits = jsonb_set(limits, '{max_sites}', '1') "
                "WHERE code='STANDARD'"
            )
        )
        owner_db.commit()
