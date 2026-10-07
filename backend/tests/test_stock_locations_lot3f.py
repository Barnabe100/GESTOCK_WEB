"""Lot 3-F (ADR-0044) — emplacements physiques des articles par site.

Un emplacement appartient à UN site (nom unique par site, insensible à la casse, jamais
supprimé) ; un article a AU PLUS un emplacement courant par site, facultatif ; plusieurs
articles peuvent partager un emplacement. Information de localisation seulement : stock, état
et alertes inchangés ; aucun instantané dans les documents ; aucune copie par un transfert.
Permission ``stock.location.manage`` ; consultation ``stock.level.view`` ; portée des sites ;
isolation entre entreprises (API, RLS, FK composite)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

MANAGE = "stock.location.manage"


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def _create(w: World, name: str, site: str | None = None, api: Api | None = None) -> Any:
    return (api or w.owner).post("/stock/locations", json={"site_id": site or w.site, "name": name})


def _location(w: World, name: str, site: str | None = None) -> dict[str, Any]:
    return dict(_ok(_create(w, name, site), 201))


def _assign(
    w: World, article: int, location: str | None, site: str | None = None, api: Api | None = None
) -> Any:
    return (api or w.owner).put(
        f"/stock/levels/{site or w.site}/{w.articles[article]}/location",
        json={"location_id": location},
    )


def _levels(w: World, api: Api | None = None, **params: Any) -> dict[str, dict[str, Any]]:
    page = _ok((api or w.owner).get("/stock/levels", params={"limit": 200, **params}))
    return {f"{r['reference']}@{r['site_id']}": r for r in page["items"]}


def _member(w: World, client: TestClient, email: str, permissions: list[str], **access: Any) -> Api:
    role = _ok(
        w.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions}), 201
    )
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{"role_id": role["id"]}],
        **(access or {"all_sites": True}),
    }
    _ok(w.owner.post("/members", json=body), 201)
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _audit(w: World, prefix: str) -> list[dict[str, Any]]:
    items = _ok(w.owner.get("/audit-logs", params={"action": prefix, "limit": 50}))["items"]
    return list(reversed(items))


# --- Gestion des emplacements d'un site -------------------------------------------------------


def test_create_rename_unique_per_site_case_insensitive(world: World) -> None:
    rayon = _location(world, "  Rayon A  ")
    assert (rayon["name"], rayon["site_id"], rayon["is_active"]) == ("Rayon A", world.site, True)
    assert rayon["article_count"] == 0
    # Nom unique par site, sans distinction de casse ; le même nom sur un autre site est un
    # AUTRE emplacement (D3).
    assert _code(_create(world, "rayon a")) == (409, "stock_location_name_taken")
    other = _location(world, "Rayon A", world.site2)
    assert other["id"] != rayon["id"] and other["site_id"] == world.site2
    assert _create(world, "   ").status_code == 422
    assert _create(world, "x" * 101).status_code == 422
    # Renommage : contrôlé par site, casse modifiable, audit avant / après.
    reserve = _location(world, "Réserve")
    assert _code(
        world.owner.patch(f"/stock/locations/{reserve['id']}", json={"name": "RAYON A"})
    ) == (
        409,
        "stock_location_name_taken",
    )
    renamed = _ok(world.owner.patch(f"/stock/locations/{rayon['id']}", json={"name": "Rayon A1"}))
    assert renamed["name"] == "Rayon A1"
    recased = _ok(world.owner.patch(f"/stock/locations/{rayon['id']}", json={"name": "RAYON A1"}))
    assert recased["name"] == "RAYON A1"
    actions = [a["action"] for a in _audit(world, "stock_location.")]
    assert actions == [
        "stock_location.created",
        "stock_location.created",
        "stock_location.created",
        "stock_location.renamed",
        "stock_location.renamed",
    ]
    last = _audit(world, "stock_location.renamed")[-1]
    assert last["data"]["name"] == {"before": "Rayon A1", "after": "RAYON A1"}
    assert last["site_id"] == world.site


def test_list_filters_and_deactivation(world: World) -> None:
    a = _location(world, "Allée 1")
    b = _location(world, "Allée 2")
    _location(world, "Zone froide", world.site2)
    _ok(world.owner.post(f"/stock/locations/{b['id']}/deactivate"))
    page = _ok(world.owner.get("/stock/locations", params={"site_id": world.site}))
    assert [loc["name"] for loc in page["items"]] == ["Allée 1", "Allée 2"]
    active = _ok(world.owner.get("/stock/locations", params={"status": "active"}))
    assert {loc["name"] for loc in active["items"]} == {"Allée 1", "Zone froide"}
    found = _ok(world.owner.get("/stock/locations", params={"search": "froide"}))
    assert [loc["site_id"] for loc in found["items"]] == [world.site2]
    # Désactivé : plus affectable ; réactivation possible ; jamais de suppression.
    assert _code(_assign(world, 0, b["id"])) == (422, "stock_location_inactive")
    assert world.owner.delete(f"/stock/locations/{b['id']}").status_code == 405
    _ok(world.owner.post(f"/stock/locations/{b['id']}/activate"))
    _ok(_assign(world, 0, b["id"]))
    assert (
        _ok(world.owner.get("/stock/locations", params={"search": "Allée 2"}))["items"][0][
            "article_count"
        ]
        == 1
    )
    assert a["is_active"] is True
    assert [x["action"] for x in _audit(world, "stock_location.")][-3:] == [
        "stock_location.deactivated",
        "stock_location.activated",
        "stock_location.assigned",
    ]


# --- Affectation article × site -------------------------------------------------------------


def test_assign_reassign_clear_and_several_articles(world: World) -> None:
    rayon = _location(world, "Rayon A")
    reserve = _location(world, "Réserve")
    sh.validated_entry(world, [(0, "10", "100"), (1, "5", "100")])
    # Article sans emplacement : autorisé (D4).
    levels = _levels(world)
    assert levels[f"A-0@{world.site}"]["location_name"] is None
    # Plusieurs articles au même emplacement (D5).
    level = _ok(_assign(world, 0, rayon["id"]))
    assert (level["location_id"], level["location_name"], level["location_active"]) == (
        rayon["id"],
        "Rayon A",
        True,
    )
    _ok(_assign(world, 1, rayon["id"]))
    assert (
        _ok(world.owner.get("/stock/locations", params={"search": "Rayon"}))["items"][0][
            "article_count"
        ]
        == 2
    )
    # Changement puis retrait (non rangé) ; affectation identique : aucun audit.
    _ok(_assign(world, 0, reserve["id"]))
    _ok(_assign(world, 0, reserve["id"]))
    cleared = _ok(_assign(world, 0, None))
    assert cleared["location_id"] is None and cleared["quantity"] == "10.000"
    assert _ok(_assign(world, 0, None))["location_name"] is None  # déjà non rangé
    events = _audit(world, "stock_location.assigned")
    assert [e["data"]["location"] for e in events] == [
        {"before": None, "after": "Rayon A"},
        {"before": None, "after": "Rayon A"},
        {"before": "Rayon A", "after": "Réserve"},
        {"before": "Réserve", "after": None},
    ]
    assert events[0]["entity_id"] == world.articles[0]
    assert events[0]["data"]["reference"] == "A-0" and events[0]["site_id"] == world.site
    assert all(e["user_name"] for e in events)


def test_deactivated_location_keeps_current_assignment(world: World) -> None:
    rayon = _location(world, "Rayon A")
    _ok(_assign(world, 0, rayon["id"]))
    _ok(world.owner.post(f"/stock/locations/{rayon['id']}/deactivate"))
    level = _levels(world)[f"A-0@{world.site}"]
    assert (level["location_name"], level["location_active"]) == ("Rayon A", False)
    # Nouvelle affectation refusée ; reconfirmer l'affectation courante : sans effet ;
    # retrait toujours possible.
    assert _code(_assign(world, 1, rayon["id"])) == (422, "stock_location_inactive")
    assert _ok(_assign(world, 0, rayon["id"]))["location_name"] == "Rayon A"
    _ok(_assign(world, 0, None))
    assert _code(_assign(world, 0, rayon["id"])) == (422, "stock_location_inactive")


def test_refusals(world: World) -> None:
    depot = _location(world, "Zone palettes", world.site2)
    # Emplacement d'un autre site : refusé par le service…
    assert _code(_assign(world, 0, depot["id"])) == (422, "stock_location_other_site")
    unknown = "01a0f000-0000-7000-8000-000000000000"
    assert _code(_assign(world, 0, unknown)) == (404, "stock_location_not_found")
    assert _code(
        world.owner.put(
            f"/stock/levels/{world.site}/{unknown}/location", json={"location_id": None}
        )
    ) == (404, "article_not_found")
    # Article non géré en stock (service) : aucun emplacement.
    category = _ok(world.owner.get("/catalog/categories"))["items"][0]["id"]
    service = _ok(
        world.owner.post(
            "/catalog/articles",
            json={
                "reference": "SRV-1",
                "designation": "Livraison",
                "category_id": category,
                "unit": "forfait",
                "stock_managed": False,
            },
        ),
        201,
    )
    refused = world.owner.put(
        f"/stock/levels/{world.site}/{service['id']}/location", json={"location_id": None}
    )
    assert _code(refused) == (422, "article_not_stock_managed")


def test_location_never_changes_stock_state_or_alerts(world: World, owner_db: Session) -> None:
    """Affecter un emplacement à un article jamais stocké sur le site ne crée aucun niveau :
    l'article reste « non stocké », n'entre pas dans les alertes ; quantité et CMUP intacts."""
    rayon = _location(world, "Rayon A")
    level = _ok(_assign(world, 2, rayon["id"]))
    assert level["state"] == "not_stocked" and level["location_name"] == "Rayon A"
    assert sh.level(owner_db, world, 2) == ("none", "none")
    alerts = _ok(world.owner.get("/alerts/stock", params={"limit": 200}))
    assert world.articles[2] not in {a["article_id"] for a in alerts["items"]}
    sh.validated_entry(world, [(0, "10", "100")])
    _ok(_assign(world, 0, rayon["id"]))
    assert sh.level(owner_db, world, 0) == ("10.000", "100.0000")


# --- Affichage : niveaux, inventaires, entrées / sorties, fiche article, transferts ------------


def test_levels_search_filter_sort(world: World) -> None:
    rayon = _location(world, "Rayon boissons")
    reserve = _location(world, "Allée réserve")
    _ok(_assign(world, 0, rayon["id"]))
    _ok(_assign(world, 1, reserve["id"]))
    site = {"site_id": world.site}
    found = _levels(world, search="boissons", **site)
    assert list(found) == [f"A-0@{world.site}"]
    by_location = _levels(world, location_id=reserve["id"], **site)
    assert list(by_location) == [f"A-1@{world.site}"]
    unlocated = _levels(world, unlocated="true", include_inactive="true", **site)
    assert set(unlocated) == {f"A-2@{world.site}"}
    page = _ok(world.owner.get("/stock/levels", params={"sort": "location", **site}))
    # Croissant : « Allée réserve », « Rayon boissons », puis les non rangés.
    assert [r["reference"] for r in page["items"]] == ["A-1", "A-0", "A-2"]


def test_inventory_lines_show_and_sort_by_location(world: World) -> None:
    rayon = _location(world, "B - Rayon")
    allee = _location(world, "A - Allée")
    _ok(_assign(world, 0, rayon["id"]))
    _ok(_assign(world, 2, allee["id"]))
    sh.validated_entry(world, [(0, "1", "100"), (1, "1", "100"), (2, "1", "100")])
    inventory = _ok(
        world.owner.post("/inventories", json={"site_id": world.site, "inventory_type": "FULL"}),
        201,
    )
    lines = _ok(
        world.owner.get(f"/inventories/{inventory['id']}/lines", params={"sort": "location"})
    )["items"]
    assert [(line["reference"], line["location_name"]) for line in lines] == [
        ("A-2", "A - Allée"),
        ("A-0", "B - Rayon"),
        ("A-1", None),
    ]
    # Aucun instantané : l'information affichée est toujours l'emplacement COURANT.
    _ok(_assign(world, 0, None))
    lines = _ok(world.owner.get(f"/inventories/{inventory['id']}/lines"))["items"]
    assert {line["reference"]: line["location_name"] for line in lines}["A-0"] is None


def test_entries_and_exits_show_current_location(world: World) -> None:
    rayon = _location(world, "Rayon A")
    reserve = _location(world, "Réserve")
    _ok(_assign(world, 0, rayon["id"]))
    entry = sh.validated_entry(world, [(0, "10", "100"), (1, "5", "100")])
    lines = {line["article_reference"]: line for line in entry["lines"]}
    assert lines["A-0"]["location_name"] == "Rayon A"
    assert lines["A-1"]["location_name"] is None  # non rangé : rien n'est bloqué
    exit_doc = sh.exit_doc(world, [(0, "2")])
    assert exit_doc["lines"][0]["location_name"] == "Rayon A"
    # D8 : l'emplacement suit la donnée courante (jamais figé dans le document).
    _ok(_assign(world, 0, reserve["id"]))
    reread = _ok(world.owner.get(f"/stock/entries/{entry['id']}"))
    assert {line["article_reference"]: line["location_name"] for line in reread["lines"]}[
        "A-0"
    ] == "Réserve"
    # Emplacement du site DU document : rien sur un autre site.
    other = sh.validated_entry(world, [(0, "1", "100")], site_id=world.site2)
    assert other["lines"][0]["location_name"] is None


def test_article_view_per_site_and_transfer_without_copy(world: World) -> None:
    rayon = _location(world, "Rayon boissons")
    palettes = _location(world, "Zone palettes", world.site2)
    sh.validated_entry(world, [(0, "10", "100")])
    _ok(_assign(world, 0, rayon["id"]))
    # Transfert vers le dépôt : l'emplacement du site source n'est jamais recopié (D9).
    transfer = _ok(
        world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site,
                "destination_site_id": world.site2,
                "lines": [{"article_id": world.articles[0], "quantity": "4"}],
            },
        ),
        201,
    )
    _ok(world.owner.post(f"/stock/transfers/{transfer['id']}/validate"))
    per_site = _levels(world, article_id=world.articles[0])
    assert per_site[f"A-0@{world.site2}"]["location_name"] is None
    assert per_site[f"A-0@{world.site2}"]["quantity"] == "4.000"
    # Affectation manuelle au dépôt : un emplacement différent par site.
    _ok(_assign(world, 0, palettes["id"], world.site2))
    per_site = _levels(world, article_id=world.articles[0])
    assert per_site[f"A-0@{world.site}"]["location_name"] == "Rayon boissons"
    assert per_site[f"A-0@{world.site2}"]["location_name"] == "Zone palettes"


# --- Permissions et portée des sites ----------------------------------------------------------


def test_permissions(world: World, client: TestClient) -> None:
    rayon = _location(world, "Rayon A")
    seller = sh.member(world, client, "vendeur@example.com", "seller", all_sites=True)
    # Consultation : stock.level.view (Vendeur) ; gestion : stock.location.manage.
    assert _ok(seller.get("/stock/locations"))["total"] == 1
    assert _levels(world, api=seller)[f"A-0@{world.site}"]["location_name"] is None
    assert _create(world, "Interdit", api=seller).status_code == 403
    assert seller.patch(f"/stock/locations/{rayon['id']}", json={"name": "X"}).status_code == 403
    assert seller.post(f"/stock/locations/{rayon['id']}/deactivate").status_code == 403
    assert _assign(world, 0, rayon["id"], api=seller).status_code == 403
    # Le Gestionnaire (rôle de base) reçoit la permission ; seuils et emplacements distincts.
    manager = sh.member(world, client, "gestionnaire@example.com", "manager", all_sites=True)
    assert _create(world, "Réserve", api=manager).status_code == 201
    assert _assign(world, 0, rayon["id"], api=manager).status_code == 200
    thresholds_only = _member(
        world, client, "seuils@example.com", ["stock.level.view", "stock.threshold.manage"]
    )
    assert _assign(world, 1, rayon["id"], api=thresholds_only).status_code == 403
    no_view = _member(world, client, "rien@example.com", ["catalog.article.view"])
    assert no_view.get("/stock/locations").status_code == 403
    roles = {r["template_code"]: r for r in world.owner.get("/roles").json()}
    assert MANAGE in roles["manager"]["permission_codes"]
    assert MANAGE in roles["administrator"]["permission_codes"]
    assert MANAGE not in roles["seller"]["permission_codes"]
    assert MANAGE not in roles["viewer"]["permission_codes"]


def test_member_limited_to_one_site(world: World, client: TestClient) -> None:
    main = _location(world, "Rayon A")
    depot = _location(world, "Zone palettes", world.site2)
    member = sh.member(world, client, "magasinier@example.com", "manager", site_ids=[world.site])
    # Ne voit que les emplacements de SON site…
    names = {loc["name"] for loc in _ok(member.get("/stock/locations"))["items"]}
    assert names == {"Rayon A"}
    assert _ok(member.get("/stock/locations", params={"site_id": world.site2}))["total"] == 0
    # … ne peut ni modifier ceux d'un autre site (introuvables) ni y créer ou y affecter.
    assert _code(member.patch(f"/stock/locations/{depot['id']}", json={"name": "X"})) == (
        404,
        "stock_location_not_found",
    )
    assert _code(member.post(f"/stock/locations/{depot['id']}/deactivate")) == (
        404,
        "stock_location_not_found",
    )
    assert _create(world, "Hors site", world.site2, api=member).status_code == 403
    assert _assign(world, 0, depot["id"], world.site2, api=member).status_code == 403
    # Emplacement d'un site invisible proposé pour son propre site : introuvable.
    assert _code(_assign(world, 0, depot["id"], api=member)) == (404, "stock_location_not_found")
    _ok(_assign(world, 0, main["id"], api=member))
    levels = _levels(world, api=member)
    assert all(row["site_id"] == world.site for row in levels.values())


# --- Isolation entre entreprises (API, RLS, contraintes) --------------------------------------


def test_tenant_isolation_rls_and_composite_fk(
    world: World, provision: Any, api_for: Any, db: Session, owner_db: Session
) -> None:
    rayon = _location(world, "Rayon A")
    depot = _location(world, "Zone palettes", world.site2)
    _ok(_assign(world, 0, rayon["id"]))
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    assert _ok(other.get("/stock/locations"))["total"] == 0
    assert _code(other.patch(f"/stock/locations/{rayon['id']}", json={"name": "X"})) == (
        404,
        "stock_location_not_found",
    )
    category = _ok(other.post("/catalog/categories", json={"name": "Divers"}), 201)["id"]
    own = _ok(
        other.post(
            "/catalog/articles",
            json={
                "reference": "B-1",
                "designation": "B",
                "category_id": category,
                "unit": "u",
                "site_ids": [str(beta.site_id)],
            },
        ),
        201,
    )["id"]
    stolen = other.put(
        f"/stock/levels/{beta.site_id}/{own}/location", json={"location_id": rayon["id"]}
    )
    assert _code(stolen) == (404, "stock_location_not_found")
    # Un nom identique dans une autre entreprise est indépendant.
    assert (
        other.post(
            "/stock/locations", json={"site_id": str(beta.site_id), "name": "Rayon A"}
        ).status_code
        == 201
    )
    # SQL, rôle applicatif : RLS (lecture et écriture) ; droits minimaux.
    set_db_context(db, tenant_id=beta.tenant_id)
    for table in ("stock_locations", "stock_article_locations"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == (
            1 if table == "stock_locations" else 0
        )
    alpha = owner_db.execute(
        text("SELECT tenant_id FROM stock_locations WHERE id = :id"), {"id": rayon["id"]}
    ).scalar_one()
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO stock_locations (id, tenant_id, site_id, name, is_active) "
                "VALUES (gen_random_uuid(), :t, :s, 'Pirate', true)"
            ),
            {"t": alpha, "s": world.site},
        )
    db.rollback()
    set_db_context(db, tenant_id=alpha)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("DELETE FROM stock_locations WHERE id = :id"), {"id": rayon["id"]})
    db.rollback()
    set_db_context(db, tenant_id=alpha)
    # FK composite (tenant, site, emplacement) : l'emplacement d'un autre site est
    # inaffectable EN BASE, même en contournant le service.
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "UPDATE stock_article_locations SET location_id = :l "
                "WHERE site_id = :s AND article_id = :a"
            ),
            {"l": depot["id"], "s": world.site, "a": world.articles[0]},
        )
    db.rollback()
    for table in ("stock_locations", "stock_article_locations"):
        flags = owner_db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
        assert tuple(flags) == (True, True)
    privileges = owner_db.execute(
        text(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name IN ('stock_locations', 'stock_article_locations') "
            "AND grantee = 'stockmanager_app' ORDER BY 1, 2"
        )
    ).all()
    assert [tuple(p) for p in privileges] == [
        ("stock_article_locations", "DELETE"),
        ("stock_article_locations", "INSERT"),
        ("stock_article_locations", "SELECT"),
        ("stock_locations", "INSERT"),
        ("stock_locations", "SELECT"),
    ]
