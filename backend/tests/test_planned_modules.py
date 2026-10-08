"""Palier E.1 : un module « Bientôt disponible » (``ModuleStatus.PLANNED``) n'est jamais activé.

Source de vérité : le statut du manifeste dans le registre (``app/modules/planned.py``). Seul
point d'activation explicite : ``ModuleService.set_enabled_for_site``
(``PUT /sites/{id}/modules/{code}``) ; la route de l'entreprise est retirée, la console n'active
aucun module. Le contrôle est fait par le serveur : une requête forgée est refusée de la même
façon. Le module reste visible et consultable au catalogue.
"""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.registry import ModuleStatus, get_registry
from tests.conftest import Api, add_site, set_site_module
from tests.stock_helpers import member

PLANNED = sorted(m.code for m in get_registry().all() if m.status is ModuleStatus.PLANNED)


def _site_modules(api: Api, site: Any) -> dict[str, dict[str, Any]]:
    response = api.get(f"/sites/{site}/modules")
    assert response.status_code == 200, response.text
    return {m["code"]: m for m in response.json()}


def _caps(api: Api, site: Any) -> dict[str, Any]:
    response = Api(api.client, api.token, uuid.UUID(str(site))).get("/me/capabilities")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _activation_audits(owner_db: Session, tenant: Any) -> list[str]:
    return list(
        owner_db.scalars(
            text(
                "SELECT entity_id FROM audit_logs WHERE tenant_id = :t "
                "AND action = 'module.enabled' ORDER BY occurred_at, id"
            ),
            {"t": tenant},
        )
    )


@pytest.fixture
def resto(provision: Any, api_for: Any) -> SimpleNamespace:
    """Maquis ENTREPRISE : les modules restaurant planifiés sont proposés par le profil ET
    inclus dans l'abonnement du site (seul le statut « planifié » s'oppose à l'activation)."""
    t = provision("resto-e1", profile="restaurant.maquis", plan="ENTREPRISE")
    owner = api_for("owner@resto-e1.example.com")
    return SimpleNamespace(owner=owner, site=t.site_id, tenant=t.tenant_id)


def test_the_registry_declares_planned_modules() -> None:
    assert "restaurant.qr" in PLANNED and "restaurant.tables" in PLANNED
    assert "pos" not in PLANNED and "alerts" not in PLANNED


def test_available_module_in_profile_and_plan_is_activated(resto: SimpleNamespace) -> None:
    assert set_site_module(resto.owner, resto.site, "alerts", False).status_code == 204
    assert set_site_module(resto.owner, resto.site, "alerts", True).status_code == 204
    alerts = _site_modules(resto.owner, resto.site)["alerts"]
    assert alerts["activated_for_site"] is True and alerts["effective"] is True


def test_planned_module_offered_by_profile_and_plan_is_refused(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    before = _site_modules(resto.owner, resto.site)["restaurant.qr"]
    assert (before["status"], before["in_profile"], before["in_plan"]) == ("planned", True, True)
    assert before["activated_for_site"] is False
    refused = set_site_module(resto.owner, resto.site, "restaurant.qr", True)
    assert refused.status_code == 422
    assert refused.json()["code"] == "module_not_implemented"
    assert _site_modules(resto.owner, resto.site)["restaurant.qr"]["activated_for_site"] is False
    row = owner_db.execute(
        text(
            "SELECT enabled FROM site_modules WHERE site_id = :s AND module_code = 'restaurant.qr'"
        ),
        {"s": resto.site},
    ).scalar_one_or_none()
    assert row is False
    assert "restaurant.qr" not in _activation_audits(owner_db, resto.tenant)


def test_every_planned_module_is_refused_on_every_path(resto: SimpleNamespace) -> None:
    modules = _site_modules(resto.owner, resto.site)
    # Désactivation d'abord (toujours permise ; dépendants avant leurs dépendances) : la
    # réactivation est alors une activation explicite.
    pending = [c for c in PLANNED if c in modules and modules[c]["activated_for_site"]]
    while pending:
        done = [
            c
            for c in pending
            if set_site_module(resto.owner, resto.site, c, False).status_code == 204
        ]
        assert done, pending
        pending = [c for c in pending if c not in done]
    for code in PLANNED:
        offered = code in modules and modules[code]["in_profile"] and modules[code]["in_plan"]
        refused = set_site_module(resto.owner, resto.site, code, True)
        expected = "module_not_implemented" if offered else "module_not_offered"
        assert (refused.status_code, refused.json()["code"]) == (422, expected), code
        # Route de l'entreprise retirée : jamais une activation.
        retired = resto.owner.put(f"/modules/{code}", json={"enabled": True})
        assert (retired.status_code, retired.json()["code"]) == (409, "module_is_per_site")
    after = _site_modules(resto.owner, resto.site)
    assert not any(after[c]["activated_for_site"] for c in PLANNED if c in after)


def test_forged_direct_request_is_refused(
    resto: SimpleNamespace, client: TestClient, owner_db: Session
) -> None:
    # Requête directe, hors interface, avec des champs inventés : seul ``enabled`` est lu ; le
    # statut du module vient du registre du serveur.
    forged = client.put(
        f"/api/v1/sites/{resto.site}/modules/restaurant.qr",
        headers={"Authorization": f"Bearer {resto.owner.token}", "X-Site-Id": str(resto.site)},
        json={"enabled": True, "status": "available", "in_plan": True, "effective": True},
    )
    assert (forged.status_code, forged.json()["code"]) == (422, "module_not_implemented")
    assert _site_modules(resto.owner, resto.site)["restaurant.qr"]["activated_for_site"] is False
    assert "restaurant.qr" not in _activation_audits(owner_db, resto.tenant)


def test_planned_module_can_still_be_disabled(resto: SimpleNamespace) -> None:
    # Activations planifiées inertes écrites par l'initialisation (défauts du profil) :
    # désactivation permise, réactivation refusée.
    recipes = _site_modules(resto.owner, resto.site)["restaurant.recipes"]
    assert recipes["status"] == "planned" and recipes["activated_for_site"] is True
    assert set_site_module(resto.owner, resto.site, "restaurant.recipes", False).status_code == 204
    recipes = _site_modules(resto.owner, resto.site)["restaurant.recipes"]
    assert recipes["activated_for_site"] is False
    refused = set_site_module(resto.owner, resto.site, "restaurant.recipes", True)
    assert refused.json()["code"] == "module_not_implemented"


def test_out_of_plan_and_not_in_profile_keep_their_code(provision: Any, api_for: Any) -> None:
    standard = provision("resto-std-e1", profile="restaurant.restaurant", plan="STANDARD")
    owner = api_for("owner@resto-std-e1.example.com")
    out_of_plan = set_site_module(owner, standard.site_id, "restaurant.qr", True)
    assert (out_of_plan.status_code, out_of_plan.json()["code"]) == (422, "module_not_offered")
    store = provision("quinc-e1", profile="retail.quincaillerie")
    quinc = api_for("owner@quinc-e1.example.com")
    not_in_profile = set_site_module(quinc, store.site_id, "restaurant.recipes", True)
    assert (not_in_profile.status_code, not_in_profile.json()["code"]) == (
        422,
        "module_not_offered",
    )


def test_planned_module_stays_visible_and_consultable(resto: SimpleNamespace) -> None:
    caps_before = _caps(resto.owner, resto.site)
    set_site_module(resto.owner, resto.site, "restaurant.qr", True)
    site_modules = _site_modules(resto.owner, resto.site)
    assert site_modules["restaurant.qr"]["status"] == "planned"
    assert site_modules["restaurant.menu"]["status"] == "planned"
    summary = {m["code"]: m for m in resto.owner.get("/modules").json()}
    assert summary["restaurant.qr"]["status"] == "planned"
    assert summary["pos"]["status"] == "available" and summary["pos"]["effective"] is True
    # Capacités inchangées par le refus.
    caps_after = _caps(resto.owner, resto.site)
    assert caps_after["modules"] == caps_before["modules"]
    assert caps_after["permissions"] == caps_before["permissions"]


def test_refusal_on_one_site_never_touches_another(resto: SimpleNamespace) -> None:
    other = add_site(resto.owner, "Annexe", "ANX")
    assert other.status_code == 201, other.text
    annex = other.json()["id"]
    before = _site_modules(resto.owner, annex)
    caps_before = _caps(resto.owner, annex)
    refused = set_site_module(resto.owner, resto.site, "restaurant.qr", True)
    assert refused.json()["code"] == "module_not_implemented"
    assert _site_modules(resto.owner, annex) == before
    assert _caps(resto.owner, annex)["modules"] == caps_before["modules"]
    # Un module disponible s'active toujours site par site.
    assert set_site_module(resto.owner, annex, "alerts", False).status_code == 204
    assert _site_modules(resto.owner, resto.site)["alerts"]["activated_for_site"] is True


def test_rbac_is_checked_before_the_planned_rule(
    resto: SimpleNamespace, client: TestClient
) -> None:
    owner_ns = SimpleNamespace(owner=resto.owner)
    seller = member(owner_ns, client, "vendeur@resto-e1.example.com", "seller")  # type: ignore[arg-type]
    denied = set_site_module(seller, resto.site, "restaurant.qr", True)
    assert (denied.status_code, denied.json()["code"]) == (403, "permission_denied")
    viewer = member(owner_ns, client, "consultant@resto-e1.example.com", "viewer")  # type: ignore[arg-type]
    refused = set_site_module(viewer, resto.site, "restaurant.qr", True)
    assert (refused.status_code, refused.json()["code"]) == (403, "permission_denied")
