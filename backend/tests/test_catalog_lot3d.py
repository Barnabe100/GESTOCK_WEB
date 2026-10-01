"""Lot 3-D (ADR-0042) — codes-barres multiples et codes-barres des conditionnements : registre
commun du tenant (code principal, codes supplémentaires, codes des conditionnements), unicité
parmi les présentations ACTIVES (élément désactivé = codes libérés, revérifiés à la
réactivation), scan EXACT vers une présentation (unité de base ou conditionnement) au point de
vente et dans les écrans opérationnels, recherche partielle étendue à tous les codes, audit,
isolation (API et SQL)."""

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World

INVENTORIES = "/inventories"


@pytest.fixture
def coca(world: World) -> dict[str, str]:
    """Article 0 (bouteille « u ») : code principal 111, Pack 6 (prix 900) et Carton 24 (prix
    3 400) ; 100 en boutique."""
    assert _patch_article(world, 0, barcode="111").status_code == 200
    pack = _packaging(world, 0, "Pack 6", "6", "900")
    carton = _packaging(world, 0, "Carton 24", "24", "3400")
    sh.validated_entry(world, [(0, "100", "100")])
    return {"article": world.articles[0], "pack": pack, "carton": carton}


def _patch_article(w: World, index: int, **fields: Any) -> Any:
    return w.owner.patch(f"/catalog/articles/{w.articles[index]}", json=fields)


def _packaging(w: World, index: int, name: str, conversion: str, price: str | None = None) -> str:
    body: dict[str, Any] = {"name": name, "conversion": conversion}
    if price is not None:
        body["sale_price"] = price
    response = w.owner.post(f"/catalog/articles/{w.articles[index]}/packagings", json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _add_article_code(w: World, index: int, code: str, api: Api | None = None) -> Any:
    return (api or w.owner).post(
        f"/catalog/articles/{w.articles[index]}/barcodes", json={"code": code}
    )


def _add_packaging_code(w: World, packaging: str, code: str, api: Api | None = None) -> Any:
    return (api or w.owner).post(f"/catalog/packagings/{packaging}/barcodes", json={"code": code})


def _ok(response: Any, status: int = 201) -> dict[str, Any]:
    assert response.status_code == status, response.text
    return dict(response.json())


def _code(response: Any) -> str:
    return str(response.json().get("code"))


def _codes(w: World, index: int) -> list[tuple[str, str, str | None, bool]]:
    page = w.owner.get(f"/catalog/articles/{w.articles[index]}/barcodes?limit=100").json()
    return sorted(
        (b["code"], b["kind"], b["packaging_name"], b["is_active"]) for b in page["items"]
    )


def _resolve(w: World, code: str, api: Api | None = None) -> Any:
    return (api or w.owner).get("/catalog/barcodes/resolve", params={"code": code})


def _pos_scan(w: World, code: str, api: Api | None = None) -> Any:
    return (api or w.owner).get(
        "/pos/articles/by-barcode", params={"site_id": w.site, "barcode": code}
    )


# --- 1-3. Codes de l'article et des conditionnements -------------------------------------------


def test_primary_code_is_kept_and_mirrored(world: World, owner_db: Session) -> None:
    # Code principal = champ actuel de l'article (API inchangée), présent dans le registre.
    _ok(_patch_article(world, 0, barcode="3017620422003"), 200)
    article = world.owner.get(f"/catalog/articles/{world.articles[0]}").json()
    assert article["barcode"] == "3017620422003"
    assert _codes(world, 0) == [("3017620422003", "PRIMARY", None, True)]
    # Modification puis retrait du code principal : le registre suit, dans la même instruction.
    _ok(_patch_article(world, 0, barcode="3017620422004"), 200)
    assert _codes(world, 0) == [("3017620422004", "PRIMARY", None, True)]
    _ok(_patch_article(world, 0, barcode=None), 200)
    assert _codes(world, 0) == []
    # Le code principal ne se retire jamais par la route des codes.
    _ok(_patch_article(world, 0, barcode="3017620422005"), 200)
    primary = world.owner.get(f"/catalog/articles/{world.articles[0]}/barcodes").json()["items"]
    refused = world.owner.delete(f"/catalog/barcodes/{primary[0]['id']}")
    assert (refused.status_code, _code(refused)) == (422, "barcode_primary")


def test_article_with_several_codes(world: World, coca: dict[str, str]) -> None:
    _ok(_add_article_code(world, 0, "222"))
    added = _ok(_add_article_code(world, 0, " 333 "))
    assert (added["code"], added["kind"], added["packaging_id"]) == ("333", "ADDITIONAL", None)
    # Tous désignent l'article en unité de base.
    for code in ("111", "222", "333"):
        scan = _ok(_resolve(world, code), 200)
        assert (scan["article"]["id"], scan["packaging"]) == (world.articles[0], None)
    # Retrait d'un code supplémentaire : plus reconnu, les autres restent.
    removed = world.owner.delete(f"/catalog/barcodes/{added['id']}")
    assert removed.status_code == 204, removed.text
    assert _code(_resolve(world, "333")) == "barcode_unknown"
    assert _resolve(world, "222").status_code == 200


def test_packaging_with_several_codes(world: World, coca: dict[str, str]) -> None:
    for code in ("C-1", "C-2"):
        created = _ok(_add_packaging_code(world, coca["carton"], code))
        assert (created["kind"], created["packaging_name"]) == ("PACKAGING", "Carton 24")
    _ok(_add_packaging_code(world, coca["pack"], "P-1"))
    _ok(_add_packaging_code(world, coca["pack"], "P-2"))
    assert _codes(world, 0) == [
        ("111", "PRIMARY", None, True),
        ("C-1", "PACKAGING", "Carton 24", True),
        ("C-2", "PACKAGING", "Carton 24", True),
        ("P-1", "PACKAGING", "Pack 6", True),
        ("P-2", "PACKAGING", "Pack 6", True),
    ]
    for code, packaging, conversion in (
        ("C-2", coca["carton"], "24.000"),
        ("P-1", coca["pack"], "6.000"),
    ):
        scan = _ok(_resolve(world, code), 200)
        assert scan["article"]["id"] == world.articles[0]
        assert (scan["packaging"]["id"], scan["packaging"]["conversion"]) == (
            packaging,
            conversion,
        )
    # Un conditionnement d'un AUTRE article ne peut pas recevoir de code via cet article : la
    # route vise le conditionnement lui-même (le code suit toujours son conditionnement).
    other = _packaging(world, 1, "Carton 12", "12")
    created = _ok(_add_packaging_code(world, other, "C-3"))
    assert created["article_id"] == world.articles[1]


# --- 4-7. Unicité commune au tenant -------------------------------------------------------------


def test_uniqueness_across_presentations(world: World, coca: dict[str, str]) -> None:
    _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    # Article / article (code principal comme code supplémentaire).
    for refused in (
        _patch_article(world, 1, barcode="111"),
        _add_article_code(world, 1, "111"),
    ):
        assert refused.status_code == 409, refused.text
    assert _code(_patch_article(world, 1, barcode="111")) == "article_barcode_taken"
    assert _code(_add_article_code(world, 1, "111")) == "barcode_taken"
    # Article / conditionnement, dans les deux sens.
    assert _code(_add_packaging_code(world, coca["pack"], "111")) == "barcode_taken"
    assert _code(_patch_article(world, 1, barcode="C-1")) == "article_barcode_taken"
    assert _code(_add_article_code(world, 0, "C-1")) == "barcode_taken"
    # Conditionnement / conditionnement (du même article ou d'un autre).
    assert _code(_add_packaging_code(world, coca["pack"], "C-1")) == "barcode_taken"
    other = _packaging(world, 1, "Carton 12", "12")
    assert _code(_add_packaging_code(world, other, "C-1")) == "barcode_taken"
    # Création d'un article avec un code déjà pris.
    category = world.owner.get("/catalog/categories").json()["items"][0]["id"]
    created = world.owner.post(
        "/catalog/articles",
        json={
            "reference": "NEW-1",
            "designation": "Nouveau",
            "category_id": category,
            "unit": "u",
            "barcode": "C-1",
        },
    )
    assert (created.status_code, _code(created)) == (409, "article_barcode_taken")
    # Format libre, 50 caractères au plus, jamais vide.
    assert _add_article_code(world, 1, "X" * 51).status_code == 422
    assert _add_article_code(world, 1, "   ").status_code == 422
    assert _ok(_add_article_code(world, 1, "FOURNISSEUR-ABC/12"))["code"] == "FOURNISSEUR-ABC/12"


def test_database_guarantees_uniqueness(world: World, coca: dict[str, str], db: Session) -> None:
    """Même sans le contrôle du service, l'index unique partiel refuse un doublon actif."""
    from app.modules.catalog.models import Barcode

    tenant = world.owner.get("/tenant").json()["id"]
    set_db_context(db, tenant_id=tenant)
    db.add(
        Barcode(
            tenant_id=tenant,
            article_id=world.articles[1],
            packaging_id=None,
            code="111",
            kind="ADDITIONAL",
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError, match="uq_catalog_barcodes_tenant_code_active"):
        db.flush()
    db.rollback()


def test_same_code_in_two_tenants(
    world: World, coca: dict[str, str], provision: Any, api_for: Any
) -> None:
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    category = other.post("/catalog/categories", json={"name": "Divers"}).json()["id"]
    created = other.post(
        "/catalog/articles",
        json={
            "reference": "B-1",
            "designation": "Article B",
            "category_id": category,
            "unit": "u",
            "barcode": "111",
        },
    )
    assert created.status_code == 201, created.text
    own = created.json()["id"]
    assert _add_article_code(world, 0, "SHARED").status_code == 201
    assert (
        other.post(f"/catalog/articles/{own}/barcodes", json={"code": "SHARED"}).status_code == 201
    )
    # Chaque tenant résout SON article.
    mine = _ok(_resolve(world, "111"), 200)
    theirs = _ok(other.get("/catalog/barcodes/resolve", params={"code": "111"}), 200)
    assert (mine["article"]["id"], theirs["article"]["id"]) == (world.articles[0], own)
    scan = other.get(
        "/pos/articles/by-barcode", params={"site_id": str(beta.site_id), "barcode": "SHARED"}
    )
    assert scan.json()["article_id"] == own


# --- 8-10. Éléments inactifs, réactivation -------------------------------------------------------


def test_inactive_article_frees_its_codes(world: World, coca: dict[str, str]) -> None:
    _ok(_add_article_code(world, 0, "222"))
    _ok(world.owner.post(f"/catalog/articles/{world.articles[0]}/deactivate"), 200)
    # Codes conservés (historique), mais inactifs et plus reconnus.
    assert ("111", "PRIMARY", None, False) in _codes(world, 0)
    assert ("222", "ADDITIONAL", None, False) in _codes(world, 0)
    for code in ("111", "222"):
        assert _code(_resolve(world, code)) == "barcode_unknown"
        assert _code(_pos_scan(world, code)) == "barcode_unknown"
    # … et libérés pour un nouvel élément actif.
    _ok(_patch_article(world, 1, barcode="111"), 200)
    _ok(_add_article_code(world, 2, "222"))
    assert _ok(_resolve(world, "111"), 200)["article"]["id"] == world.articles[1]


def test_inactive_packaging_frees_its_codes(world: World, coca: dict[str, str]) -> None:
    _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    _ok(world.owner.post(f"/catalog/packagings/{coca['carton']}/deactivate"), 200)
    assert ("C-1", "PACKAGING", "Carton 24", False) in _codes(world, 0)
    assert _code(_resolve(world, "C-1")) == "barcode_unknown"
    assert _code(_pos_scan(world, "C-1")) == "barcode_unknown"
    # Code libéré : repris par un autre conditionnement actif.
    other = _packaging(world, 1, "Carton 12", "12")
    _ok(_add_packaging_code(world, other, "C-1"))
    assert _ok(_resolve(world, "C-1"), 200)["packaging"]["id"] == other
    # Un code ajouté à un conditionnement inactif n'est pas contrôlé tant qu'il le reste.
    assert _add_packaging_code(world, coca["carton"], "C-1").status_code == 201


def test_reactivation_with_conflict(world: World, coca: dict[str, str]) -> None:
    _ok(_add_article_code(world, 0, "222"))
    _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    _ok(world.owner.post(f"/catalog/articles/{world.articles[0]}/deactivate"), 200)
    _ok(world.owner.post(f"/catalog/packagings/{coca['carton']}/deactivate"), 200)
    _ok(_add_article_code(world, 1, "222"))
    other = _packaging(world, 1, "Carton 12", "12")
    _ok(_add_packaging_code(world, other, "C-1"))
    # Réactivation refusée tant que l'un de ses codes est pris (aucun changement d'état).
    article = world.owner.post(f"/catalog/articles/{world.articles[0]}/activate")
    assert (article.status_code, _code(article)) == (409, "article_barcode_taken")
    assert article.json()["codes"] == ["222"]
    packaging = world.owner.post(f"/catalog/packagings/{coca['carton']}/activate")
    assert (packaging.status_code, _code(packaging)) == (409, "barcode_taken")
    assert packaging.json()["codes"] == ["C-1"]
    assert world.owner.get(f"/catalog/articles/{world.articles[0]}").json()["is_active"] is False
    # Le code retiré de l'autre élément, la réactivation passe et ses codes redeviennent actifs.
    taken = [
        b
        for b in world.owner.get(f"/catalog/articles/{world.articles[1]}/barcodes").json()["items"]
        if b["code"] == "222"
    ][0]
    assert world.owner.delete(f"/catalog/barcodes/{taken['id']}").status_code == 204
    _ok(world.owner.post(f"/catalog/articles/{world.articles[0]}/activate"), 200)
    assert _ok(_resolve(world, "222"), 200)["article"]["id"] == world.articles[0]


def test_concurrent_claims_of_one_code(
    world: World, coca: dict[str, str], client: TestClient
) -> None:
    """Deux ajouts simultanés du même code : un seul réussit (index unique), l'autre 409."""
    results: list[int] = []
    barrier = threading.Barrier(2)

    def claim(index: int) -> None:
        barrier.wait()
        results.append(_add_article_code(world, index, "RACE").status_code)

    threads = [threading.Thread(target=claim, args=(i,)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [201, 409]


# --- 11-14. Scan exact ----------------------------------------------------------------------------


def test_exact_scan_article_and_packaging(world: World, coca: dict[str, str]) -> None:
    _ok(_add_article_code(world, 0, "222"))
    _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    _ok(_add_packaging_code(world, coca["pack"], "P-1"))
    # Article : unité de base.
    for code in ("111", "222"):
        scan = _ok(_pos_scan(world, code), 200)
        assert (scan["article_id"], scan["scanned_packaging_id"]) == (world.articles[0], None)
    # Conditionnements : la présentation scannée, avec sa conversion.
    for code, packaging in (("C-1", coca["carton"]), ("P-1", coca["pack"])):
        scan = _ok(_pos_scan(world, code), 200)
        assert scan["scanned_packaging_id"] == packaging
        assert packaging in {p["id"] for p in scan["packagings"]}
    # Le panier vend 1 carton (et non 24 unités) : la vente garde la présentation.
    sale = _ok(
        world.owner.post(
            "/sales",
            json={
                "site_id": world.site,
                "lines": [
                    {
                        "article_id": world.articles[0],
                        "packaging_id": coca["carton"],
                        "quantity": "1",
                    }
                ],
            },
        )
    )
    assert (sale["lines"][0]["packaging_name"], sale["lines"][0]["base_quantity"]) == (
        "Carton 24",
        "24.000",
    )


def test_unknown_and_partial_codes_never_match(world: World, coca: dict[str, str]) -> None:
    _ok(_add_packaging_code(world, coca["carton"], "C-12345"))
    for code in ("C-1234", "12345", "c-12345", "A-0", "Article 0", "999"):
        assert _code(_resolve(world, code)) == "barcode_unknown"
        assert _code(_pos_scan(world, code)) == "barcode_unknown"
    # Espaces autour d'un code saisi au clavier : ignorés.
    assert _resolve(world, " C-12345 ").status_code == 200


def test_unpriced_packaging_scanned_at_pos(world: World, coca: dict[str, str]) -> None:
    unpriced = _packaging(world, 0, "Fardeau 12", "12")
    _ok(_add_packaging_code(world, unpriced, "F-1"))
    refused = _pos_scan(world, "F-1")
    assert (refused.status_code, _code(refused)) == (422, "packaging_price_not_set")
    assert refused.json()["packagings"] == ["Fardeau 12"]
    # Le scan des écrans de stock l'identifie (le prix n'intervient pas en stock).
    assert _ok(_resolve(world, "F-1"), 200)["packaging"]["sale_price"] is None
    # Prix fixé : vendable par scan.
    _ok(world.owner.patch(f"/catalog/packagings/{unpriced}", json={"sale_price": "1800"}), 200)
    assert _ok(_pos_scan(world, "F-1"), 200)["scanned_packaging_id"] == unpriced


# --- 15-18. Scan dans les écrans opérationnels ----------------------------------------------------


def test_scan_in_stock_operations(world: World, coca: dict[str, str], owner_db: Session) -> None:
    """Entrée, sortie, transfert : le scan présélectionne article + présentation ; la quantité
    (et le coût) restent saisis, les règles existantes s'appliquent (conversion serveur)."""
    _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    scan = _ok(_resolve(world, "C-1"), 200)
    line = {"article_id": scan["article"]["id"], "packaging_id": scan["packaging"]["id"]}
    entry = _ok(
        world.owner.post(
            "/stock/entries",
            json={
                "site_id": world.site,
                "supplier_id": world.supplier,
                "lines": [{**line, "quantity": "2", "unit_cost": "2400"}],
            },
        )
    )
    assert entry["lines"][0]["base_quantity"] == "48.000"
    _ok(world.owner.post(f"/stock/entries/{entry['id']}/validate"), 200)
    exit_ = _ok(
        world.owner.post(
            "/stock/exits",
            json={
                "site_id": world.site,
                "reason_id": world.reasons["PERTE"],
                "lines": [{**line, "quantity": "1"}],
            },
        )
    )
    assert exit_["lines"][0]["base_quantity"] == "24.000"
    _ok(world.owner.post(f"/stock/exits/{exit_['id']}/validate"), 200)
    transfer = _ok(
        world.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": world.site,
                "destination_site_id": world.site2,
                "lines": [{**line, "quantity": "1"}],
            },
        )
    )
    assert transfer["lines"][0]["packaging_name"] == "Carton 24"
    _ok(world.owner.post(f"/stock/transfers/{transfer['id']}/validate"), 200)
    assert sh.level(owner_db, world, 0)[0] == "100.000"  # 100 + 48 − 24 − 24
    assert sh.level(owner_db, world, 0, world.site2)[0] == "24.000"
    # Un conditionnement désactivé n'est plus reconnu : impossible de le présélectionner.
    _ok(world.owner.post(f"/catalog/packagings/{coca['carton']}/deactivate"), 200)
    assert _code(_resolve(world, "C-1")) == "barcode_unknown"


def test_scan_in_inventory(world: World, coca: dict[str, str]) -> None:
    """Le scan identifie la ligne (``article_id`` exact) et la présentation ; la quantité
    comptée n'est jamais devinée."""
    _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    inventory = _ok(
        world.owner.post(
            INVENTORIES,
            json={
                "site_id": world.site,
                "inventory_type": "TARGETED",
                "article_ids": [world.articles[0], world.articles[1]],
            },
        )
    )
    _ok(world.owner.post(f"{INVENTORIES}/{inventory['id']}/start"), 200)
    scan = _ok(_resolve(world, "C-1"), 200)
    lines = world.owner.get(
        f"{INVENTORIES}/{inventory['id']}/lines",
        params={"article_id": scan["article"]["id"]},
    ).json()
    assert lines["total"] == 1
    line = lines["items"][0]
    assert line["quantity_physical"] is None  # rien n'est compté par le scan
    assert scan["packaging"]["id"] in {p["id"] for p in line["packagings"]}
    counted = world.owner.patch(
        f"{INVENTORIES}/{inventory['id']}/lines",
        json={
            "counts": [
                {
                    "line_id": line["id"],
                    "packaging_id": scan["packaging"]["id"],
                    "packaging_quantity": "4",
                    "unit_quantity": "3",
                }
            ]
        },
    )
    assert counted.status_code == 200, counted.text
    assert counted.json()["lines"][0]["quantity_physical"] == "99.000"


# --- 19-20. Recherche partielle -------------------------------------------------------------------


def test_partial_search_covers_all_codes(world: World, coca: dict[str, str]) -> None:
    _ok(_add_article_code(world, 0, "SEC-98765"))
    _ok(_add_packaging_code(world, coca["carton"], "CART-55501"))
    for term in ("98765", "5550", "111"):
        found = world.owner.get("/catalog/articles", params={"search": term}).json()["items"]
        assert [a["id"] for a in found] == [world.articles[0]], term
        levels = world.owner.get(
            "/stock/levels", params={"search": term, "site_id": world.site}
        ).json()["items"]
        assert {r["article_id"] for r in levels} == {world.articles[0]}, term
        pos = world.owner.get("/pos/articles", params={"search": term, "site_id": world.site})
        assert [a["article_id"] for a in pos.json()] == [world.articles[0]], term
        candidates = world.owner.get(
            f"{INVENTORIES}/candidates", params={"search": term, "site_id": world.site}
        ).json()["items"]
        assert [c["article_id"] for c in candidates] == [world.articles[0]], term
    # Ventes : filtre « référence article » sur un code de conditionnement.
    sale = _ok(
        world.owner.post(
            "/sales",
            json={
                "site_id": world.site,
                "lines": [{"article_id": world.articles[0], "quantity": "1"}],
            },
        )
    )
    found = world.owner.get("/sales", params={"article_reference": "CART-555"}).json()["items"]
    assert [s["id"] for s in found] == [sale["id"]]
    # Le scan reste exact : un fragment n'est jamais un code.
    assert _code(_resolve(world, "5550")) == "barcode_unknown"


# --- 21. Isolation (API et SQL) -------------------------------------------------------------------


def test_isolation_api_and_sql(
    world: World,
    coca: dict[str, str],
    provision: Any,
    api_for: Any,
    db: Session,
    owner_db: Session,
) -> None:
    created = _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    assert (
        _code(other.get("/catalog/barcodes/resolve", params={"code": "C-1"})) == "barcode_unknown"
    )
    assert other.get(f"/catalog/articles/{world.articles[0]}/barcodes").status_code == 404
    assert (
        other.post(f"/catalog/packagings/{coca['carton']}/barcodes", json={"code": "X"}).status_code
        == 404
    )
    assert other.delete(f"/catalog/barcodes/{created['id']}").status_code == 404
    # SQL : RLS (aucune ligne d'un autre tenant, aucune écriture pour lui), droits minimaux.
    set_db_context(db, tenant_id=beta.tenant_id)
    assert db.execute(text("SELECT count(*) FROM catalog_barcodes")).scalar_one() == 0
    alpha = owner_db.execute(text("SELECT DISTINCT tenant_id FROM catalog_barcodes")).scalar_one()
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO catalog_barcodes (id, tenant_id, article_id, code, kind, is_active) "
                "VALUES (gen_random_uuid(), :t, :a, 'Z', 'ADDITIONAL', true)"
            ),
            {"t": alpha, "a": world.articles[0]},
        )
    db.rollback()
    flags = owner_db.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'catalog_barcodes'"
        )
    ).one()
    assert tuple(flags) == (True, True)
    privileges = owner_db.execute(
        text(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'catalog_barcodes' AND grantee = 'stockmanager_app' "
            "ORDER BY privilege_type"
        )
    ).scalars()
    assert list(privileges) == ["DELETE", "INSERT", "SELECT"]
    # Un code ne peut viser le conditionnement d'un AUTRE article (FK composite).
    set_db_context(db, tenant_id=alpha)
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO catalog_barcodes "
                "(id, tenant_id, article_id, packaging_id, code, kind, is_active) "
                "VALUES (gen_random_uuid(), :t, :a, :p, 'Z', 'PACKAGING', true)"
            ),
            {"t": alpha, "a": world.articles[1], "p": coca["carton"]},
        )
    db.rollback()


def test_permissions(world: World, coca: dict[str, str], client: TestClient) -> None:
    """Consultation ``catalog.article.view`` (Vendeur) ; gestion ``catalog.article.update``."""
    seller = sh.member(world, client, "vendeur@example.com", "seller")
    assert seller.get(f"/catalog/articles/{world.articles[0]}/barcodes").status_code == 200
    assert _resolve(world, "111", api=seller).status_code == 200
    assert _add_article_code(world, 0, "222", api=seller).status_code == 403
    assert _add_packaging_code(world, coca["carton"], "C-1", api=seller).status_code == 403
    created = _ok(_add_article_code(world, 0, "222"))
    assert seller.delete(f"/catalog/barcodes/{created['id']}").status_code == 403
    manager = sh.member(world, client, "gestionnaire@example.com", "manager")
    assert _add_article_code(world, 0, "333", api=manager).status_code == 201


# --- 22. Audit ------------------------------------------------------------------------------------


def test_barcode_changes_are_audited(world: World, coca: dict[str, str], owner_db: Session) -> None:
    added = _ok(_add_article_code(world, 0, "222"))
    on_carton = _ok(_add_packaging_code(world, coca["carton"], "C-1"))
    assert world.owner.delete(f"/catalog/barcodes/{added['id']}").status_code == 204
    assert world.owner.delete(f"/catalog/barcodes/{on_carton['id']}").status_code == 204
    _ok(_patch_article(world, 0, barcode="112"), 200)
    rows = owner_db.execute(
        text(
            "SELECT action, entity_id::text, user_id IS NOT NULL, occurred_at IS NOT NULL, data "
            "FROM audit_logs WHERE action LIKE '%barcode%' OR action = 'article.updated' "
            "ORDER BY occurred_at"
        )
    ).all()
    events = [(r[0], r[1], r[4].get("barcode")) for r in rows]
    assert events[-5:] == [
        ("article.barcode_added", world.articles[0], {"before": None, "after": "222"}),
        ("packaging.barcode_added", coca["carton"], {"before": None, "after": "C-1"}),
        ("article.barcode_removed", world.articles[0], {"before": "222", "after": None}),
        ("packaging.barcode_removed", coca["carton"], {"before": "C-1", "after": None}),
        # Code principal : modification de l'article, avant / après (journal existant).
        ("article.updated", world.articles[0], {"before": "111", "after": "112"}),
    ]
    assert all(r[2] and r[3] for r in rows)
    assert rows[-4][4]["name"] == "Carton 24"
