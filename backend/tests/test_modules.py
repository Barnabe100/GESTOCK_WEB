from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.v1 import mount_module_routers
from app.platform import context
from app.platform.registry import ModuleManifest, ModuleRegistry
from tests.conftest import set_site_module


def _by_code(api: Any) -> dict[str, dict[str, Any]]:
    return {m["code"]: m for m in api.get("/modules").json()}


def test_module_listing_reflects_profile_and_plan(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant.restaurant", plan="STANDARD")
    modules = _by_code(api_for("owner@resto.example.com"))
    assert modules["restaurant.qr"]["in_profile"] is True
    assert modules["restaurant.qr"]["in_plan"] is False
    assert modules["pos"]["enabled"] is True and modules["pos"]["status"] == "available"
    # Palier R1 (ADR-0049) : le menu est livré ; les autres modules restaurant restent planifiés.
    assert modules["restaurant.menu"]["status"] == "available"
    assert modules["restaurant.orders"]["status"] == "planned"
    assert modules["users"]["core"] is True
    assert "stock" in modules


def test_toggle_optional_module_on_a_site(provision: Any, api_for: Any) -> None:
    t = provision("resto", profile="restaurant.restaurant", plan="ENTREPRISE")
    api = api_for("owner@resto.example.com")
    assert set_site_module(api, t.site_id, "alerts", False).status_code == 204
    assert _by_code(api)["alerts"]["enabled"] is False
    assert set_site_module(api, t.site_id, "alerts", True).status_code == 204
    assert _by_code(api)["alerts"]["enabled"] is True
    assert _by_code(api)["alerts"]["effective"] is True
    caps = api.get("/me/capabilities").json()
    assert "alerts" in {m["code"] for m in caps["modules"]}
    # Module optionnel « Bientôt disponible » (palier E.1) : jamais activé.
    assert _by_code(api)["restaurant.qr"]["enabled"] is False
    refused = set_site_module(api, t.site_id, "restaurant.qr", True)
    assert (refused.status_code, refused.json()["code"]) == (422, "module_not_implemented")
    assert _by_code(api)["restaurant.qr"]["enabled"] is False


def test_tenant_level_toggle_is_retired(provision: Any, api_for: Any) -> None:
    provision("alpha", profile="retail.alimentation")
    api = api_for("owner@alpha.example.com")
    refused = api.put("/modules/pos", json={"enabled": False})
    assert (refused.status_code, refused.json()["code"]) == (409, "module_is_per_site")
    assert _by_code(api)["pos"]["enabled"] is True


def test_toggle_rules(provision: Any, api_for: Any) -> None:
    r = provision("resto", profile="restaurant.restaurant", plan="STANDARD")
    q = provision("quinc", profile="retail.quincaillerie")
    resto = api_for("owner@resto.example.com")
    assert set_site_module(resto, r.site_id, "restaurant.qr", True).json()["code"] == (
        "module_not_offered"
    )
    dependents = set_site_module(resto, r.site_id, "catalog", False)
    assert dependents.status_code == 409 and dependents.json()["code"] == "module_has_dependents"
    assert set_site_module(resto, r.site_id, "users", False).json()["code"] == (
        "module_not_configurable"
    )
    quinc = api_for("owner@quinc.example.com")
    assert set_site_module(quinc, q.site_id, "restaurant.tables", True).json()["code"] == (
        "module_not_offered"
    )
    assert set_site_module(quinc, q.site_id, "alerts", False).status_code == 204
    assert set_site_module(quinc, q.site_id, "stock", False).status_code == 409
    missing = set_site_module(quinc, q.site_id, "sales", False)
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
            "UPDATE site_modules SET enabled = false "
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
    try:
        mount_module_routers(api, registry)
        app = FastAPI()
        app.include_router(api)
        assert "/demo/sub/ping" in app.openapi()["paths"]
    finally:
        # Le montage déclare ``demo.sub`` dans l'ensemble global vérifié au démarrage :
        # retiré pour ne pas faire échouer une application construite ensuite.
        context._DECLARED_MODULES.discard("demo.sub")
