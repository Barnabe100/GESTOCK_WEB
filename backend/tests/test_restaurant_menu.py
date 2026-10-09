"""Palier R1 (ADR-0049, ``RESTAURANT.md`` §4 et §14) — menu des sites de restauration.

Menu par site limité à l'assortiment ACTIF ; présentation (unité de base ou conditionnement) au
plus une fois par site, garantie en base (``NULLS NOT DISTINCT``) ; conditionnement sans prix
refusé ; « épuisé » manuel ; état « commandable » calculé depuis l'état COURANT du catalogue ;
prix du catalogue, aucun coût ; audit ; isolation des sites (lecture limitée aux sites où le
menu est effectif, écritures revérifiées) et des entreprises (API, RLS, FK composites) ;
activation sans surprise (sites existants désactivés, dépendants planifiés inertes)."""

import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from tests.conftest import Api, add_site, set_site_module
from tests.stock_helpers import member
from tests.test_site_profile_change import _apply

MAQUIS = "restaurant.maquis"
SECTIONS = "/restaurant/menu/sections"
ITEMS = "/restaurant/menu/items"


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def _article(api: Api, category: str, ref: str, price: str, sites: list[str]) -> str:
    body = {
        "reference": ref,
        "designation": f"Produit {ref}",
        "category_id": category,
        "unit": "bouteille",
        "barcode": f"BC-{ref}",
        "purchase_price": "100",
        "sale_price": price,
        "site_ids": sites,
    }
    return str(_ok(api.post("/catalog/articles", json=body), 201)["id"])


def _packaging(api: Api, article: str, name: str, conversion: str, price: str | None) -> str:
    body = {"name": name, "conversion": conversion, "sale_price": price}
    return str(_ok(api.post(f"/catalog/articles/{article}/packagings", json=body), 201)["id"])


@pytest.fixture
def resto(provision: Any, api_for: Any) -> SimpleNamespace:
    """Maquis ENTREPRISE, deux sites (menu activé par défaut sur chacun, créés après la
    livraison) ; articles dans l'assortiment des deux sites sauf ``only_b`` (site B seul)."""
    t = provision("menu-r1", profile=MAQUIS, plan="ENTREPRISE")
    owner: Api = api_for("owner@menu-r1.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER"), 201)["id"]
    category = _ok(owner.post("/catalog/categories", json={"name": "Boissons"}), 201)["id"]
    coca = _article(owner, category, "COCA", "500", [a, b])
    plat = _article(owner, category, "PLAT", "2500", [a, b])
    only_b = _article(owner, category, "BISSAP", "300", [b])
    crate = _packaging(owner, coca, "Casier 24", "24", "11000")
    unpriced = _packaging(owner, coca, "Pack 6", "6", None)
    return SimpleNamespace(
        tenant=t.tenant_id,
        owner=owner,
        a=a,
        b=b,
        category=category,
        coca=coca,
        plat=plat,
        only_b=only_b,
        crate=crate,
        unpriced=unpriced,
    )


def _section(r: SimpleNamespace, name: str, site: str | None = None, api: Api | None = None) -> Any:
    return (api or r.owner).post(SECTIONS, json={"site_id": site or r.a, "name": name})


def _item(
    r: SimpleNamespace,
    section: str,
    article: str,
    packaging: str | None = None,
    site: str | None = None,
    api: Api | None = None,
    **extra: Any,
) -> Any:
    body = {
        "site_id": site or r.a,
        "section_id": section,
        "article_id": article,
        "packaging_id": packaging,
        **extra,
    }
    return (api or r.owner).post(ITEMS, json=body)


def _items(api: Api, **params: Any) -> list[dict[str, Any]]:
    return list(_ok(api.get(ITEMS, params={"limit": 100, **params}))["items"])


def _audit(r: SimpleNamespace, action: str) -> list[dict[str, Any]]:
    items = _ok(r.owner.get("/audit-logs", params={"action": action, "limit": 50}))["items"]
    return list(reversed(items))


def _selected(api: Api, site: str) -> Api:
    return Api(api.client, api.token, uuid.UUID(site))


# --- Sections -----------------------------------------------------------------------------------


def test_sections_are_per_site_unique_case_insensitive_and_audited(resto: SimpleNamespace) -> None:
    r = resto
    boissons = _ok(_section(r, "  Boissons  "), 201)
    assert (boissons["name"], boissons["site_id"], boissons["is_active"]) == (
        "Boissons",
        r.a,
        True,
    )
    assert boissons["item_count"] == 0
    assert _code(_section(r, "boissons")) == (409, "menu_section_name_taken")
    # Le même nom sur un autre site est une AUTRE section.
    assert _ok(_section(r, "Boissons", r.b), 201)["site_id"] == r.b
    plats = _ok(_section(r, "Plats"), 201)
    renamed = r.owner.put(f"{SECTIONS}/{plats['id']}", json={"name": "BOISSONS", "sort_order": 1})
    assert _code(renamed) == (409, "menu_section_name_taken")
    updated = _ok(
        r.owner.put(f"{SECTIONS}/{plats['id']}", json={"name": "Grillades", "sort_order": 2})
    )
    assert (updated["name"], updated["sort_order"]) == ("Grillades", 2)
    off = _ok(r.owner.post(f"{SECTIONS}/{plats['id']}/deactivate"))
    assert off["is_active"] is False
    assert _ok(r.owner.post(f"{SECTIONS}/{plats['id']}/activate"))["is_active"] is True
    # Ordre d'affichage : position, puis nom.
    listed = _ok(r.owner.get(SECTIONS, params={"site_id": r.a}))["items"]
    assert [s["name"] for s in listed] == ["Boissons", "Grillades"]
    assert [a["data"]["name"] for a in _audit(r, "restaurant_menu.section_created")] == [
        "Boissons",
        "Boissons",
        "Plats",
    ]
    change = _audit(r, "restaurant_menu.section_updated")[0]["data"]
    assert change["before"]["name"] == "Plats" and change["after"]["name"] == "Grillades"
    assert len(_audit(r, "restaurant_menu.section_deactivated")) == 1


def test_concurrent_sections_with_the_same_name_one_created_one_409(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    """L'unicité fait foi en base : deux créations simultanées (casse différente) donnent un
    succès et un conflit ``409`` — jamais deux sections."""
    r = resto
    results: list[tuple[int, str | None]] = []
    barrier = threading.Barrier(2)

    def create(name: str) -> None:
        barrier.wait()
        response = _section(r, name)
        results.append((response.status_code, response.json().get("code")))

    threads = [threading.Thread(target=create, args=(n,)) for n in ("Desserts", "DESSERTS")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results, key=lambda x: x[0]) == [
        (201, None),
        (409, "menu_section_name_taken"),
    ]
    count = owner_db.execute(
        text(
            "SELECT count(*) FROM restaurant_menu_sections "
            "WHERE site_id = :s AND lower(name) = 'desserts'"
        ),
        {"s": r.a},
    ).scalar_one()
    assert count == 1


# --- Éléments : présentations et contrôles du serveur -------------------------------------------


def test_items_one_presentation_per_site_base_unit_and_packaging(resto: SimpleNamespace) -> None:
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    base = _ok(_item(r, section, r.coca, display_name="Coca 33 cl"), 201)
    assert (base["packaging_id"], base["price"], base["orderable"], base["blockers"]) == (
        None,
        "500.00",
        True,
        [],
    )
    crate = _ok(_item(r, section, r.coca, r.crate), 201)
    assert (crate["packaging_name"], crate["conversion"], crate["price"]) == (
        "Casier 24",
        "24.000",
        "11000.00",
    )
    # Une présentation au plus une fois par site, y compris l'unité de base (conditionnement nul).
    duplicate = _item(r, section, r.coca)
    assert _code(duplicate) == (409, "menu_item_exists")
    assert duplicate.json()["id"] == base["id"]
    assert _code(_item(r, section, r.coca, r.crate)) == (409, "menu_item_exists")
    # Même présentation sur un autre site : un autre élément.
    other = _ok(_section(r, "Boissons", r.b), 201)["id"]
    assert _ok(_item(r, other, r.coca, site=r.b), 201)["site_id"] == r.b
    section_row = _ok(r.owner.get(f"{SECTIONS}/{section}"))
    assert section_row["item_count"] == 2
    created = _audit(r, "restaurant_menu.item_created")
    assert [a["data"]["reference"] for a in created] == ["COCA", "COCA", "COCA"]


def test_concurrent_items_with_the_same_presentation_one_created_one_409(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    section = _ok(_section(r, "Plats"), 201)["id"]
    codes: list[int] = []
    barrier = threading.Barrier(2)

    def create() -> None:
        barrier.wait()
        codes.append(_item(r, section, r.plat).status_code)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(codes) == [201, 409]
    count = owner_db.execute(
        text(
            "SELECT count(*) FROM restaurant_menu_items "
            "WHERE site_id = :s AND article_id = :a AND packaging_id IS NULL"
        ),
        {"s": r.a, "a": r.plat},
    ).scalar_one()
    assert count == 1


def test_item_creation_is_checked_by_the_server(resto: SimpleNamespace) -> None:
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    on_b = _ok(_section(r, "Boissons", r.b), 201)["id"]
    # Hors assortiment ACTIF du site (jamais d'ajout automatique).
    refused = _item(r, section, r.only_b)
    assert _code(refused) == (422, "article_not_in_site_assortment")
    # Conditionnement sans prix configuré : invendable.
    assert _code(_item(r, section, r.coca, r.unpriced)) == (422, "packaging_price_not_set")
    # Conditionnement d'un autre article.
    assert _code(_item(r, section, r.plat, r.crate)) == (422, "packaging_not_found")
    # Section d'un autre site, section inconnue, article inconnu.
    assert _code(_item(r, on_b, r.coca)) == (404, "menu_section_not_found")
    assert _code(_item(r, str(uuid.uuid4()), r.coca)) == (404, "menu_section_not_found")
    assert _code(_item(r, section, str(uuid.uuid4()))) == (404, "article_not_found")
    # Section désactivée.
    _ok(r.owner.post(f"{SECTIONS}/{section}/deactivate"))
    assert _code(_item(r, section, r.coca)) == (422, "menu_section_inactive")
    _ok(r.owner.post(f"{SECTIONS}/{section}/activate"))
    # Article désactivé.
    _ok(r.owner.post(f"/catalog/articles/{r.plat}/deactivate"))
    assert _code(_item(r, section, r.plat)) == (422, "article_inactive")
    # Conditionnement désactivé.
    _ok(r.owner.post(f"/catalog/packagings/{r.crate}/deactivate"))
    assert _code(_item(r, section, r.coca, r.crate)) == (422, "packaging_inactive")
    # Champs inconnus ou de décision ignorés (prix, statut, disponibilité : jamais du client).
    created = _ok(
        _item(r, section, r.coca, price="1", orderable=False, available=False, is_active=False),
        201,
    )
    assert (created["price"], created["available"], created["is_active"]) == ("500.00", True, True)
    assert _audit(r, "restaurant_menu.item_created")[-1]["data"]["reference"] == "COCA"


def test_orderable_state_follows_the_current_catalog_and_assortment(
    resto: SimpleNamespace,
) -> None:
    """Le menu ne bloque jamais le catalogue : un retrait d'assortiment, un article ou un
    conditionnement désactivé rendent l'élément non commandable (motifs), sans erreur."""
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    base = _ok(_item(r, section, r.coca), 201)["id"]
    crate = _ok(_item(r, section, r.coca, r.crate), 201)["id"]

    def blockers(item: str) -> list[str]:
        return list(_ok(r.owner.get(f"{ITEMS}/{item}"))["blockers"])

    removed = r.owner.post(f"/catalog/sites/{r.a}/articles/remove", json={"article_ids": [r.coca]})
    assert removed.status_code == 200, removed.text
    assert blockers(base) == ["article_not_in_site_assortment"]
    # Réactivation d'un élément : contrôles refaits (hors assortiment : refus).
    _ok(r.owner.post(f"{ITEMS}/{base}/deactivate"))
    assert blockers(base) == ["item_inactive", "article_not_in_site_assortment"]
    assert _code(r.owner.post(f"{ITEMS}/{base}/activate")) == (
        422,
        "article_not_in_site_assortment",
    )
    _ok(r.owner.post(f"/catalog/sites/{r.a}/articles", json={"article_ids": [r.coca]}))
    activated = _ok(r.owner.post(f"{ITEMS}/{base}/activate"))
    assert (activated["is_active"], activated["orderable"]) == (True, True)
    # Conditionnement désactivé : élément conservé, non commandable, prix non exposé.
    _ok(r.owner.post(f"/catalog/packagings/{r.crate}/deactivate"))
    row = _ok(r.owner.get(f"{ITEMS}/{crate}"))
    assert (row["blockers"], row["price"], row["packaging_name"]) == (
        ["packaging_inactive"],
        None,
        None,
    )
    _ok(r.owner.post(f"/catalog/packagings/{r.crate}/activate"))
    # Section désactivée ; article désactivé.
    _ok(r.owner.post(f"{SECTIONS}/{section}/deactivate"))
    assert blockers(crate) == ["section_inactive"]
    _ok(r.owner.post(f"{SECTIONS}/{section}/activate"))
    _ok(r.owner.post(f"/catalog/articles/{r.coca}/deactivate"))
    assert blockers(base) == ["article_inactive"]
    # Prix : toujours celui du catalogue COURANT (aucun prix par site).
    _ok(r.owner.post(f"/catalog/articles/{r.coca}/activate"))
    _ok(r.owner.patch(f"/catalog/articles/{r.coca}", json={"sale_price": "650"}))
    assert _ok(r.owner.get(f"{ITEMS}/{base}"))["price"] == "650.00"


def test_update_item_keeps_its_presentation(resto: SimpleNamespace) -> None:
    r = resto
    boissons = _ok(_section(r, "Boissons"), 201)["id"]
    fraiches = _ok(_section(r, "Fraîches"), 201)["id"]
    on_b = _ok(_section(r, "Boissons", r.b), 201)["id"]
    item = _ok(_item(r, boissons, r.coca), 201)["id"]
    body = {"section_id": fraiches, "display_name": "Coca glacé", "description": "33 cl"}
    updated = _ok(r.owner.put(f"{ITEMS}/{item}", json={**body, "article_id": r.plat}))
    assert (updated["section_name"], updated["display_name"], updated["article_id"]) == (
        "Fraîches",
        "Coca glacé",
        r.coca,
    )
    # Section d'un autre site : refusée.
    moved = r.owner.put(f"{ITEMS}/{item}", json={**body, "section_id": on_b})
    assert _code(moved) == (404, "menu_section_not_found")
    change = _audit(r, "restaurant_menu.item_updated")[0]["data"]
    assert change["display_name"] == {"before": None, "after": "Coca glacé"}


# --- Épuisé, permissions, prix, recherche ------------------------------------------------------


def test_availability_permissions_and_audit(resto: SimpleNamespace, client: TestClient) -> None:
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    item = _ok(_item(r, section, r.coca), 201)["id"]
    owner_ns = SimpleNamespace(owner=r.owner)
    seller = member(owner_ns, client, "vendeur@menu-r1.example.com", "seller", all_sites=True)  # type: ignore[arg-type]
    viewer = member(owner_ns, client, "consultant@menu-r1.example.com", "viewer", all_sites=True)  # type: ignore[arg-type]
    manager = member(owner_ns, client, "gestion@menu-r1.example.com", "manager", all_sites=True)  # type: ignore[arg-type]
    # Vendeur : lit le menu et marque « épuisé » ; ne le configure pas.
    off = _ok(
        seller.put(f"{ITEMS}/{item}/availability", json={"available": False, "reason": "Rupture"})
    )
    assert (off["available"], off["unavailable_reason"], off["orderable"]) == (
        False,
        "Rupture",
        False,
    )
    assert off["blockers"] == ["unavailable"]
    assert _code(_section(r, "Desserts", api=seller)) == (403, "permission_denied")
    assert _code(seller.post(f"{ITEMS}/{item}/deactivate")) == (403, "permission_denied")
    # Consultant : lecture seule.
    assert _items(viewer, site_id=r.a)[0]["available"] is False
    denied = viewer.put(f"{ITEMS}/{item}/availability", json={"available": True})
    assert _code(denied) == (403, "permission_denied")
    # Gestionnaire : configuration complète.
    assert _section(r, "Desserts", api=manager).status_code == 201
    back = _ok(manager.put(f"{ITEMS}/{item}/availability", json={"available": True}))
    assert (back["available"], back["unavailable_reason"]) == (True, None)
    # Un motif n'est conservé que pour un élément épuisé ; rejouer ne journalise rien.
    _ok(r.owner.put(f"{ITEMS}/{item}/availability", json={"available": True, "reason": "x"}))
    changes = _audit(r, "restaurant_menu.item_availability_changed")
    assert [c["data"]["after"] for c in changes] == [
        {"available": False, "reason": "Rupture"},
        {"available": True, "reason": None},
    ]


def test_prices_come_from_the_catalog_and_no_cost_is_exposed(resto: SimpleNamespace) -> None:
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    item = _ok(_item(r, section, r.coca, r.crate), 201)
    forbidden = {"purchase_price", "cost", "unit_cost", "cmup", "average_cost"}
    assert not forbidden & set(item)
    for row in _items(r.owner):
        assert not forbidden & set(row)


def test_search_filters_and_sorting_are_server_side(resto: SimpleNamespace) -> None:
    r = resto
    boissons = _ok(_section(r, "Boissons", api=r.owner), 201)["id"]
    plats = _ok(_section(r, "Plats"), 201)["id"]
    _ok(r.owner.put(f"{SECTIONS}/{plats}", json={"name": "Plats", "sort_order": 0}))
    _ok(r.owner.put(f"{SECTIONS}/{boissons}", json={"name": "Boissons", "sort_order": 1}))
    coca = _ok(_item(r, boissons, r.coca, display_name="Coca"), 201)["id"]
    _ok(_item(r, boissons, r.coca, r.crate), 201)
    _ok(_item(r, plats, r.plat, display_name="Poulet braisé"), 201)
    # Position : sections (ordre), puis éléments.
    assert [i["section_name"] for i in _items(r.owner, site_id=r.a)] == [
        "Plats",
        "Boissons",
        "Boissons",
    ]
    assert [i["display_name"] for i in _items(r.owner, search="poulet")] == ["Poulet braisé"]
    # Recherche par référence et par code-barres (tous les codes de l'article, Lot 3-D).
    assert {i["reference"] for i in _items(r.owner, search="BC-COCA")} == {"COCA"}
    assert len(_items(r.owner, search="coca")) == 2
    assert len(_items(r.owner, section_id=plats)) == 1
    _ok(r.owner.put(f"{ITEMS}/{coca}/availability", json={"available": False}))
    assert [i["id"] for i in _items(r.owner, availability="unavailable")] == [coca]
    assert len(_items(r.owner, availability="available")) == 2
    _ok(r.owner.post(f"{ITEMS}/{coca}/deactivate"))
    assert [i["id"] for i in _items(r.owner, status="inactive")] == [coca]
    names = [i["designation"] for i in _items(r.owner, sort="reference")]
    assert names == ["Produit COCA", "Produit COCA", "Produit PLAT"]
    page = _ok(r.owner.get(ITEMS, params={"limit": 1, "offset": 1}))
    assert (page["total"], len(page["items"])) == (3, 1)
    assert r.owner.get(ITEMS, params={"sort": "price"}).status_code == 400


# --- Portée des sites -----------------------------------------------------------------------------


def test_reads_and_writes_are_limited_to_sites_where_the_menu_is_effective(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    on_a = _ok(_section(r, "Boissons"), 201)["id"]
    on_b = _ok(_section(r, "Boissons", r.b), 201)["id"]
    item_b = _ok(_item(r, on_b, r.coca, site=r.b), 201)["id"]
    _ok(_item(r, on_a, r.coca), 201)
    assert {s["site_id"] for s in _ok(r.owner.get(SECTIONS))["items"]} == {r.a, r.b}
    # Menu désactivé sur B : configuration conservée, mais plus lisible ni modifiable.
    assert set_site_module(r.owner, r.b, "restaurant.menu", False).status_code == 204
    # Site sélectionné sans menu effectif : le module est indisponible (lecture comprise).
    on_b_selected = _selected(r.owner, r.b)
    assert _code(on_b_selected.get(ITEMS)) == (403, "module_unavailable")
    assert _code(_section(r, "Plats", r.b, api=on_b_selected)) == (403, "module_unavailable")
    # Vue consolidée (aucun site sélectionné) : B filtré par le service.
    assert {s["site_id"] for s in _ok(r.owner.get(SECTIONS))["items"]} == {r.a}
    assert _ok(r.owner.get(ITEMS, params={"site_id": r.b}))["items"] == []
    assert _code(r.owner.get(f"{ITEMS}/{item_b}")) == (404, "menu_item_not_found")
    assert _code(r.owner.get(f"{SECTIONS}/{on_b}")) == (404, "menu_section_not_found")
    assert _code(_section(r, "Plats", r.b)) == (403, "permission_denied")
    unavailable = r.owner.put(f"{ITEMS}/{item_b}/availability", json={"available": False})
    assert _code(unavailable) == (404, "menu_item_not_found")
    kept = owner_db.execute(
        text("SELECT count(*) FROM restaurant_menu_items WHERE site_id = :s"), {"s": r.b}
    ).scalar_one()
    assert kept == 1
    # Réactivé : le menu de B réapparaît intact.
    assert set_site_module(r.owner, r.b, "restaurant.menu", True).status_code == 204
    assert _ok(r.owner.get(f"{ITEMS}/{item_b}"))["site_id"] == r.b
    # Site sélectionné A : une écriture visant B est refusée.
    on_a_selected = _selected(r.owner, r.a)
    assert _code(_section(r, "Plats", r.b, api=on_a_selected)) == (403, "site_mismatch")
    assert _code(on_a_selected.get(f"{ITEMS}/{item_b}")) == (403, "site_mismatch")
    assert {i["site_id"] for i in _items(on_a_selected)} == {r.a}


def test_member_limited_to_one_site_never_reaches_the_other(
    resto: SimpleNamespace, client: TestClient
) -> None:
    r = resto
    item_b = _ok(_item(r, _ok(_section(r, "Boissons", r.b), 201)["id"], r.coca, site=r.b), 201)
    _ok(_item(r, _ok(_section(r, "Boissons"), 201)["id"], r.coca), 201)
    owner_ns = SimpleNamespace(owner=r.owner)
    local = member(  # type: ignore[arg-type]
        owner_ns,
        client,
        "gestion-a@menu-r1.example.com",
        "manager",
        all_sites=False,
        site_ids=[r.a],
    )
    assert {i["site_id"] for i in _items(local)} == {r.a}
    assert _items(local, site_id=r.b) == []
    assert _code(local.get(f"{ITEMS}/{item_b['id']}")) == (404, "menu_item_not_found")
    refused = local.put(f"{ITEMS}/{item_b['id']}/availability", json={"available": False})
    assert _code(refused) == (404, "menu_item_not_found")
    assert _code(_section(r, "Plats", r.b, api=local)) == (403, "site_access_denied")
    # Recherche par code-barres : aucune fuite du site B.
    assert {i["site_id"] for i in _items(local, search="BC-COCA")} == {r.a}


def test_pending_site_can_be_configured_but_not_operated(resto: SimpleNamespace) -> None:
    """Abonnement du site en attente d'activation : configuration (nature ``admin``) permise,
    « épuisé » (nature ``write``) refusé pour CE site."""
    r = resto
    pending = _ok(add_site(r.owner, "Futur", "FUT", active=False), 201)["id"]
    _ok(r.owner.post(f"/catalog/sites/{pending}/articles", json={"article_ids": [r.coca]}))
    section = _ok(_section(r, "Boissons", pending), 201)["id"]
    item = _ok(_item(r, section, r.coca, site=pending), 201)["id"]
    refused = r.owner.put(f"{ITEMS}/{item}/availability", json={"available": False})
    assert _code(refused) == (403, "subscription_restricted")


def test_companies_are_isolated(resto: SimpleNamespace, provision: Any, api_for: Any) -> None:
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    item = _ok(_item(r, section, r.coca), 201)["id"]
    provision("menu-r1-b", profile=MAQUIS, plan="ENTREPRISE")
    other: Api = api_for("owner@menu-r1-b.example.com")
    assert _ok(other.get(SECTIONS))["items"] == []
    assert _ok(other.get(ITEMS, params={"search": "COCA"}))["items"] == []
    assert _code(other.get(f"{ITEMS}/{item}")) == (404, "menu_item_not_found")
    assert _code(other.get(f"{SECTIONS}/{section}")) == (404, "menu_section_not_found")
    assert _code(other.post(f"{SECTIONS}/{section}/deactivate")) == (404, "menu_section_not_found")
    # Site d'une autre entreprise : jamais utilisable.
    assert _code(_section(r, "Pirate", r.a, api=other)) == (403, "site_access_denied")


def test_rls_composite_fks_and_sql_privileges(
    resto: SimpleNamespace, provision: Any, db: Session, owner_db: Session
) -> None:
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    on_b = _ok(_section(r, "Boissons", r.b), 201)["id"]
    _ok(_item(r, section, r.coca), 201)
    other = provision("menu-r1-rls", profile=MAQUIS, plan="ENTREPRISE")
    # Aucun contexte : aucune ligne ; contexte d'une autre entreprise : aucune ligne de A.
    for table in ("restaurant_menu_sections", "restaurant_menu_items"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    set_db_context(db, tenant_id=other.tenant_id)
    for table in ("restaurant_menu_sections", "restaurant_menu_items"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    with pytest.raises(DBAPIError):
        db.execute(
            text(
                "INSERT INTO restaurant_menu_sections (id, tenant_id, site_id, name, sort_order, "
                "is_active) VALUES (gen_random_uuid(), :t, :s, 'Pirate', 0, true)"
            ),
            {"t": r.tenant, "s": r.a},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    with pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("DELETE FROM restaurant_menu_items"))
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    # FK composite : la section d'un autre site est inutilisable EN BASE.
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO restaurant_menu_items (id, tenant_id, site_id, section_id, "
                "article_id, sort_order, is_active, available) "
                "VALUES (gen_random_uuid(), :t, :s, :sec, :a, 0, true, true)"
            ),
            {"t": r.tenant, "s": r.a, "sec": on_b, "a": r.plat},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    # FK composite : le conditionnement doit être celui de l'article.
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO restaurant_menu_items (id, tenant_id, site_id, section_id, "
                "article_id, packaging_id, sort_order, is_active, available) "
                "VALUES (gen_random_uuid(), :t, :s, :sec, :a, :p, 0, true, true)"
            ),
            {"t": r.tenant, "s": r.a, "sec": section, "a": r.plat, "p": r.crate},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    # Unicité en base, unité de base comprise (conditionnement nul : NULLS NOT DISTINCT).
    with pytest.raises(IntegrityError, match="uq_restaurant_menu_items_site_presentation"):
        db.execute(
            text(
                "INSERT INTO restaurant_menu_items (id, tenant_id, site_id, section_id, "
                "article_id, sort_order, is_active, available) "
                "VALUES (gen_random_uuid(), :t, :s, :sec, :a, 0, true, true)"
            ),
            {"t": r.tenant, "s": r.a, "sec": section, "a": r.coca},
        )
    db.rollback()
    for table in ("restaurant_menu_sections", "restaurant_menu_items"):
        flags = owner_db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
        assert tuple(flags) == (True, True)
    privileges = owner_db.execute(
        text(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name LIKE 'restaurant_menu_%' AND grantee = 'stockmanager_app' "
            "ORDER BY 1, 2"
        )
    ).all()
    assert [tuple(p) for p in privileges] == [
        ("restaurant_menu_items", "INSERT"),
        ("restaurant_menu_items", "SELECT"),
        ("restaurant_menu_sections", "INSERT"),
        ("restaurant_menu_sections", "SELECT"),
    ]
    console = owner_db.execute(
        text(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_name LIKE 'restaurant_menu_%' AND grantee = 'stockmanager_platform'"
        )
    ).scalar_one()
    assert console == 0
    nulls_not_distinct = owner_db.execute(
        text(
            "SELECT i.indnullsnotdistinct FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = 'uq_restaurant_menu_items_site_presentation'"
        )
    ).scalar_one()
    assert nulls_not_distinct is True


# --- Activation sans surprise et dépendants planifiés (D10) ---------------------------------


def _modules(api: Api, site: str) -> dict[str, dict[str, Any]]:
    return {m["code"]: m for m in _ok(api.get(f"/sites/{site}/modules"))}


def _row(owner_db: Session, site: str, code: str) -> bool | None:
    owner_db.expire_all()
    value = owner_db.execute(
        text("SELECT enabled FROM site_modules WHERE site_id = :s AND module_code = :c"),
        {"s": site, "c": code},
    ).scalar_one_or_none()
    return None if value is None else bool(value)


def test_existing_site_state_menu_toggles_despite_inert_planned_orders(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    """État d'un site existant après 0041 : menu ``false``, commandes ``true`` (défaut inerte
    de la période planifiée). Activer puis désactiver le menu fonctionne ; la ligne des
    commandes n'est pas touchée et les commandes restent inactivables."""
    r = resto
    owner_db.execute(
        text(
            "UPDATE site_modules SET enabled = false "
            "WHERE site_id = :s AND module_code = 'restaurant.menu'"
        ),
        {"s": r.a},
    )
    owner_db.commit()
    assert _row(owner_db, r.a, "restaurant.orders") is True
    assert _modules(r.owner, r.a)["restaurant.menu"]["activated_for_site"] is False
    assert set_site_module(r.owner, r.a, "restaurant.menu", True).status_code == 204
    assert _modules(r.owner, r.a)["restaurant.menu"]["effective"] is True
    assert set_site_module(r.owner, r.a, "restaurant.menu", False).status_code == 204
    assert _row(owner_db, r.a, "restaurant.orders") is True
    modules = _modules(r.owner, r.a)
    assert modules["restaurant.orders"]["effective"] is False
    refused = set_site_module(r.owner, r.a, "restaurant.orders", True)
    assert _code(refused) == (422, "module_not_implemented")


def test_available_dependents_still_block_and_planned_ones_never_do(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    # Activation inerte héritée (site créé quand les recettes étaient activées par défaut, avant
    # D11) : un module planifié qui dépend du catalogue.
    owner_db.execute(
        text(
            "UPDATE site_modules SET enabled = true "
            "WHERE site_id = :s AND module_code = 'restaurant.recipes'"
        ),
        {"s": r.a},
    )
    owner_db.commit()
    assert _row(owner_db, r.a, "restaurant.recipes") is True
    # Dépendants mixtes : seul le disponible est cité ; le refus demeure.
    busy = set_site_module(r.owner, r.a, "catalog", False)
    assert _code(busy) == (409, "module_has_dependents")
    dependents = busy.json()["dependents"]
    assert "restaurant.menu" in dependents and "restaurant.recipes" not in dependents
    # Tous les dépendants disponibles désactivés (dépendants d'abord) : le seul dépendant
    # restant, planifié, ne bloque plus la désactivation du catalogue.
    for code in (
        "pos",
        "cash_register",
        "receivables",
        "sales",
        "alerts",
        "inventory_count",
        "stock",
        "restaurant.menu",
    ):
        response = set_site_module(r.owner, r.a, code, False)
        assert response.status_code == 204, (code, response.text)
    assert set_site_module(r.owner, r.a, "catalog", False).status_code == 204
    assert _row(owner_db, r.a, "restaurant.recipes") is True
    refused = set_site_module(r.owner, r.a, "restaurant.recipes", True)
    assert _code(refused) == (422, "module_not_implemented")


def test_module_activation_is_reserved_to_the_site_administrator(
    resto: SimpleNamespace, client: TestClient
) -> None:
    r = resto
    manager = member(  # type: ignore[arg-type]
        SimpleNamespace(owner=r.owner),
        client,
        "gestion-mod@menu-r1.example.com",
        "manager",
        all_sites=True,
    )
    denied = set_site_module(manager, r.a, "restaurant.menu", False)
    assert _code(denied) == (403, "permission_denied")


def test_menu_is_offered_only_by_restaurant_profiles(provision: Any, api_for: Any) -> None:
    t = provision("menu-r1-shop", profile="retail.alimentation", plan="ENTREPRISE")
    shop: Api = api_for("owner@menu-r1-shop.example.com")
    site = str(t.site_id)
    assert "restaurant.menu" not in _modules(shop, site)
    refused = set_site_module(shop, site, "restaurant.menu", True)
    assert _code(refused) == (422, "module_not_offered")
    assert _code(shop.get(SECTIONS)) == (403, "module_unavailable")


def test_profile_change_keeps_the_menu_configuration(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    """Changement de profil du site (palier D) : le menu est de la configuration — jamais
    bloquant, conservé, retrouvé au retour au profil de restauration."""
    r = resto
    section = _ok(_section(r, "Boissons", r.b), 201)["id"]
    _ok(_item(r, section, r.coca, site=r.b), 201)
    assert _apply(r.owner, r.b, "retail.alimentation").status_code == 200
    kept = owner_db.execute(
        text("SELECT count(*) FROM restaurant_menu_items WHERE site_id = :s"), {"s": r.b}
    ).scalar_one()
    assert kept == 1
    assert _apply(r.owner, r.b, MAQUIS).status_code == 200
    if not _modules(r.owner, r.b)["restaurant.menu"]["activated_for_site"]:
        assert set_site_module(r.owner, r.b, "restaurant.menu", True).status_code == 204
    assert len(_items(r.owner, site_id=r.b)) == 1


# --- Non-régression : vente d'un article présent au menu -------------------------------------


def test_sale_and_pos_of_a_menu_article_are_unchanged(resto: SimpleNamespace) -> None:
    r = resto
    section = _ok(_section(r, "Boissons"), 201)["id"]
    item = _ok(_item(r, section, r.coca), 201)["id"]
    _ok(r.owner.put(f"{ITEMS}/{item}/availability", json={"available": False}))
    entry = _ok(
        r.owner.post(
            "/stock/entries",
            json={
                "site_id": r.a,
                "supplier_id": _ok(r.owner.post("/suppliers", json={"name": "Brasseur"}), 201)[
                    "id"
                ],
                "lines": [{"article_id": r.coca, "quantity": "10", "unit_cost": "300"}],
            },
        ),
        201,
    )
    _ok(r.owner.post(f"/stock/entries/{entry['id']}/validate"))
    # L'« épuisé » du menu n'agit ni sur la vente ni sur le stock (aucune réservation, V1).
    sale = _ok(
        r.owner.post(
            "/sales",
            json={"site_id": r.a, "lines": [{"article_id": r.coca, "quantity": "2"}]},
        ),
        201,
    )
    assert sale["lines"][0]["unit_price"] == "500.00"
