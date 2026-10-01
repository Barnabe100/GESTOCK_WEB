"""Lot 3-B (ADR-0040) — quantités décimales et conditionnements de vente.

- ``catalog_articles.decimal_quantity_allowed`` : quantités vendues entières, sauf article
  autorisé (contrôle serveur, back-office et point de vente) ;
- ``catalog_packagings`` : conditionnements d'un article (nom libre, conversion > 0, prix
  propre, actif / inactif ; jamais supprimés ; conversion figée dès qu'une vente l'utilise) ;
- vente : présentation choisie (unité de base ou conditionnement), quantité de base =
  quantité × conversion, stock TOUJOURS en unité de base, instantané figé sur la ligne."""

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
from tests.conftest import Api
from tests.stock_helpers import World


@pytest.fixture
def shop(world: World) -> World:
    """Article 0 (« u », entier) : 50 en boutique ; article 1 : 40 en boutique."""
    sh.validated_entry(world, [(0, "50", "100"), (1, "40", "100")])
    return world


def _category(w: World) -> str:
    return str(w.owner.get("/catalog/categories").json()["items"][0]["id"])


def _article(
    w: World,
    reference: str,
    *,
    unit: str = "pièce",
    price: str = "500",
    decimal: bool = False,
    managed: bool = True,
) -> dict[str, Any]:
    response = w.owner.post(
        "/catalog/articles",
        json={
            "reference": reference,
            "designation": f"Article {reference}",
            "category_id": _category(w),
            "unit": unit,
            "sale_price": price,
            "decimal_quantity_allowed": decimal,
            "stock_managed": managed,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _packaging(
    w: World, article_id: str, name: str, conversion: str, price: str, api: Api | None = None
) -> dict[str, Any]:
    response = (api or w.owner).post(
        f"/catalog/articles/{article_id}/packagings",
        json={"name": name, "conversion": conversion, "sale_price": price},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _line(article_id: str, quantity: str, packaging_id: str | None = None) -> dict[str, Any]:
    line: dict[str, Any] = {"article_id": article_id, "quantity": quantity}
    if packaging_id is not None:
        line["packaging_id"] = packaging_id
    return line


def _draft(w: World, lines: list[dict[str, Any]], api: Api | None = None) -> Any:
    return (api or w.owner).post("/sales", json={"site_id": w.site, "lines": lines})


def _validate(w: World, sale: dict[str, Any], api: Api | None = None) -> Any:
    return (api or w.owner).post(
        f"/sales/{sale['id']}/validate",
        json={"payments": [{"amount": sale["total"], "method": "CASH"}]},
    )


def _sell(w: World, lines: list[dict[str, Any]]) -> dict[str, Any]:
    draft = _draft(w, lines)
    assert draft.status_code == 201, draft.text
    validated = _validate(w, draft.json())
    assert validated.status_code == 200, validated.text
    return dict(validated.json())


def _checkout(w: World, lines: list[dict[str, Any]], total: str) -> Any:
    return w.owner.post(
        "/pos/checkout",
        json={
            "site_id": w.site,
            "lines": lines,
            "payments": [{"amount": total, "method": "CASH"}],
            "idempotency_key": str(uuid.uuid4()),
        },
    )


def _level(owner_db: Session, w: World, article_id: str) -> str:
    owner_db.expire_all()
    row = owner_db.execute(
        text("SELECT quantity::text FROM stock_levels WHERE site_id = :s AND article_id = :a"),
        {"s": w.site, "a": article_id},
    ).one_or_none()
    return row[0] if row else "none"


def _code(response: Any) -> str:
    return str(response.json().get("code"))


# --- 1. Unité de base et quantités décimales ---------------------------------------------------


def test_decimal_flag_defaults_to_false(world: World, owner_db: Session) -> None:
    article = world.owner.get(f"/catalog/articles/{world.articles[0]}").json()
    assert article["decimal_quantity_allowed"] is False
    created = _article(world, "KG-1", unit="kg", decimal=True)
    assert created["decimal_quantity_allowed"] is True
    # Les articles existants n'ont reçu aucun conditionnement.
    assert world.owner.get(f"/catalog/articles/{world.articles[0]}/packagings").json()["total"] == 0
    # Modification : champ général (``catalog.article.update``), audité.
    patched = world.owner.patch(
        f"/catalog/articles/{world.articles[0]}", json={"decimal_quantity_allowed": True}
    )
    assert patched.status_code == 200 and patched.json()["decimal_quantity_allowed"] is True
    owner_db.expire_all()
    diff = owner_db.execute(
        text(
            "SELECT data FROM audit_logs WHERE action = 'article.updated' AND entity_id = :a "
            "ORDER BY occurred_at DESC LIMIT 1"
        ),
        {"a": world.articles[0]},
    ).scalar_one()
    assert diff["decimal_quantity_allowed"] == {"before": False, "after": True}


def test_whole_quantities_enforced_by_server(shop: World, owner_db: Session) -> None:
    article = shop.articles[0]
    for quantity in ("2.5", "0.5", "1.001"):
        refused = _draft(shop, [_line(article, quantity)])
        assert refused.status_code == 422, quantity
        assert _code(refused) == "quantity_not_whole"
        assert refused.json()["articles"] == ["A-0"]
        refused = _checkout(shop, [_line(article, quantity)], "1000")
        assert (refused.status_code, _code(refused)) == (422, "quantity_not_whole")
    for quantity in ("1", "2", "10", "3.000"):
        assert _draft(shop, [_line(article, quantity)]).status_code == 201, quantity
    _sell(shop, [_line(article, "2")])
    assert _level(owner_db, shop, article) == "48.000"


def test_decimal_quantities_when_allowed(shop: World, owner_db: Session) -> None:
    rice = _article(shop, "RIZ", unit="kg", price="650", decimal=True)
    entry = shop.owner.post(
        "/stock/entries",
        json={
            "site_id": shop.site,
            "supplier_id": shop.supplier,
            "lines": [{"article_id": rice["id"], "quantity": "10", "unit_cost": "500"}],
        },
    ).json()
    assert shop.owner.post(f"/stock/entries/{entry['id']}/validate").status_code == 200
    sale = _sell(shop, [_line(rice["id"], "2.5")])
    line = sale["lines"][0]
    assert (line["quantity"], line["base_quantity"], line["unit_price"], line["line_total"]) == (
        "2.500",
        "2.500",
        "650.00",
        "1625.00",
    )
    assert line["packaging_id"] is None
    _sell(shop, [_line(rice["id"], "0.5")])
    assert _level(owner_db, shop, rice["id"]) == "7.000"


# --- 2. Conditionnements : création, règles, modification -------------------------------------


def test_packaging_lifecycle(shop: World, owner_db: Session) -> None:
    article = shop.articles[0]
    carton = _packaging(shop, article, "Carton 24", "24", "10500")
    assert (carton["conversion"], carton["sale_price"], carton["is_active"], carton["in_use"]) == (
        "24.000",
        "10500.00",
        True,
        False,
    )
    # Conversion strictement positive ; entière pour un article sans quantités décimales.
    for conversion in ("0", "-2"):
        bad = shop.owner.post(
            f"/catalog/articles/{article}/packagings",
            json={"name": "X", "conversion": conversion, "sale_price": "1"},
        )
        assert bad.status_code == 422, conversion
    fractional = shop.owner.post(
        f"/catalog/articles/{article}/packagings",
        json={"name": "Demi", "conversion": "0.5", "sale_price": "1"},
    )
    assert (fractional.status_code, _code(fractional)) == (422, "packaging_conversion_not_whole")
    # Nom unique parmi les conditionnements ACTIFS de l'article (insensible à la casse).
    duplicate = shop.owner.post(
        f"/catalog/articles/{article}/packagings",
        json={"name": "carton 24", "conversion": "12", "sale_price": "1"},
    )
    assert (duplicate.status_code, _code(duplicate)) == (409, "packaging_name_taken")
    # Jamais utilisé : nom, conversion et prix modifiables.
    patched = shop.owner.patch(
        f"/catalog/packagings/{carton['id']}",
        json={"name": "Carton de 24", "conversion": "24", "sale_price": "10000"},
    )
    assert patched.status_code == 200, patched.text
    assert (patched.json()["name"], patched.json()["sale_price"]) == ("Carton de 24", "10000.00")
    # Désactivation (aucune suppression physique), puis réactivation.
    assert (
        shop.owner.post(f"/catalog/packagings/{carton['id']}/deactivate").json()["is_active"]
        is False
    )
    assert (
        shop.owner.client.delete(
            f"/api/v1/catalog/packagings/{carton['id']}",
            headers={"Authorization": f"Bearer {shop.owner.token}"},
        ).status_code
        == 405
    )
    assert shop.owner.post(f"/catalog/packagings/{carton['id']}/activate").json()["is_active"]
    listing = shop.owner.get(f"/catalog/articles/{article}/packagings?status=active").json()
    assert [p["name"] for p in listing["items"]] == ["Carton de 24"]
    owner_db.expire_all()
    actions = owner_db.execute(
        text("SELECT action FROM audit_logs WHERE entity_id = :p ORDER BY occurred_at"),
        {"p": carton["id"]},
    ).scalars()
    assert list(actions) == [
        "packaging.created",
        "packaging.updated",
        "packaging.deactivated",
        "packaging.activated",
    ]


def test_conversion_frozen_once_used_price_still_editable(shop: World) -> None:
    article = shop.articles[0]
    carton = _packaging(shop, article, "Carton 24", "24", "10500")
    draft = _draft(shop, [_line(article, "1", carton["id"])])
    assert draft.status_code == 201, draft.text
    assert shop.owner.get(f"/catalog/articles/{article}/packagings").json()["items"][0]["in_use"]
    frozen = shop.owner.patch(f"/catalog/packagings/{carton['id']}", json={"conversion": "12"})
    assert (frozen.status_code, _code(frozen)) == (409, "packaging_in_use")
    # Valeur inchangée renvoyée par le formulaire : acceptée.
    same = shop.owner.patch(f"/catalog/packagings/{carton['id']}", json={"conversion": "24.000"})
    assert same.status_code == 200
    # Le prix d'un conditionnement utilisé peut changer : le brouillon doit être réenregistré
    # (mécanisme existant ``sale_prices_changed``), la vente validée garde son prix figé.
    repriced = shop.owner.patch(f"/catalog/packagings/{carton['id']}", json={"sale_price": "11000"})
    assert repriced.status_code == 200
    stale = _validate(shop, draft.json())
    assert (stale.status_code, _code(stale)) == (409, "sale_prices_changed")
    assert stale.json()["articles"] == ["A-0"]
    refreshed = shop.owner.put(
        f"/sales/{draft.json()['id']}",
        json={"lines": [_line(article, "1", carton["id"])]},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["total"] == "11000.00"
    assert _validate(shop, refreshed.json()).status_code == 200


def test_disallowing_decimals_requires_whole_active_packagings(shop: World) -> None:
    rice = _article(shop, "RIZ", unit="kg", decimal=True)
    bag = _packaging(shop, rice["id"], "Sac 25,5 kg", "25.5", "16000")
    refused = shop.owner.patch(
        f"/catalog/articles/{rice['id']}", json={"decimal_quantity_allowed": False}
    )
    assert (refused.status_code, _code(refused)) == (409, "article_has_fractional_packagings")
    assert refused.json()["packagings"] == ["Sac 25,5 kg"]
    shop.owner.post(f"/catalog/packagings/{bag['id']}/deactivate")
    allowed = shop.owner.patch(
        f"/catalog/articles/{rice['id']}", json={"decimal_quantity_allowed": False}
    )
    assert allowed.status_code == 200
    # Réactiver un conditionnement à conversion décimale : refusé pour un article entier.
    again = shop.owner.post(f"/catalog/packagings/{bag['id']}/activate")
    assert (again.status_code, _code(again)) == (422, "packaging_conversion_not_whole")


# --- 3. Vente en conditionnement : stock en unité de base --------------------------------------


def test_sale_in_packaging_moves_base_quantity(shop: World, owner_db: Session) -> None:
    coca = shop.articles[0]
    carton = _packaging(shop, coca, "Carton 24", "24", "10500")
    sale = _sell(shop, [_line(coca, "2", carton["id"]), _line(coca, "1")])
    by_packaging = {line["packaging_name"]: line for line in sale["lines"]}
    packed, base = by_packaging["Carton 24"], by_packaging[None]
    assert (
        packed["quantity"],
        packed["unit_price"],
        packed["line_total"],
        packed["packaging_conversion"],
        packed["base_quantity"],
    ) == ("2.000", "10500.00", "21000.00", "24.000", "48.000")
    assert (base["quantity"], base["unit_price"], base["base_quantity"]) == (
        "1.000",
        "150.00",
        "1.000",
    )
    assert sale["total"] == "21150.00"
    # Stock 50 − 48 − 1 = 1 ; mouvements en unité de base.
    assert _level(owner_db, shop, coca) == "1.000"
    owner_db.expire_all()
    quantities = owner_db.execute(
        text(
            "SELECT m.quantity::text FROM stock_movements m WHERE m.source_id = :s "
            "ORDER BY m.quantity"
        ),
        {"s": sale["id"]},
    ).scalars()
    assert list(quantities) == ["-48.000", "-1.000"]
    # Annulation (paiements annulés d'abord) : la quantité de base revient au stock.
    for payment in shop.owner.get(f"/sales/{sale['id']}/payments").json()["items"]:
        shop.owner.post(
            f"/sales/{sale['id']}/payments/{payment['id']}/cancel", json={"reason": "Erreur"}
        )
    response = shop.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur de saisie"})
    assert response.status_code == 200, response.text
    assert _level(owner_db, shop, coca) == "50.000"


def test_insufficient_stock_after_conversion(shop: World, owner_db: Session) -> None:
    article = shop.articles[1]  # 40 en stock
    carton = _packaging(shop, article, "Carton 24", "24", "3000")
    refused = _checkout(shop, [_line(article, "2", carton["id"])], "6000")
    assert (refused.status_code, _code(refused)) == (422, "insufficient_stock")
    assert _level(owner_db, shop, article) == "40.000"
    assert _checkout(shop, [_line(article, "1", carton["id"])], "3000").status_code == 201
    assert _level(owner_db, shop, article) == "16.000"


def test_decimal_packaging(shop: World, owner_db: Session) -> None:
    rice = _article(shop, "RIZ", unit="kg", price="700", decimal=True)
    entry = shop.owner.post(
        "/stock/entries",
        json={
            "site_id": shop.site,
            "supplier_id": shop.supplier,
            "lines": [{"article_id": rice["id"], "quantity": "100", "unit_cost": "500"}],
        },
    ).json()
    shop.owner.post(f"/stock/entries/{entry['id']}/validate")
    bag = _packaging(shop, rice["id"], "Sac", "25.5", "17000")
    sale = _sell(shop, [_line(rice["id"], "1.5", bag["id"])])
    line = sale["lines"][0]
    assert (line["quantity"], line["base_quantity"], line["line_total"]) == (
        "1.500",
        "38.250",
        "25500.00",
    )
    assert _level(owner_db, shop, rice["id"]) == "61.750"
    # Article entier : 1,5 carton refusé (quantité fractionnaire de conditionnement).
    carton = _packaging(shop, shop.articles[0], "Carton 24", "24", "10500")
    refused = _draft(shop, [_line(shop.articles[0], "1.5", carton["id"])])
    assert (refused.status_code, _code(refused)) == (422, "quantity_not_whole")
    # Quantité de base au-delà de 3 décimales : refusée, jamais arrondie.
    dose = _packaging(shop, rice["id"], "Dose", "0.125", "100")
    precise = _draft(shop, [_line(rice["id"], "0.001", dose["id"])])
    assert (precise.status_code, _code(precise)) == (422, "base_quantity_precision")
    assert _draft(shop, [_line(rice["id"], "0.008", dose["id"])]).status_code == 201


def test_presentation_rules_on_lines(shop: World) -> None:
    article, other = shop.articles[0], shop.articles[1]
    carton = _packaging(shop, article, "Carton 24", "24", "10500")
    duplicate = _draft(shop, [_line(article, "1", carton["id"]), _line(article, "2", carton["id"])])
    assert (duplicate.status_code, _code(duplicate)) == (422, "duplicate_article_line")
    duplicate = _draft(shop, [_line(article, "1"), _line(article, "2")])
    assert (duplicate.status_code, _code(duplicate)) == (422, "duplicate_article_line")
    # Conditionnement d'un autre article, ou inconnu : refusé.
    for packaging_id in (carton["id"], str(uuid.uuid4())):
        wrong = _draft(shop, [_line(other, "1", packaging_id)])
        assert (wrong.status_code, _code(wrong)) == (422, "packaging_not_found")


def test_unmanaged_article_sold_by_packaging(shop: World, owner_db: Session) -> None:
    service = _article(shop, "SRV", unit="heure", price="5000", managed=False)
    pack = _packaging(shop, service["id"], "Forfait 10 h", "10", "45000")
    sale = _sell(shop, [_line(service["id"], "1", pack["id"])])
    assert sale["lines"][0]["base_quantity"] == "10.000"
    owner_db.expire_all()
    assert (
        owner_db.execute(
            text("SELECT count(*) FROM stock_movements WHERE source_id = :s"), {"s": sale["id"]}
        ).scalar_one()
        == 0
    )


def test_deactivated_packaging_refused_and_history_kept(shop: World) -> None:
    article = shop.articles[0]
    carton = _packaging(shop, article, "Carton 24", "24", "10500")
    sold = _sell(shop, [_line(article, "1", carton["id"])])
    draft = _draft(shop, [_line(article, "1", carton["id"])]).json()
    shop.owner.patch(f"/catalog/packagings/{carton['id']}", json={"name": "Carton (ancien)"})
    shop.owner.post(f"/catalog/packagings/{carton['id']}/deactivate")
    # Panier en cours : refus à la validation ; nouvel encaissement : refus.
    refused = _validate(shop, draft)
    assert (refused.status_code, _code(refused)) == (422, "packaging_inactive")
    refused = _checkout(shop, [_line(article, "1", carton["id"])], "10500")
    assert (refused.status_code, _code(refused)) == (422, "packaging_inactive")
    # Le point de vente ne le propose plus.
    found = shop.owner.get("/pos/articles", params={"site_id": shop.site, "search": "A-0"})
    assert found.json()[0]["packagings"] == []
    # Nouveau conditionnement, même nom possible (l'ancien est inactif).
    _packaging(shop, article, "Carton 24", "24", "12000")
    # Vente historique : instantané inchangé (nom, conversion, prix).
    line = shop.owner.get(f"/sales/{sold['id']}").json()["lines"][0]
    assert (
        line["packaging_name"],
        line["packaging_conversion"],
        line["unit_price"],
        line["base_quantity"],
    ) == ("Carton 24", "24.000", "10500.00", "24.000")


def test_pos_offers_base_unit_and_active_packagings(shop: World) -> None:
    article = shop.articles[0]
    _packaging(shop, article, "Pack 6", "6", "850")
    _packaging(shop, article, "Carton 24", "24", "3200")
    inactive = _packaging(shop, article, "Palette", "480", "60000")
    shop.owner.post(f"/catalog/packagings/{inactive['id']}/deactivate")
    found = shop.owner.get("/pos/articles", params={"site_id": shop.site, "search": "A-0"}).json()
    item = found[0]
    assert (item["sale_price"], item["unit"], item["decimal_quantity_allowed"]) == (
        "150.00",
        "u",
        False,
    )
    assert [(p["name"], p["conversion"], p["sale_price"]) for p in item["packagings"]] == [
        ("Pack 6", "6.000", "850.00"),
        ("Carton 24", "24.000", "3200.00"),
    ]
    done = _checkout(shop, [_line(article, "2", item["packagings"][1]["id"])], "6400")
    assert done.status_code == 201, done.text
    assert done.json()["sale"]["lines"][0]["packaging_name"] == "Carton 24"


# --- 4. Permissions -----------------------------------------------------------------------------


def test_packaging_permissions(shop: World, client: TestClient) -> None:
    article = shop.articles[0]
    carton = _packaging(shop, article, "Carton 24", "24", "10500")
    manager = sh.member(shop, client, "gestionnaire@example.com", "manager", all_sites=True)
    seller = sh.member(shop, client, "vendeur@example.com", "seller", all_sites=True)
    # Gestionnaire : informations générales (nom, conversion, état), pas les prix.
    priced = manager.post(
        f"/catalog/articles/{article}/packagings",
        json={"name": "Pack 6", "conversion": "6", "sale_price": "900"},
    )
    assert (priced.status_code, _code(priced)) == (403, "price_update_not_allowed")
    pack = _packaging(shop, article, "Pack 6", "6", "0", api=manager)
    assert pack["sale_price"] == "0.00"
    assert (
        manager.patch(f"/catalog/packagings/{pack['id']}", json={"name": "Pack de 6"}).status_code
        == 200
    )
    denied = manager.patch(f"/catalog/packagings/{carton['id']}", json={"sale_price": "9000"})
    assert (denied.status_code, _code(denied)) == (403, "price_update_not_allowed")
    assert manager.post(f"/catalog/packagings/{pack['id']}/deactivate").status_code == 200
    # Vendeur : consultation et vente seulement.
    assert seller.get(f"/catalog/articles/{article}/packagings").status_code == 200
    for response in (
        seller.post(
            f"/catalog/articles/{article}/packagings",
            json={"name": "X", "conversion": "2", "sale_price": "0"},
        ),
        seller.patch(f"/catalog/packagings/{carton['id']}", json={"name": "Y"}),
        seller.post(f"/catalog/packagings/{carton['id']}/deactivate"),
    ):
        assert response.status_code == 403
    sale = _draft(shop, [_line(article, "1", carton["id"])], api=seller)
    assert sale.status_code == 201, sale.text
    assert sale.json()["total"] == "10500.00"


# --- 5. Multi-tenant ----------------------------------------------------------------------------


def test_packaging_isolation_api_and_sql(
    shop: World, provision: Any, api_for: Any, db: Session, owner_db: Session
) -> None:
    carton = _packaging(shop, shop.articles[0], "Carton 24", "24", "10500")
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    category = other.post("/catalog/categories", json={"name": "Divers"}).json()["id"]
    own = other.post(
        "/catalog/articles",
        json={"reference": "B-1", "designation": "B", "category_id": category, "unit": "u"},
    ).json()["id"]
    assert other.patch(f"/catalog/packagings/{carton['id']}", json={"name": "X"}).status_code == 404
    assert other.post(f"/catalog/packagings/{carton['id']}/deactivate").status_code == 404
    assert other.get(f"/catalog/articles/{shop.articles[0]}/packagings").status_code == 404
    stolen = other.post(
        "/sales",
        json={"site_id": str(beta.site_id), "lines": [_line(own, "1", carton["id"])]},
    )
    assert (stolen.status_code, _code(stolen)) == (422, "packaging_not_found")
    # SQL : RLS (aucune ligne d'un autre tenant, aucune écriture pour lui) et droits minimaux.
    set_db_context(db, tenant_id=beta.tenant_id)
    assert db.execute(text("SELECT count(*) FROM catalog_packagings")).scalar_one() == 0
    alpha = owner_db.execute(text("SELECT tenant_id FROM catalog_packagings")).scalar_one()
    with pytest.raises(DBAPIError, match="row-level security"):
        db.execute(
            text(
                "INSERT INTO catalog_packagings (id, tenant_id, article_id, name, conversion, "
                "sale_price, is_active) VALUES (gen_random_uuid(), :t, :a, 'X', 2, 0, true)"
            ),
            {"t": alpha, "a": shop.articles[0]},
        )
    db.rollback()
    flags = owner_db.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'catalog_packagings'"
        )
    ).one()
    assert tuple(flags) == (True, True)
    privileges = owner_db.execute(
        text(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'catalog_packagings' AND grantee = 'stockmanager_app' "
            "ORDER BY privilege_type"
        )
    ).scalars()
    assert list(privileges) == ["INSERT", "SELECT"]
    updatable = owner_db.execute(
        text(
            "SELECT column_name FROM information_schema.column_privileges "
            "WHERE table_name = 'catalog_packagings' AND grantee = 'stockmanager_app' "
            "AND privilege_type = 'UPDATE' ORDER BY column_name"
        )
    ).scalars()
    assert list(updatable) == ["conversion", "is_active", "name", "sale_price", "updated_at"]


# --- 6. Concurrence -----------------------------------------------------------------------------


def _race(app: Any, token: str, calls: list[tuple[str, str, str, Any]]) -> dict[str, int]:
    barrier = threading.Barrier(len(calls))
    statuses: dict[str, int] = {}

    def run(name: str, method: str, path: str, body: Any) -> None:
        with TestClient(app) as client:
            api = Api(client, token)
            barrier.wait()
            statuses[name] = getattr(api, method)(path, json=body).status_code

    threads = [threading.Thread(target=run, args=call) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return statuses


def test_concurrent_conversion_change_and_sale(shop: World, app: Any, owner_db: Session) -> None:
    """Modification de la conversion et encaissement simultanés : l'un attend l'autre (verrou
    exclusif / partagé du conditionnement). Jamais de vente à une conversion différente de
    celle du conditionnement : soit la vente passe et la conversion est figée (409), soit la
    conversion change d'abord et la vente utilise la nouvelle."""
    for attempt in range(3):
        carton = _packaging(shop, shop.articles[1], f"Lot {attempt}", "2", "300")
        statuses = _race(
            app,
            shop.owner.token,
            [
                (
                    "sale",
                    "post",
                    "/pos/checkout",
                    {
                        "site_id": shop.site,
                        "lines": [_line(shop.articles[1], "1", carton["id"])],
                        "payments": [{"amount": "300", "method": "CASH"}],
                        "idempotency_key": str(uuid.uuid4()),
                    },
                ),
                ("patch", "patch", f"/catalog/packagings/{carton['id']}", {"conversion": "3"}),
            ],
        )
        assert statuses["sale"] == 201, statuses
        assert statuses["patch"] in (200, 409), statuses
        owner_db.expire_all()
        conversion, snapshot, base = owner_db.execute(
            text(
                "SELECT p.conversion::text, l.packaging_conversion::text, l.base_quantity::text "
                "FROM catalog_packagings p JOIN sale_lines l ON l.packaging_id = p.id "
                "WHERE p.id = :p"
            ),
            {"p": carton["id"]},
        ).one()
        assert conversion == snapshot == base, (statuses, conversion, snapshot)
