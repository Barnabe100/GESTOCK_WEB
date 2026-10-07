"""Recette, étape 1 — assortiment par site (ADR-0046, D1 à D6), palier 1 : modèle, service, API.

CATALOGUE TENANT ≠ ASSORTIMENT SITE ≠ STOCK SITE.

- Nouveau site : assortiment vide, aucun niveau de stock ; nouvel article : aucun site.
- Ajout / réactivation (même ligne), idempotent, articles actifs seulement ; audit.
- Retrait (désactivation, tout ou rien) : refusé si stock sur CE site, ou document ouvert du site
  (brouillons de vente, d'entrée, de sortie, de transfert source ou destination, inventaire).
- Copie (ajout seulement, réactivation, catégorie facultative ; ni stock, ni seuils, ni lots).
- ``site_ids`` à la création d'un article : vide par défaut, permission sur chaque site, tout ou
  rien.
- ``catalog.assortment.manage`` (nature ``admin``) par site : Administrateur et Gestionnaire,
  pas le Vendeur ni le Consultant ; site en attente d'activation préparable.
- RLS, FK composites, droits SQL ; isolation API ; concurrence (aucun doublon).
"""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, add_site, login
from tests.stock_helpers import World

MANAGE = "catalog.assortment.manage"


@pytest.fixture
def world(bare_world: World) -> World:
    """Articles au catalogue seulement : chaque test construit l'assortiment qu'il vérifie."""
    return bare_world


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def _add(w: World, site: str, indexes: list[int], api: Api | None = None) -> Any:
    return (api or w.owner).post(
        f"/catalog/sites/{site}/articles", json={"article_ids": [w.articles[i] for i in indexes]}
    )


def _remove(w: World, site: str, indexes: list[int], api: Api | None = None) -> Any:
    return (api or w.owner).post(
        f"/catalog/sites/{site}/articles/remove",
        json={"article_ids": [w.articles[i] for i in indexes]},
    )


def _site_articles(w: World, site: str, **params: Any) -> dict[str, str]:
    page = _ok(w.owner.get(f"/catalog/sites/{site}/articles", params={"limit": 100, **params}))
    return {item["reference"]: item["state"] for item in page["items"]}


def _rows(owner_db: Session, site: str) -> dict[str, Any]:
    owner_db.expire_all()
    return {
        r.reference: r
        for r in owner_db.execute(
            text(
                "SELECT a.reference, s.id, s.is_active, s.added_by, s.added_at, s.removed_at,"
                " s.removed_by FROM catalog_site_articles s"
                " JOIN catalog_articles a ON a.id = s.article_id WHERE s.site_id = :s"
            ),
            {"s": site},
        )
    }


def _login_member(client: TestClient, email: str) -> Api:
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _audit(w: World, action: str) -> list[dict[str, Any]]:
    items = _ok(w.owner.get("/audit-logs", params={"action": action, "limit": 100}))["items"]
    return list(reversed(items))


def _article(w: World, reference: str, **extra: Any) -> Any:
    category = _ok(w.owner.get("/catalog/articles", params={"limit": 1}))["items"][0]
    return w.owner.post(
        "/catalog/articles",
        json={
            "reference": reference,
            "designation": f"Article {reference}",
            "category_id": category["category_id"],
            "unit": "u",
            **extra,
        },
    )


# --- Catalogue ≠ assortiment ≠ stock --------------------------------------------------------------


def test_new_site_and_new_article_start_without_assortment(world: World, owner_db: Session) -> None:
    site3 = add_site(world.owner, "Boutique 3", "B3").json()["id"]
    assert _site_articles(world, site3) == {}
    assert sh.count(owner_db, f"SELECT count(*) FROM stock_levels WHERE site_id = '{site3}'") == 0
    created = _ok(_article(world, "NEW-1"), 201)
    assert (
        sh.count(
            owner_db,
            f"SELECT count(*) FROM catalog_site_articles WHERE article_id = '{created['id']}'",
        )
        == 0
    )
    sites = _ok(world.owner.get(f"/catalog/articles/{created['id']}/sites"))
    assert {s["state"] for s in sites} == {"none"}
    # Ajouter à l'assortiment ne crée AUCUN niveau de stock.
    _ok(world.owner.post(f"/catalog/sites/{site3}/articles", json={"article_ids": [created["id"]]}))
    assert sh.count(owner_db, f"SELECT count(*) FROM stock_levels WHERE site_id = '{site3}'") == 0


# --- Ajout, réactivation --------------------------------------------------------------------------


def test_add_is_idempotent_and_reactivation_reuses_the_same_row(
    world: World, owner_db: Session
) -> None:
    assert _ok(_add(world, world.site, [0, 1])) == {"added": 2, "reactivated": 0, "unchanged": 0}
    assert _ok(_add(world, world.site, [0, 1])) == {"added": 0, "reactivated": 0, "unchanged": 2}
    first = _rows(owner_db, world.site)["A-0"]
    assert first.is_active and first.added_by is not None and first.removed_at is None
    assert _ok(_remove(world, world.site, [0])) == {"removed": 1, "unchanged": 0}
    removed = _rows(owner_db, world.site)["A-0"]
    assert (removed.is_active, removed.removed_at is not None, removed.removed_by) == (
        False,
        True,
        first.added_by,
    )
    assert _site_articles(world, world.site) == {"A-1": "active"}
    assert _site_articles(world, world.site, status="removed") == {"A-0": "removed"}
    assert _ok(_add(world, world.site, [0])) == {"added": 0, "reactivated": 1, "unchanged": 0}
    again = _rows(owner_db, world.site)["A-0"]
    assert again.id == first.id  # même ligne (D4)
    assert again.is_active and again.removed_at is None and again.removed_by is None
    assert again.added_at > first.added_at
    added = _audit(world, "site_assortment.added")
    assert [(a["data"]["reference"], a["data"]["reactivated"]) for a in added] == [
        ("A-0", False),
        ("A-1", False),
        ("A-0", True),
    ]
    assert all(a["site_id"] == world.site for a in added)
    assert [a["data"]["reference"] for a in _audit(world, "site_assortment.removed")] == ["A-0"]


def test_add_refuses_unknown_or_inactive_articles_atomically(world: World) -> None:
    unknown = world.owner.post(
        f"/catalog/sites/{world.site}/articles",
        json={"article_ids": [world.articles[0], str(uuid.uuid4())]},
    )
    assert _code(unknown) == (404, "article_not_found")
    _ok(world.owner.post(f"/catalog/articles/{world.articles[2]}/deactivate"))
    inactive = _add(world, world.site, [0, 2])
    assert _code(inactive) == (422, "article_inactive")
    assert inactive.json()["articles"] == ["A-2"]
    assert _site_articles(world, world.site, status="all") == {}


def test_lists_states_and_filters(world: World) -> None:
    other = _ok(world.owner.post("/catalog/categories", json={"name": "Boissons"}), 201)
    drink = _ok(_article(world, "COLA", category_id=other["id"]), 201)
    _ok(_add(world, world.site, [0, 1]))
    _ok(
        world.owner.post(
            f"/catalog/sites/{world.site}/articles", json={"article_ids": [drink["id"]]}
        )
    )
    _ok(_remove(world, world.site, [1]))
    assert _site_articles(world, world.site) == {"A-0": "active", "COLA": "active"}
    assert _site_articles(world, world.site, status="all") == {
        "A-0": "active",
        "A-1": "removed",
        "COLA": "active",
    }
    assert _site_articles(world, world.site, category_id=other["id"]) == {"COLA": "active"}
    assert _site_articles(world, world.site, search="cola") == {"COLA": "active"}
    # Catalogue global avec l'état d'assortiment du site demandé ; sans site : champ absent.
    page = _ok(world.owner.get("/catalog/articles", params={"site_id": world.site, "limit": 50}))
    states = {a["reference"]: a["site_assortment"] for a in page["items"]}
    assert states == {"A-0": "active", "A-1": "removed", "A-2": "none", "COLA": "active"}
    plain = _ok(world.owner.get("/catalog/articles", params={"limit": 50}))["items"]
    assert all("site_assortment" not in a for a in plain)
    sites = {
        s["site_name"]: s["state"]
        for s in _ok(world.owner.get(f"/catalog/articles/{world.articles[1]}/sites"))
    }
    assert sites == {"Dépôt": "none", "Site principal": "removed"}


# --- Retrait (D4) ---------------------------------------------------------------------------------


def test_removal_refused_with_stock_on_this_site_only(world: World) -> None:
    _ok(_add(world, world.site, [0]))
    _ok(_add(world, world.site2, [0]))
    entry = sh.validated_entry(world, [(0, "5", "100")])
    refused = _remove(world, world.site, [0])
    assert _code(refused) == (409, "article_has_stock")
    assert refused.json()["blocked"] == [{"reference": "A-0", "reason": "stock", "documents": []}]
    # Le stock d'un AUTRE site ne bloque pas : chaque assortiment est indépendant.
    assert _ok(_remove(world, world.site2, [0])) == {"removed": 1, "unchanged": 0}
    # Stock revenu à zéro (annulation de la réception) : retrait accepté.
    _ok(world.owner.post(f"/stock/entries/{entry['id']}/cancel", json={"reason": "Erreur"}))
    assert _ok(_remove(world, world.site, [0])) == {"removed": 1, "unchanged": 0}


def test_removal_refused_by_open_documents_all_or_nothing(world: World) -> None:
    _ok(_add(world, world.site, [0, 1, 2]))
    _ok(_add(world, world.site2, [0, 1, 2]))
    entry = sh.entry(world, [(1, "2", "100")])  # brouillon d'entrée
    blocked = _remove(world, world.site, [0, 1])
    assert _code(blocked) == (409, "article_in_open_documents")
    assert blocked.json()["blocked"] == [
        {"reference": "A-1", "reason": "open_document", "documents": [entry["number"]]}
    ]
    # Tout ou rien : A-0, libre, n'a pas été retiré.
    assert _site_articles(world, world.site)["A-0"] == "active"
    # Brouillon de transfert : bloque la SOURCE comme la DESTINATION.
    transfer = _ok(
        world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site2,
                "destination_site_id": world.site,
                "lines": [{"article_id": world.articles[2], "quantity": "1"}],
            },
        ),
        201,
    )
    for site in (world.site, world.site2):
        refused = _remove(world, site, [2])
        assert _code(refused) == (409, "article_in_open_documents")
        assert refused.json()["documents"] == [transfer["number"]]
    # Brouillon de vente, brouillon de sortie, inventaire ouvert du site.
    _ok(
        world.owner.post(
            "/sales",
            json={
                "site_id": world.site,
                "lines": [{"article_id": world.articles[0], "quantity": "1"}],
            },
        ),
        201,
    )
    sale_blocked = _remove(world, world.site, [0])
    assert _code(sale_blocked) == (409, "article_in_open_documents")
    assert sale_blocked.json()["documents"][0].startswith("VENTE-")
    world_exit = sh.exit_doc(world, [(1, "1")])
    inventory = _ok(
        world.owner.post(
            "/inventories",
            json={
                "site_id": world.site2,
                "inventory_type": "TARGETED",
                "article_ids": [world.articles[1]],
            },
        ),
        201,
    )
    exit_blocked = _remove(world, world.site, [1])
    assert set(exit_blocked.json()["documents"]) == {entry["number"], world_exit["number"]}
    inventory_blocked = _remove(world, world.site2, [1])
    assert _code(inventory_blocked) == (409, "article_in_open_documents")
    assert inventory_blocked.json()["documents"] == [inventory["number"]]
    # Un article absent de l'assortiment : retrait sans effet (inchangé).
    _ok(world.owner.post(f"/catalog/articles/{world.articles[2]}/deactivate"))
    assert _ok(_remove(world, world.site2, [0])) == {"removed": 1, "unchanged": 0}
    assert _ok(_remove(world, world.site2, [0])) == {"removed": 0, "unchanged": 1}


# --- Copie (D6) -----------------------------------------------------------------------------------


def test_copy_adds_only_and_never_copies_stock_thresholds_or_locations(
    world: World, owner_db: Session
) -> None:
    other = _ok(world.owner.post("/catalog/categories", json={"name": "Boissons"}), 201)
    drink = _ok(_article(world, "COLA", category_id=other["id"]), 201)
    _ok(_add(world, world.site, [0, 1]))
    _ok(
        world.owner.post(
            f"/catalog/sites/{world.site}/articles", json={"article_ids": [drink["id"]]}
        )
    )
    sh.validated_entry(world, [(0, "5", "100")])
    _ok(
        world.owner.put(
            f"/stock/levels/{world.site}/{world.articles[1]}/thresholds",
            json={"min_stock": "3", "max_stock": "9"},
        )
    )
    # Destination : A-1 retiré (réactivé par la copie), A-2 présent (jamais retiré).
    _ok(_add(world, world.site2, [1, 2]))
    _ok(_remove(world, world.site2, [1]))
    copied = _ok(
        world.owner.post(
            f"/catalog/sites/{world.site2}/articles/copy", json={"source_site_id": world.site}
        )
    )
    assert copied == {"added": 2, "reactivated": 1, "unchanged": 0}
    assert _site_articles(world, world.site2) == {
        "A-0": "active",
        "A-1": "active",
        "A-2": "active",
        "COLA": "active",
    }
    assert (
        sh.count(owner_db, f"SELECT count(*) FROM stock_levels WHERE site_id = '{world.site2}'")
        == 0
    )
    summary = _audit(world, "site_assortment.copied")[-1]
    assert summary["data"] == {
        "source_site_id": world.site,
        "category_id": None,
        "added": 2,
        "reactivated": 1,
        "unchanged": 0,
    }
    # Filtre de catégorie ; même site : refus ; idempotent.
    site3 = add_site(world.owner, "Boutique 3", "B3").json()["id"]
    by_category = world.owner.post(
        f"/catalog/sites/{site3}/articles/copy",
        json={"source_site_id": world.site, "category_id": other["id"]},
    )
    assert _ok(by_category) == {"added": 1, "reactivated": 0, "unchanged": 0}
    assert _site_articles(world, site3) == {"COLA": "active"}
    same = world.owner.post(
        f"/catalog/sites/{world.site}/articles/copy", json={"source_site_id": world.site}
    )
    assert _code(same) == (422, "assortment_copy_same_site")
    assert _ok(
        world.owner.post(
            f"/catalog/sites/{world.site2}/articles/copy", json={"source_site_id": world.site}
        )
    ) == {"added": 0, "reactivated": 0, "unchanged": 3}


# --- Création d'un article avec ses sites (D6) ----------------------------------------------------


def test_create_article_with_sites_is_all_or_nothing(
    world: World, client: TestClient, owner_db: Session
) -> None:
    created = _ok(_article(world, "SITES-1", site_ids=[world.site, world.site2]), 201)
    assert {
        s["state"] for s in _ok(world.owner.get(f"/catalog/articles/{created['id']}/sites"))
    } == {"active"}
    assert _audit(world, "article.created")[-1]["data"]["reference"] == "SITES-1"
    added = [a for a in _audit(world, "site_assortment.added") if a["entity_id"] == created["id"]]
    assert sorted(a["site_id"] for a in added) == sorted([world.site, world.site2])
    # Gestionnaire limité au site principal : un site hors de sa portée refuse TOUT.
    manager = sh.member(
        world,
        client,
        "gest@alpha.example.com",
        "manager",
        site_ids=[world.site],
    )
    refused = manager.post(
        "/catalog/articles",
        json={
            "reference": "SITES-2",
            "designation": "Refusé",
            "category_id": created["category_id"],
            "unit": "u",
            "site_ids": [world.site, world.site2],
        },
    )
    assert _code(refused) == (403, "site_access_denied")
    assert (
        sh.count(owner_db, "SELECT count(*) FROM catalog_articles WHERE reference = 'SITES-2'") == 0
    )


# --- Permissions (D5) -----------------------------------------------------------------------------


def test_permission_by_role_and_by_site(world: World, client: TestClient) -> None:
    for template, email in (
        ("seller", "vend@alpha.example.com"),
        ("viewer", "cons@alpha.example.com"),
    ):
        api = sh.member(world, client, email, template, all_sites=True)
        assert _code(_add(world, world.site, [0], api)) == (403, "permission_denied")
        # Consultation de l'assortiment : permission de lecture du catalogue.
        assert api.get(f"/catalog/sites/{world.site}/articles").status_code == 200
    manager = sh.member(world, client, "gest@alpha.example.com", "manager", all_sites=True)
    assert _ok(_add(world, world.site, [0], manager)) == {
        "added": 1,
        "reactivated": 0,
        "unchanged": 0,
    }
    # Rôle limité à un site : la permission ne vaut que sur ce site.
    roles = {r["template_code"]: r["id"] for r in world.owner.get("/roles").json()}
    scoped = _ok(
        world.owner.post(
            "/members",
            json={
                "email": "local@alpha.example.com",
                "full_name": "Local",
                "password": "Provisoire-123",
                "roles": [
                    {"role_id": roles["manager"], "site_id": world.site2},
                    {"role_id": roles["seller"], "site_id": world.site},
                ],
                "all_sites": True,
            },
        ),
        201,
    )
    assert scoped["email"] == "local@alpha.example.com"
    # Un rôle limité à un site ne vaut que sur ce site, sélectionné (``X-Site-Id``).
    local = _login_member(client, "local@alpha.example.com")
    on_depot = Api(local.client, local.token, uuid.UUID(world.site2))
    on_main = Api(local.client, local.token, uuid.UUID(world.site))
    assert _ok(_add(world, world.site2, [1], on_depot))["added"] == 1
    assert _code(_add(world, world.site, [1], on_main)) == (403, "permission_denied")
    assert _code(_add(world, world.site, [1], on_depot)) == (403, "site_mismatch")
    # Site sélectionné (X-Site-Id) : l'opération est bornée à ce site.
    selected = Api(world.owner.client, world.owner.token, uuid.UUID(world.site))
    assert _code(_add(world, world.site2, [2], selected)) == (403, "site_mismatch")


def test_pending_activation_site_can_be_prepared_but_not_operated(
    world: World, owner_db: Session
) -> None:
    pending = add_site(world.owner, "Futur magasin", "FUTUR", active=False).json()["id"]
    assert _ok(_add(world, pending, [0, 1])) == {"added": 2, "reactivated": 0, "unchanged": 0}
    assert _ok(
        world.owner.post(
            f"/catalog/sites/{pending}/articles/copy", json={"source_site_id": world.site}
        )
    ) == {"added": 0, "reactivated": 0, "unchanged": 0}
    # Préparer l'assortiment ne permet aucune opération métier (nature ``write``).
    response = world.owner.post(
        "/stock/entries",
        json={
            "site_id": pending,
            "supplier_id": world.supplier,
            "lines": [{"article_id": world.articles[0], "quantity": "1", "unit_cost": "10"}],
        },
    )
    assert _code(response) == (403, "subscription_restricted")
    assert sh.count(owner_db, f"SELECT count(*) FROM stock_levels WHERE site_id = '{pending}'") == 0


# --- Sécurité : RLS, FK composites, droits SQL, isolation API -------------------------------------


def test_rls_composite_fks_and_sql_privileges(
    world: World, provision: Any, api_for: Any, db: Session, owner_db: Session
) -> None:
    _ok(_add(world, world.site, [0]))
    alpha = owner_db.execute(
        text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": world.site}
    ).scalar_one()
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    set_db_context(db, tenant_id=beta.tenant_id)
    assert db.execute(text("SELECT count(*) FROM catalog_site_articles")).scalar_one() == 0
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO catalog_site_articles (id, tenant_id, site_id, article_id, is_active)"
                " VALUES (gen_random_uuid(), :t, :s, :a, true)"
            ),
            {"t": alpha, "s": world.site, "a": world.articles[1]},
        )
    db.rollback()
    # Site ou article d'un autre tenant : FK composites.
    beta_owner = api_for("owner@beta.example.com")
    beta_category = _ok(beta_owner.post("/catalog/categories", json={"name": "B"}), 201)
    beta_article = _ok(
        beta_owner.post(
            "/catalog/articles",
            json={
                "reference": "B-1",
                "designation": "Beta",
                "category_id": beta_category["id"],
                "unit": "u",
            },
        ),
        201,
    )
    set_db_context(db, tenant_id=alpha)
    for site, article in ((str(beta.site_id), world.articles[1]), (world.site, beta_article["id"])):
        with pytest.raises(DBAPIError, match="foreign key"):
            db.execute(
                text(
                    "INSERT INTO catalog_site_articles (id, tenant_id, site_id, article_id,"
                    " is_active) VALUES (gen_random_uuid(), :t, :s, :a, true)"
                ),
                {"t": alpha, "s": site, "a": article},
            )
        db.rollback()
        set_db_context(db, tenant_id=alpha)
    # Jamais de suppression par le rôle applicatif ; aucun droit pour la console.
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("DELETE FROM catalog_site_articles"))
    db.rollback()
    owner_db.expire_all()
    assert owner_db.execute(
        text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class"
            " WHERE relname = 'catalog_site_articles'"
        )
    ).scalar_one()
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert not owner_db.execute(
            text(
                "SELECT has_table_privilege('stockmanager_platform', 'catalog_site_articles',"
                f" '{privilege}')"
            )
        ).scalar_one()
    assert not owner_db.execute(
        text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'stockmanager_app'")
    ).scalar_one()
    # API : site d'un autre tenant inaccessible, article d'un autre tenant introuvable.
    assert _code(beta_owner.get(f"/catalog/sites/{world.site}/articles")) == (
        403,
        "site_access_denied",
    )
    foreign = world.owner.post(
        f"/catalog/sites/{world.site}/articles", json={"article_ids": [beta_article["id"]]}
    )
    assert _code(foreign) == (404, "article_not_found")
    assert _code(
        world.owner.post(
            f"/catalog/sites/{beta.site_id}/articles", json={"article_ids": [world.articles[0]]}
        )
    ) == (403, "site_access_denied")


# --- Concurrence ----------------------------------------------------------------------------------


def test_concurrent_adds_never_duplicate(world: World, app: Any, owner_db: Session) -> None:
    barrier = threading.Barrier(2)
    results: list[Any] = []

    def run() -> None:
        with TestClient(app) as client:
            barrier.wait()
            results.append(
                Api(client, world.owner.token)
                .post(
                    f"/catalog/sites/{world.site}/articles",
                    json={"article_ids": [world.articles[0]]},
                )
                .json()
            )

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted((r["added"], r["unchanged"]) for r in results) == [(0, 1), (1, 0)]
    assert (
        sh.count(
            owner_db,
            f"SELECT count(*) FROM catalog_site_articles WHERE site_id = '{world.site}'",
        )
        == 1
    )


def test_catalog_filter_on_site_assortment(world: World) -> None:
    """Palier 3 : la liste du catalogue filtre sur l'état d'assortiment d'un site (dialogue
    « Ajouter à l'assortiment » : articles pas encore actifs sur le site)."""
    _ok(_add(world, world.site, [0, 1]))
    _ok(_remove(world, world.site, [1]))

    def refs(**params: Any) -> list[str]:
        page = _ok(world.owner.get("/catalog/articles", params={"limit": 50, **params}))
        return sorted(a["reference"] for a in page["items"])

    assert refs(site_id=world.site, in_site_assortment="true") == ["A-0"]
    assert refs(site_id=world.site, in_site_assortment="false") == ["A-1", "A-2"]
    assert refs(site_id=world.site2, in_site_assortment="false") == ["A-0", "A-1", "A-2"]
    missing = world.owner.get("/catalog/articles", params={"in_site_assortment": "true"})
    assert _code(missing) == (422, "site_required")
    foreign = world.owner.get(
        "/catalog/articles", params={"site_id": str(uuid.uuid4()), "in_site_assortment": "true"}
    )
    assert foreign.status_code in (403, 404)
