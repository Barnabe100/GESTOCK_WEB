"""Palier G — recette du modèle « profil + modules par site » (ADR-0048), scénario de bout en bout.

Entreprise A : site A1 Alimentation, site A2 Entrepôt (créé avec ce profil) ; entreprise B : site
B1 Maquis. Vérifie, sans fuite A1 ↔ A2 ni A ↔ B : profil, modules, capacités, navigation,
tableau de bord, thème et données ; puis un changement réel du profil de A2 (API) qui ne touche
ni A1, ni le profil d'origine de A, ni B. Aucune règle nouvelle : recette des paliers A à F.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Api, add_site
from tests.stock_helpers import member
from tests.test_site_profile_change import _apply

ALIMENTATION = "retail.alimentation"
ENTREPOT = "distribution.entrepot"
MAQUIS = "restaurant.maquis"
QUINCAILLERIE = "retail.quincaillerie"


def _caps(api: Api, site: str) -> dict[str, Any]:
    response = Api(api.client, api.token, uuid.UUID(site)).get("/me/capabilities")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _codes(caps: dict[str, Any]) -> set[str]:
    return {m["code"] for m in caps["modules"]}


def _ok(response: Any) -> Any:
    assert response.status_code in (200, 201), response.text
    return response.json()


def _levels(api: Api, site: str) -> dict[str, Decimal]:
    page = _ok(api.get("/stock/levels", params={"site_id": site, "limit": 100}))
    return {item["reference"]: Decimal(item["quantity"]) for item in page["items"]}


def _profile_of_tenant(owner_db: Session, tenant_id: Any) -> str:
    code: str = owner_db.execute(
        text("SELECT business_profile_code FROM tenants WHERE id = :t"), {"t": tenant_id}
    ).scalar_one()
    return code


@pytest.fixture
def scenario(provision: Any, api_for: Any) -> SimpleNamespace:
    a = provision("recette-a", profile=ALIMENTATION, plan="ENTREPRISE")
    owner_a: Api = api_for("owner@recette-a.example.com")
    a2 = _ok(add_site(owner_a, "Dépôt A2", "A2", "warehouse", business_profile_code=ENTREPOT))
    b = provision("recette-b", profile=MAQUIS, plan="ENTREPRISE")
    owner_b: Api = api_for("owner@recette-b.example.com")
    a1 = str(a.site_id)
    category = _ok(owner_a.post("/catalog/categories", json={"name": "Épicerie"}))
    article = _ok(
        owner_a.post(
            "/catalog/articles",
            json={
                "reference": "RIZ-25",
                "designation": "Riz 25 kg",
                "category_id": category["id"],
                "unit": "sac",
                "purchase_price": "15000",
                "sale_price": "17500",
                "site_ids": [a1, a2["id"]],
            },
        )
    )
    supplier = _ok(owner_a.post("/suppliers", json={"name": "Grossiste Recette"}))
    entry = _ok(
        owner_a.post(
            "/stock/entries",
            json={
                "site_id": a1,
                "supplier_id": supplier["id"],
                "lines": [{"article_id": article["id"], "quantity": "12", "unit_cost": "15000"}],
            },
        )
    )
    _ok(owner_a.post(f"/stock/entries/{entry['id']}/validate"))
    return SimpleNamespace(
        a=a, b=b, owner_a=owner_a, owner_b=owner_b, a1=a1, a2=a2["id"], b1=str(b.site_id)
    )


def test_each_site_has_its_own_profile_modules_capabilities_and_experience(
    scenario: SimpleNamespace, owner_db: Session
) -> None:
    s = scenario
    a1, a2 = _caps(s.owner_a, s.a1), _caps(s.owner_a, s.a2)
    b1 = _caps(s.owner_b, s.b1)
    # Profil : celui du site, jamais celui de l'entreprise.
    assert (a1["profile"]["code"], a2["profile"]["code"], b1["profile"]["code"]) == (
        ALIMENTATION,
        ENTREPOT,
        MAQUIS,
    )
    assert _profile_of_tenant(owner_db, s.a.tenant_id) == ALIMENTATION
    # Modules et capacités : point de vente sur A1 et B1, jamais sur A2 (hors profil Entrepôt).
    assert {"pos", "cash_register"} <= _codes(a1)
    assert not {"pos", "cash_register"} & _codes(a2)
    assert "pos.terminal.use" in a1["permissions"]
    assert "pos.terminal.use" not in a2["permissions"]
    a2_modules = {m["code"]: m for m in _ok(s.owner_a.get(f"/sites/{s.a2}/modules"))}
    # Hors profil du site : non proposé, donc absent de la liste des modules du site.
    assert "pos" not in a2_modules and a2_modules["stock"]["effective"] is True
    # Navigation, tableau de bord et thème : présentation du profil de CHAQUE site.
    assert a1["ux"]["navigation"] != a2["ux"]["navigation"]
    assert a1["ux"]["theme"] != a2["ux"]["theme"]
    assert a1["ux"]["dashboard"] != b1["ux"]["dashboard"]
    # Les sites de B n'apparaissent jamais dans les capacités de A (et inversement).
    assert {site["id"] for site in a1["sites"]} == {s.a1, s.a2}
    assert {site["id"] for site in b1["sites"]} == {s.b1}


def test_data_never_leaks_between_sites_or_companies(
    scenario: SimpleNamespace, client: TestClient
) -> None:
    s = scenario
    # Stock reçu sur A1 seulement : A2 n'a rien (article dans son assortiment, non stocké).
    assert _levels(s.owner_a, s.a1)["RIZ-25"] == Decimal("12")
    assert _levels(s.owner_a, s.a2)["RIZ-25"] == 0
    # Membre de A limité à A1 : A2 lui est inaccessible.
    only_a1 = member(
        SimpleNamespace(owner=s.owner_a),  # type: ignore[arg-type]
        client,
        "gestion-a1@recette-a.example.com",
        "manager",
        all_sites=False,
        site_ids=[s.a1],
    )
    assert set(_levels(only_a1, s.a1)) == {"RIZ-25"}
    # Lecture : la liste est restreinte aux sites accessibles (aucune ligne de A2) ; écriture
    # sur A2 : refusée.
    assert _ok(only_a1.get("/stock/levels", params={"site_id": s.a2}))["items"] == []
    denied = only_a1.post("/stock/entries", json={"site_id": s.a2, "lines": []})
    assert denied.status_code == 403, denied.text
    assert {site["id"] for site in _caps(only_a1, s.a1)["sites"]} == {s.a1}
    # Entreprise B : ni les articles, ni le stock, ni les sites de A.
    assert _ok(s.owner_b.get("/catalog/articles"))["total"] == 0
    assert _ok(s.owner_b.get("/stock/levels", params={"limit": 100}))["items"] == []
    assert s.owner_b.get(f"/sites/{s.a1}/modules").status_code == 404
    foreign = Api(s.owner_b.client, s.owner_b.token, uuid.UUID(s.a1)).get("/me/capabilities")
    assert foreign.status_code == 403


def test_changing_one_site_profile_touches_nothing_else(
    scenario: SimpleNamespace, owner_db: Session
) -> None:
    s = scenario
    before_a1 = _caps(s.owner_a, s.a1)
    before_b1 = _caps(s.owner_b, s.b1)
    a1_modules = _ok(s.owner_a.get(f"/sites/{s.a1}/modules"))
    # A2 → Quincaillerie par l'API (aperçu, puis changement contrôlé).
    changed = _apply(s.owner_a, s.a2, QUINCAILLERIE)
    assert changed.status_code == 200, changed.text
    after_a2 = _caps(s.owner_a, s.a2)
    assert after_a2["profile"]["code"] == QUINCAILLERIE
    assert "pos" in _codes(after_a2)
    # A1, le profil d'origine de A et B restent identiques.
    after_a1 = _caps(s.owner_a, s.a1)
    assert after_a1["profile"] == before_a1["profile"]
    assert after_a1["modules"] == before_a1["modules"]
    assert after_a1["permissions"] == before_a1["permissions"]
    assert after_a1["ux"] == before_a1["ux"]
    assert _ok(s.owner_a.get(f"/sites/{s.a1}/modules")) == a1_modules
    assert _profile_of_tenant(owner_db, s.a.tenant_id) == ALIMENTATION
    assert _caps(s.owner_b, s.b1) == before_b1
    # Données préservées : le stock de A1 n'a pas bougé.
    assert _levels(s.owner_a, s.a1)["RIZ-25"] == Decimal("12")
