from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import mount_module_routers
from app.platform.registry import ModuleManifest, ModuleRegistry


def _by_code(api: Any) -> dict[str, dict[str, Any]]:
    return {m["code"]: m for m in api.get("/modules").json()}


def test_module_listing_reflects_profile_and_plan(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant.restaurant", plan="STANDARD")
    modules = _by_code(api_for("owner@resto.example.com"))
    assert modules["restaurant.qr"]["in_profile"] is True
    assert modules["restaurant.qr"]["in_plan"] is False
    assert modules["pos"]["enabled"] is True and modules["pos"]["status"] == "available"
    assert modules["restaurant.menu"]["status"] == "planned"
    assert modules["users"]["core"] is True
    assert "stock" in modules


def test_toggle_optional_module(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant.restaurant", plan="ENTREPRISE")
    api = api_for("owner@resto.example.com")
    assert _by_code(api)["restaurant.qr"]["enabled"] is False
    assert api.put("/modules/restaurant.qr", json={"enabled": True}).status_code == 204
    assert _by_code(api)["restaurant.qr"]["effective"] is True
    caps = api.get("/me/capabilities").json()
    assert "restaurant.qr" in {m["code"] for m in caps["modules"]}


def test_toggle_rules(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant.restaurant", plan="STANDARD")
    provision("quinc", profile="retail.quincaillerie")
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
    provision: Any, api_for: Any, owner_db: Session, client: TestClient
) -> None:
    """Un routeur de module métier n'est joignable que si le module est effectif pour le
    tenant : la garde require_module est ajoutée automatiquement au montage."""
    t = provision("alpha", profile="retail.alimentation")
    api = api_for("owner@alpha.example.com")
    assert api.get("/catalog/categories").status_code == 200

    owner_db.execute(
        text(
            "UPDATE tenant_modules SET enabled = false "
            "WHERE tenant_id = :t AND module_code = 'catalog'"
        ),
        {"t": t.tenant_id},
    )
    owner_db.commit()
    denied = api.get("/catalog/categories")
    assert denied.status_code == 403
    assert denied.json()["code"] == "module_unavailable"
    assert client.get("/api/v1/catalog/categories").status_code == 401


def test_mount_uses_given_registry() -> None:
    router = APIRouter()

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "ok"}

    registry = ModuleRegistry([ModuleManifest(code="demo.sub", router=router)])
    api = APIRouter()
    mount_module_routers(api, registry)
    app = FastAPI()
    app.include_router(api)
    assert "/demo/sub/ping" in app.openapi()["paths"]
