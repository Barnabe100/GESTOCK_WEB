from typing import Any

from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import mount_module_routers
from app.core.config import Settings
from app.main import create_app
from app.modules.planned import PLANNED_MODULES
from app.platform.context import get_registry_dep
from app.platform.manifests import PLATFORM_MODULES
from app.platform.registry import ModuleManifest, ModuleRegistry
from tests.conftest import Api, login


def _by_code(api: Any) -> dict[str, dict[str, Any]]:
    return {m["code"]: m for m in api.get("/modules").json()}


def test_module_listing_reflects_profile_and_plan(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant", plan="STANDARD")
    modules = _by_code(api_for("owner@resto.example.com"))
    assert modules["restaurant.qr"]["in_profile"] is True
    assert modules["restaurant.qr"]["in_plan"] is False
    assert modules["pos"]["enabled"] is True and modules["pos"]["status"] == "planned"
    assert modules["users"]["core"] is True
    assert "stock" in modules


def test_toggle_optional_module(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant", plan="ENTREPRISE")
    api = api_for("owner@resto.example.com")
    assert _by_code(api)["restaurant.qr"]["enabled"] is False
    assert api.put("/modules/restaurant.qr", json={"enabled": True}).status_code == 204
    assert _by_code(api)["restaurant.qr"]["effective"] is True
    caps = api.get("/me/capabilities").json()
    assert "restaurant.qr" in {m["code"] for m in caps["modules"]}


def test_toggle_rules(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant", plan="STANDARD")
    provision("quinc", profile="quincaillerie")
    resto = api_for("owner@resto.example.com")
    assert resto.put("/modules/restaurant.qr", json={"enabled": True}).json()["code"] == (
        "module_not_offered"
    )
    dependents = resto.put("/modules/catalog", json={"enabled": False})
    assert dependents.status_code == 409 and dependents.json()["code"] == "module_has_dependents"
    assert resto.put("/modules/users", json={"enabled": False}).json()["code"] == (
        "module_not_configurable"
    )
    quinc = api_for("owner@quinc.example.com")
    assert quinc.put("/modules/restaurant.tables", json={"enabled": True}).json()["code"] == (
        "module_not_offered"
    )
    assert quinc.put("/modules/alerts", json={"enabled": False}).status_code == 204
    assert quinc.put("/modules/stock", json={"enabled": False}).status_code == 409
    missing = quinc.put("/modules/sales", json={"enabled": False})
    assert missing.json()["code"] == "module_has_dependents"


def test_module_routers_are_guarded_by_module_activation(
    settings: Settings, provision: Any, owner_db: Session
) -> None:
    """Un routeur de module métier n'est joignable que si le module est effectif pour le
    tenant : la garde require_module est ajoutée automatiquement au montage."""
    router = APIRouter()

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "ok"}

    catalog = ModuleManifest(code="catalog", router=router)  # « implémenté » pour le test
    registry = ModuleRegistry(
        [*PLATFORM_MODULES, *(catalog if m.code == "catalog" else m for m in PLANNED_MODULES)]
    )
    app = create_app(settings)
    modules_router = APIRouter()
    mount_module_routers(modules_router, registry)
    app.include_router(modules_router, prefix=settings.api_v1_prefix)
    app.dependency_overrides[get_registry_dep] = lambda: registry

    t = provision("alpha", profile="alimentation")
    with TestClient(app) as client:
        token = login(client, "owner@alpha.example.com").json()["access_token"]
        api = Api(client, token)
        assert api.get("/catalog/ping").json() == {"pong": "ok"}

        owner_db.execute(
            text(
                "UPDATE tenant_modules SET enabled = false "
                "WHERE tenant_id = :t AND module_code = 'catalog'"
            ),
            {"t": t.tenant_id},
        )
        owner_db.commit()
        denied = api.get("/catalog/ping")
        assert denied.status_code == 403
        assert denied.json()["code"] == "module_unavailable"
        assert client.get("/api/v1/catalog/ping").status_code == 401
