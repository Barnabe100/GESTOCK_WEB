"""Profils / modules par site, palier B : capacités et API calculées avec le profil du SITE.

Site A : Quincaillerie (point de vente, caisse) ; site B : Entrepôt (ni point de vente ni
caisse). Le plan reste la limite commerciale ; le profil du tenant (profil d'origine) n'est
jamais le profil effectif d'un site.
"""

import uuid
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Api, add_site
from tests.stock_helpers import World, member

ENTREPOT = "distribution.entrepot"
QUINCAILLERIE = "retail.quincaillerie"


def _set_site_profile(owner_db: Session, site: str, profile: str) -> None:
    """Profil d'un site posé hors API (le changement de profil n'existe pas encore)."""
    owner_db.execute(
        text("UPDATE sites SET business_profile_code = :p WHERE id = :s"),
        {"p": profile, "s": site},
    )
    owner_db.commit()


def _caps(api: Api, site: str | None = None) -> dict[str, Any]:
    response = Api(api.client, api.token, uuid.UUID(site) if site else None).get("/me/capabilities")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _modules(caps: dict[str, Any]) -> set[str]:
    return {m["code"] for m in caps["modules"]}


def _site_modules(api: Api, site: str) -> dict[str, dict[str, Any]]:
    response = api.get(f"/sites/{site}/modules")
    assert response.status_code == 200, response.text
    return {m["code"]: m for m in response.json()}


def test_two_sites_with_two_profiles_have_their_own_capabilities(
    world: World, owner_db: Session
) -> None:
    _set_site_profile(owner_db, world.site2, ENTREPOT)
    a, b = _caps(world.owner, world.site), _caps(world.owner, world.site2)
    assert (a["profile"]["code"], a["profile_scope"]) == (QUINCAILLERIE, "site")
    assert (b["profile"]["code"], b["profile_scope"]) == (ENTREPOT, "site")
    # Entrepôt : ni point de vente ni caisse, sur ce site seulement.
    assert {"pos", "cash_register"} <= _modules(a)
    assert not {"pos", "cash_register"} & _modules(b)
    assert "pos.terminal.use" in a["permissions"]
    assert "pos.terminal.use" not in b["permissions"]
    # Présentation du profil du site (navigation, thème).
    assert b["ux"]["theme"]["density"] == "compact"
    assert a["ux"]["navigation"] != b["ux"]["navigation"]
    # Chaque site expose son profil.
    profiles = {s["id"]: s["profile"]["code"] for s in a["sites"]}
    assert profiles == {world.site: QUINCAILLERIE, world.site2: ENTREPOT}
    # Le site A n'est pas modifié par la configuration du site B.
    assert _caps(world.owner, world.site)["profile"]["code"] == QUINCAILLERIE


def test_site_modules_follow_the_profile_of_that_site(world: World, owner_db: Session) -> None:
    _set_site_profile(owner_db, world.site2, ENTREPOT)
    a = _site_modules(world.owner, world.site)
    b = _site_modules(world.owner, world.site2)
    assert a["pos"]["in_profile"] and a["pos"]["in_plan"] and a["pos"]["effective"]
    # Le profil Entrepôt ne propose pas le point de vente : absent de la liste du site B.
    assert "pos" not in b and "cash_register" not in b
    assert b["stock"]["in_profile"] and b["stock"]["effective"]
    # Indépendant du site sélectionné par l'en-tête : la ressource demandée fait foi.
    scoped = Api(world.owner.client, world.owner.token, uuid.UUID(world.site))
    assert "pos" not in _site_modules(scoped, world.site2)


def test_writes_on_a_site_follow_its_profile_server_side(world: World, owner_db: Session) -> None:
    _set_site_profile(owner_db, world.site2, ENTREPOT)
    body = {
        "site_id": world.site2,
        "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        "idempotency_key": str(uuid.uuid4()),
    }
    # Site B sélectionné : module indisponible sur ce site.
    on_b = Api(world.owner.client, world.owner.token, uuid.UUID(world.site2))
    refused = on_b.post("/pos/checkout", json=body)
    assert (refused.status_code, refused.json()["code"]) == (403, "module_unavailable")
    # Sans site sélectionné (union des modules) : l'écriture est revérifiée pour le site B.
    revalidated = world.owner.post("/pos/checkout", json=body)
    assert revalidated.status_code == 403, revalidated.text
    assert revalidated.json()["code"] == "permission_denied"
    assert revalidated.json()["site_id"] == world.site2


def test_the_plan_still_bounds_every_site(provision: Any, api_for: Any, owner_db: Session) -> None:
    t = provision("plan-borne", profile="restaurant.maquis", plan="ENTREPRISE")
    owner = api_for("owner@plan-borne.example.com")
    standard = add_site(owner, "Annexe", "ANX", plan="STANDARD")
    assert standard.status_code == 201, standard.text
    annex = standard.json()["id"]
    assert (
        owner_db.execute(
            text("SELECT business_profile_code FROM sites WHERE id = :s"), {"s": annex}
        ).scalar_one()
        == "restaurant.maquis"
    )
    main, other = _caps(owner, str(t.site_id)), _caps(owner, annex)
    # Même profil, offres différentes : la fonctionnalité du plan ENTREPRISE reste à son site.
    assert "stock.transfers" in main["features"]
    assert "stock.transfers" not in other["features"]
    # Module optionnel du profil hors plan STANDARD : proposé, jamais inclus ni effectif.
    qr = _site_modules(owner, annex)["restaurant.qr"]
    assert qr["in_profile"] is True
    assert qr["in_plan"] is False
    assert qr["effective"] is False
    # Un profil ne fait pas activer un module qu'aucun abonnement n'inclut.
    _set_site_profile(owner_db, str(t.site_id), "distribution.entrepot")
    _set_site_profile(owner_db, annex, "distribution.entrepot")
    refused = owner.put(f"/sites/{annex}/modules/pos", json={"enabled": True})
    assert (refused.status_code, refused.json()["code"]) == (422, "module_not_offered")


def test_sites_of_another_tenant_stay_out_of_reach(
    world: World, provision: Any, api_for: Any
) -> None:
    other = provision("beta", profile="restaurant.restaurant")
    beta = api_for("owner@beta.example.com")
    assert beta.get(f"/sites/{world.site}/modules").status_code == 404
    foreign = Api(beta.client, beta.token, uuid.UUID(world.site)).get("/me/capabilities")
    assert (foreign.status_code, foreign.json()["code"]) == (403, "site_access_denied")
    assert world.owner.get(f"/sites/{other.site_id}/modules").status_code == 404
    # Chaque tenant garde ses propres profils de sites.
    assert _caps(beta)["profile"]["code"] == "restaurant.restaurant"
    assert {s["profile"]["code"] for s in _caps(world.owner)["sites"]} == {QUINCAILLERIE}


def test_all_sites_view_uses_a_reference_profile_and_accessible_sites(
    world: World, owner_db: Session, client: TestClient
) -> None:
    _set_site_profile(owner_db, world.site2, ENTREPOT)
    # Propriétaire : site de référence = site principal (le plus ancien) ; modules : union.
    caps = _caps(world.owner)
    assert caps["profile_scope"] == "reference"
    assert caps["main_site_id"] == world.site
    assert caps["profile"]["code"] == QUINCAILLERIE
    assert {"pos", "stock"} <= _modules(caps)
    # Membre limité au site B (Entrepôt) : ni le profil ni les modules du site A.
    owner_ns = SimpleNamespace(owner=world.owner)
    depot = member(
        owner_ns,  # type: ignore[arg-type]
        client,
        "depot@alpha.example.com",
        "manager",
        all_sites=False,
        site_ids=[world.site2],
    )
    restricted = _caps(depot)
    assert restricted["main_site_id"] == world.site2
    assert (restricted["profile"]["code"], restricted["profile_scope"]) == (ENTREPOT, "reference")
    assert not {"pos", "cash_register"} & _modules(restricted)
    assert [s["id"] for s in restricted["sites"]] == [world.site2]
    # Le site A lui reste inaccessible.
    refused = Api(depot.client, depot.token, uuid.UUID(world.site)).get("/me/capabilities")
    assert refused.status_code == 403


def test_member_with_several_sites_gets_each_site_profile(
    world: World, owner_db: Session, client: TestClient
) -> None:
    _set_site_profile(owner_db, world.site2, ENTREPOT)
    both = member(
        SimpleNamespace(owner=world.owner),  # type: ignore[arg-type]
        client,
        "multi@alpha.example.com",
        "manager",
        all_sites=False,
        site_ids=[world.site, world.site2],
    )
    assert _caps(both, world.site)["profile"]["code"] == QUINCAILLERIE
    assert _caps(both, world.site2)["profile"]["code"] == ENTREPOT
    assert "pos" in _modules(_caps(both, world.site))
    assert "pos" not in _modules(_caps(both, world.site2))
    union = _caps(both)
    assert "pos" in _modules(union)
    assert {s["id"]: s["profile"]["code"] for s in union["sites"]} == {
        world.site: QUINCAILLERIE,
        world.site2: ENTREPOT,
    }


def test_tenant_profile_is_never_the_effective_profile_of_a_site(
    world: World, owner_db: Session
) -> None:
    # Profil d'origine du tenant différent de celui de ses sites.
    owner_db.execute(
        text(
            "UPDATE tenants SET business_profile_code = 'restaurant.maquis' WHERE id = ("
            "SELECT tenant_id FROM sites WHERE id = :s)"
        ),
        {"s": world.site},
    )
    owner_db.commit()
    _set_site_profile(owner_db, world.site2, ENTREPOT)
    assert _caps(world.owner, world.site)["profile"]["code"] == QUINCAILLERIE
    assert _caps(world.owner, world.site2)["profile"]["code"] == ENTREPOT
    # Vue « Tous les sites » : profil du site de référence, pas celui du tenant.
    reference = _caps(world.owner)
    assert reference["profile"]["code"] == QUINCAILLERIE
    assert "restaurant.tables" not in _modules(reference)
    assert "restaurant.tables" not in _site_modules(world.owner, world.site)
    # Abonnement d'un site : profil de ce site.
    subscriptions = world.owner.get("/subscriptions")
    assert subscriptions.status_code == 200, subscriptions.text


def test_existing_single_site_tenants_keep_the_same_capabilities(
    provision: Any, api_for: Any
) -> None:
    t = provision("historique", profile="retail.alimentation", plan="ENTREPRISE")
    owner = api_for("owner@historique.example.com")
    on_site, consolidated = _caps(owner, str(t.site_id)), _caps(owner)
    for caps in (on_site, consolidated):
        assert caps["profile"]["code"] == "retail.alimentation"
        assert {"pos", "cash_register", "sales", "stock"} <= _modules(caps)
    assert _modules(on_site) == _modules(consolidated)
    assert on_site["permissions"] == consolidated["permissions"]
    assert on_site["ux"] == consolidated["ux"]
    assert owner.get("/sites").json()[0]["business_profile_code"] == "retail.alimentation"
