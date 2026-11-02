"""Palier R8 (ADR-0053) — recettes : fiches techniques, versions, coût de revient, disponibilité,
déduction des ingrédients à la validation des ventes (une seule fois), annulation, intégrations.

Monde de référence : maquis, recettes activées sur le site ; « Riz gras » (non géré en stock,
1 500 ; conditionnement « Familial » de 4 portions à 5 500) ; ingrédients gérés en stock reçus au
coût : riz 10 kg à 800, huile 5 l à 1 200, œufs 30 à 100. Recette : rendement 4 portions pour
1 kg de riz, 0,2 l d'huile et 4 œufs → coût par portion (800 + 240 + 400) / 4 = 360.
"""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests.conftest import PASSWORD, Api, add_site, login, set_site_module
from tests.test_restaurant_orders_settlement import _race

RECIPES = "restaurant.recipes"
BASE = "/restaurant/recipes"


def _api(client: TestClient, email: str) -> Api:
    response = login(client, email)
    assert response.status_code == 200, response.text
    return Api(client=client, token=response.json()["access_token"])


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def _article(
    owner: Api,
    site: str,
    category: str,
    reference: str,
    unit: str,
    *,
    price: str = "0",
    managed: bool = True,
    decimals: bool = False,
    **extra: Any,
) -> str:
    body = {
        "reference": reference,
        "designation": reference.capitalize(),
        "category_id": category,
        "unit": unit,
        "sale_price": price,
        "stock_managed": managed,
        "decimal_quantity_allowed": decimals,
        "site_ids": [site],
        **extra,
    }
    return str(_ok(owner.post("/catalog/articles", json=body), 201)["id"])


def _receive(
    k: SimpleNamespace, lines: list[tuple[str, str, str]], site: str | None = None
) -> None:
    entry = _ok(
        k.owner.post(
            "/stock/entries",
            json={
                "site_id": site or k.site,
                "supplier_id": k.supplier,
                "lines": [{"article_id": a, "quantity": q, "unit_cost": c} for a, q, c in lines],
            },
        ),
        201,
    )
    _ok(k.owner.post(f"/stock/entries/{entry['id']}/validate"))


def _recipe_body(k: SimpleNamespace, **over: Any) -> dict[str, Any]:
    return {
        "article_id": k.dish,
        "yield_quantity": "4",
        "instructions": "Cuire le riz dans la sauce.",
        "lines": [
            {"article_id": k.rice, "quantity": "1"},
            {"article_id": k.oil, "quantity": "0.2"},
            {"article_id": k.egg, "quantity": "4", "note": "durs"},
        ],
        **over,
    }


def _level(owner_db: Session, site: str, article: str) -> tuple[str, str]:
    owner_db.expire_all()
    row = owner_db.execute(
        text(
            "SELECT quantity::text, average_cost::text FROM stock_levels "
            "WHERE site_id = :s AND article_id = :a"
        ),
        {"s": site, "a": article},
    ).one_or_none()
    return (row[0], row[1]) if row else ("none", "none")


def _movements(owner_db: Session, sale_id: str) -> list[tuple[str, str, str]]:
    owner_db.expire_all()
    return [
        (str(row[0]), row[1], row[2])
        for row in owner_db.execute(
            text(
                "SELECT article_id, movement_type::text, quantity::text FROM stock_movements "
                "WHERE source_id = :s ORDER BY occurred_at, id"
            ),
            {"s": sale_id},
        )
    ]


def _sell(
    k: SimpleNamespace,
    lines: list[dict[str, Any]],
    *,
    pay: bool = True,
    api: Api | None = None,
) -> Any:
    api = api or k.owner
    sale = _ok(
        api.post(
            "/sales",
            json={"site_id": k.site, "customer_id": k.customer, "lines": lines},
        ),
        201,
    )
    body = {"payments": [{"amount": sale["total"], "method": "CASH"}]} if pay else {}
    return sale, api.post(f"/sales/{sale['id']}/validate", json=body)


@pytest.fixture
def kit(provision: Any, client: TestClient) -> SimpleNamespace:
    t = provision("rcp", profile="restaurant.maquis", plan="ENTREPRISE")
    owner = _api(client, "owner@rcp.example.com")
    site = str(t.site_id)
    assert set_site_module(owner, site, RECIPES, True).status_code == 204
    category = _ok(owner.post("/catalog/categories", json={"name": "Cuisine"}), 201)["id"]
    rice = _article(owner, site, category, "RIZ", "kg", decimals=True)
    oil = _article(owner, site, category, "HUILE", "l", decimals=True)
    egg = _article(owner, site, category, "OEUF", "u")
    dish = _article(owner, site, category, "RIZGRAS", "plat", price="1500", managed=False)
    family = _ok(
        owner.post(
            f"/catalog/articles/{dish}/packagings",
            json={"name": "Familial", "conversion": "4", "sale_price": "5500"},
        ),
        201,
    )["id"]
    supplier = _ok(owner.post("/suppliers", json={"name": "Marché"}), 201)["id"]
    customer = _ok(
        owner.post("/customers", json={"customer_type": "INDIVIDUAL", "name": "Client"}), 201
    )["id"]
    k = SimpleNamespace(
        tenant=t.tenant_id,
        site=site,
        owner=owner,
        client=client,
        category=category,
        rice=rice,
        oil=oil,
        egg=egg,
        dish=dish,
        family=family,
        supplier=supplier,
        customer=customer,
    )
    _receive(k, [(rice, "10", "800"), (oil, "5", "1200"), (egg, "30", "100")])
    return k


@pytest.fixture
def recipe(kit: SimpleNamespace) -> dict[str, Any]:
    return dict(_ok(kit.owner.post(BASE, json=_recipe_body(kit)), 201))


def _member(k: SimpleNamespace, email: str, template: str) -> Api:
    roles = {r["template_code"]: r["id"] for r in k.owner.get("/roles").json()}
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{"role_id": roles[template]}],
        "all_sites": True,
    }
    _ok(k.owner.post("/members", json=body), 201)
    token = login(k.client, email, "Provisoire-123").json()["access_token"]
    Api(k.client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return _api(k.client, email)


# --- Fiches et versions (D1, D3, D4, D6) ---------------------------------------------------------


def test_create_read_and_version_a_recipe(kit: SimpleNamespace, owner_db: Session) -> None:
    k = kit
    created = _ok(k.owner.post(BASE, json=_recipe_body(k)), 201)
    assert (created["reference"], created["is_active"], created["version_count"]) == (
        "RIZGRAS",
        True,
        1,
    )
    current = created["current_version"]
    assert (current["number"], current["yield_quantity"]) == (1, "4.000")
    assert [(line["reference"], line["quantity"]) for line in current["lines"]] == [
        ("RIZ", "1.000"),
        ("HUILE", "0.200"),
        ("OEUF", "4.000"),
    ]
    assert created["sale_price"] == "1500.00"
    # Nouvelle composition : version 2 ; la version 1 reste consultable, inchangée.
    changed = _recipe_body(k, yield_quantity="5", lines=[{"article_id": k.rice, "quantity": "1"}])
    changed.pop("article_id")
    v2 = _ok(k.owner.post(f"{BASE}/{created['id']}/versions", json=changed), 201)
    assert (v2["version_count"], v2["current_version"]["number"]) == (2, 2)
    versions = _ok(k.owner.get(f"{BASE}/{created['id']}/versions"))
    assert [v["number"] for v in versions] == [2, 1]
    assert len(versions[1]["lines"]) == 3
    listing = _ok(k.owner.get(f"{BASE}?search=riz"))
    assert [
        (r["reference"], r["current_number"], r["ingredient_count"]) for r in listing["items"]
    ] == [("RIZGRAS", 2, 1)]
    # Notes, désactivation, réactivation ; audit de chaque action.
    _ok(k.owner.put(f"{BASE}/{created['id']}", json={"notes": "Plat du jour"}))
    assert _ok(k.owner.post(f"{BASE}/{created['id']}/deactivate"))["is_active"] is False
    assert _ok(k.owner.post(f"{BASE}/{created['id']}/activate"))["is_active"] is True
    actions = list(
        owner_db.scalars(
            text("SELECT action FROM audit_logs WHERE entity_id = :e ORDER BY occurred_at, id"),
            {"e": created["id"]},
        )
    )
    assert actions == [
        "recipe.created",
        "recipe.version_created",
        "recipe.updated",
        "recipe.deactivated",
        "recipe.activated",
    ]


def test_server_rules_of_a_recipe(kit: SimpleNamespace) -> None:
    k = kit
    # Plat géré en stock : refusé (il sortirait du stock en plus de ses ingrédients, D2).
    stocked = _recipe_body(k, article_id=k.rice, lines=[{"article_id": k.oil, "quantity": "1"}])
    assert _code(k.owner.post(BASE, json=stocked)) == (422, "recipe_article_stock_managed")
    # Ingrédient non géré, en double, le plat lui-même.
    service = _article(k.owner, k.site, k.category, "SERVICE", "u", managed=False)
    for lines, code in (
        ([{"article_id": service, "quantity": "1"}], "recipe_ingredient_not_stock_managed"),
        (
            [{"article_id": k.rice, "quantity": "1"}, {"article_id": k.rice, "quantity": "2"}],
            "recipe_duplicate_ingredient",
        ),
        ([{"article_id": k.dish, "quantity": "1"}], "recipe_self_ingredient"),
        # Œuf en quantités entières : 4 œufs pour 3 portions = 1,333… par portion.
        ([{"article_id": k.egg, "quantity": "4"}], "recipe_whole_quantity_required"),
    ):
        body = _recipe_body(k, yield_quantity="3", lines=lines)
        assert _code(k.owner.post(BASE, json=body)) == (422, code), code
    # Validation de format : aucune ligne, quantité nulle, champ inconnu, rendement nul.
    for bad in (
        _recipe_body(k, lines=[]),
        _recipe_body(k, lines=[{"article_id": k.rice, "quantity": "0"}]),
        _recipe_body(k, yield_quantity="0"),
        _recipe_body(k, unit_cost="1"),
    ):
        assert k.owner.post(BASE, json=bad).status_code == 422
    # Une recette par présentation ; le conditionnement peut avoir la sienne (variante, D5).
    _ok(k.owner.post(BASE, json=_recipe_body(k)), 201)
    assert _code(k.owner.post(BASE, json=_recipe_body(k))) == (409, "recipe_exists")
    variant = _recipe_body(k, packaging_id=k.family, yield_quantity="1")
    assert _ok(k.owner.post(BASE, json=variant), 201)["packaging_name"] == "Familial"
    # Inconnu ou d'une autre entreprise : introuvable.
    ghost = _recipe_body(k, lines=[{"article_id": str(uuid.uuid4()), "quantity": "1"}])
    assert k.owner.post(BASE, json=ghost).status_code == 404


def test_catalog_guards_prevent_double_deduction(
    kit: SimpleNamespace, recipe: dict[str, Any]
) -> None:
    k = kit
    # Plat d'une recette active : jamais géré en stock.
    refused = k.owner.patch(f"/catalog/articles/{k.dish}", json={"stock_managed": True})
    assert _code(refused) == (409, "article_has_recipe")
    # Ingrédient d'une recette active : jamais non géré (même à stock nul).
    spice = _article(k.owner, k.site, k.category, "EPICE", "g", decimals=True)
    body = _recipe_body(k, lines=[{"article_id": spice, "quantity": "5"}])
    body.pop("article_id")
    _ok(k.owner.post(f"{BASE}/{recipe['id']}/versions", json=body), 201)
    refused = k.owner.patch(f"/catalog/articles/{spice}", json={"stock_managed": False})
    assert _code(refused) == (409, "article_used_in_recipes")
    # Recette désactivée : plus aucune contrainte.
    _ok(k.owner.post(f"{BASE}/{recipe['id']}/deactivate"))
    _ok(k.owner.patch(f"/catalog/articles/{spice}", json={"stock_managed": False}))
    # Réactivation : revérifiée (ingrédient devenu non géré).
    assert _code(k.owner.post(f"{BASE}/{recipe['id']}/activate")) == (
        422,
        "recipe_ingredient_not_stock_managed",
    )


# --- Déduction à la vente (D7, D8, D9, D10) -------------------------------------------------------


def test_sale_deducts_ingredients_once_at_their_cmup(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    sale, validated = _sell(k, [{"article_id": k.dish, "quantity": "2"}])
    assert validated.status_code == 200, validated.text
    # 2 portions sur un rendement de 4 : 0,5 kg de riz, 0,1 l d'huile, 2 œufs ; plat : aucun.
    assert sorted(_movements(owner_db, sale["id"])) == sorted(
        [
            (k.rice, "SALE", "-0.500"),
            (k.oil, "SALE", "-0.100"),
            (k.egg, "SALE", "-2.000"),
        ]
    )
    assert _level(owner_db, k.site, k.rice) == ("9.500", "800.0000")  # CMUP inchangé
    assert _level(owner_db, k.site, k.oil) == ("4.900", "1200.0000")
    assert _level(owner_db, k.site, k.egg) == ("28.000", "100.0000")
    assert _level(owner_db, k.site, k.dish) == ("none", "none")
    # Mouvements au CMUP courant ; consommations matérialisées (O10) liées à la version 1.
    costs = owner_db.execute(
        text(
            "SELECT m.unit_cost::text FROM stock_movements m WHERE m.source_id = :s "
            "ORDER BY m.unit_cost"
        ),
        {"s": sale["id"]},
    ).scalars()
    assert list(costs) == ["100.0000", "800.0000", "1200.0000"]
    rows = owner_db.execute(
        text(
            "SELECT c.id, v.number FROM restaurant_recipe_consumptions c "
            "JOIN restaurant_recipe_versions v ON v.id = c.recipe_version_id "
            "WHERE c.sale_id = :s"
        ),
        {"s": sale["id"]},
    ).all()
    assert len(rows) == 3 and {number for _, number in rows} == {1}
    lines = owner_db.execute(
        text("SELECT source_line_id FROM stock_movements WHERE source_id = :s"),
        {"s": sale["id"]},
    ).scalars()
    assert set(lines) == {row[0] for row in rows}
    # Une vente validée ne se revalide pas : aucun second mouvement.
    again = k.owner.post(f"/sales/{sale['id']}/validate", json={})
    assert _code(again) == (409, "sale_not_draft")
    assert len(_movements(owner_db, sale["id"])) == 3
    audit = owner_db.execute(
        text(
            "SELECT data->>'recipe_components' FROM audit_logs "
            "WHERE entity_id = :s AND action = 'sale.validated'"
        ),
        {"s": sale["id"]},
    ).scalar_one()
    assert audit == "3"


def test_packaging_uses_its_own_recipe_or_the_base_one(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    # Sans recette propre : recette de base × conversion (1 Familial = 4 portions).
    sale, validated = _sell(k, [{"article_id": k.dish, "packaging_id": k.family, "quantity": "1"}])
    _ok(validated)
    assert sorted(_movements(owner_db, sale["id"])) == sorted(
        [(k.rice, "SALE", "-1.000"), (k.oil, "SALE", "-0.200"), (k.egg, "SALE", "-4.000")]
    )
    # Recette propre de la variante : elle seule s'applique.
    variant = _recipe_body(
        k,
        packaging_id=k.family,
        yield_quantity="1",
        lines=[{"article_id": k.rice, "quantity": "2"}],
    )
    _ok(k.owner.post(BASE, json=variant), 201)
    sale, validated = _sell(k, [{"article_id": k.dish, "packaging_id": k.family, "quantity": "2"}])
    _ok(validated)
    assert _movements(owner_db, sale["id"]) == [(k.rice, "SALE", "-4.000")]


def test_cancellation_restores_each_ingredient_exactly(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    sale, validated = _sell(k, [{"article_id": k.dish, "quantity": "4"}], pay=False)
    _ok(validated)
    assert _level(owner_db, k.site, k.egg)[0] == "26.000"
    _ok(k.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur de saisie"}))
    moves = _movements(owner_db, sale["id"])
    assert sorted(m for m in moves if m[1] == "CANCELLATION") == sorted(
        [
            (k.rice, "CANCELLATION", "1.000"),
            (k.oil, "CANCELLATION", "0.200"),
            (k.egg, "CANCELLATION", "4.000"),
        ]
    )
    assert _level(owner_db, k.site, k.rice) == ("10.000", "800.0000")
    assert _level(owner_db, k.site, k.egg) == ("30.000", "100.0000")
    # Inverses liés à leur mouvement d'origine ; double annulation refusée.
    unlinked = owner_db.execute(
        text(
            "SELECT count(*) FROM stock_movements WHERE source_id = :s "
            "AND movement_type = 'CANCELLATION' AND origin_movement_id IS NULL"
        ),
        {"s": sale["id"]},
    ).scalar_one()
    assert unlinked == 0
    again = k.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Encore une fois"})
    assert _code(again) == (409, "sale_already_cancelled")


def test_missing_ingredient_refuses_the_whole_sale(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    # 30 œufs = 30 portions au plus ; 31 portions : refus explicite, rien n'est écrit.
    sale, validated = _sell(k, [{"article_id": k.dish, "quantity": "31"}])
    assert _code(validated) == (422, "recipe_ingredients_unavailable")
    body = validated.json()
    assert (body["reason"], body["dishes"]) == ("insufficient_stock", ["RIZGRAS"])
    assert [a["reference"] for a in body["articles"]] == ["OEUF"]
    assert _movements(owner_db, sale["id"]) == []
    assert _ok(k.owner.get(f"/sales/{sale['id']}"))["status"] == "DRAFT"
    assert _level(owner_db, k.site, k.rice)[0] == "10.000"


def test_line_shortage_keeps_its_original_refusal(
    kit: SimpleNamespace, recipe: dict[str, Any]
) -> None:
    k = kit
    # Riz vendu tel quel ET comme ingrédient : le manque porte sur une ligne gérée — refus
    # d'origine (aucune traduction en manque d'ingrédient).
    _, validated = _sell(
        k,
        [
            {"article_id": k.dish, "quantity": "4"},
            {"article_id": k.rice, "quantity": "9.5"},
        ],
    )
    assert _code(validated) == (422, "insufficient_stock")


def test_without_effective_module_the_sale_is_unchanged(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    assert set_site_module(k.owner, k.site, RECIPES, False).status_code == 204
    sale, validated = _sell(k, [{"article_id": k.dish, "quantity": "2"}])
    _ok(validated)
    assert _movements(owner_db, sale["id"]) == []
    assert _level(owner_db, k.site, k.egg)[0] == "30.000"
    # Recette désactivée, module effectif : rien non plus.
    assert set_site_module(k.owner, k.site, RECIPES, True).status_code == 204
    _ok(k.owner.post(f"{BASE}/{recipe['id']}/deactivate"))
    sale, validated = _sell(k, [{"article_id": k.dish, "quantity": "2"}])
    _ok(validated)
    assert _movements(owner_db, sale["id"]) == []


def test_pos_checkout_deducts_ingredients(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    checkout = _ok(
        k.owner.post(
            "/pos/checkout",
            json={
                "site_id": k.site,
                "idempotency_key": str(uuid.uuid4()),
                "lines": [{"article_id": k.dish, "quantity": "1"}],
                "payments": [{"amount": "1500", "method": "CASH"}],
            },
        ),
        201,
    )
    sale_id = checkout["sale"]["id"] if "sale" in checkout else checkout["id"]
    assert sorted(_movements(owner_db, sale_id)) == sorted(
        [(k.rice, "SALE", "-0.250"), (k.oil, "SALE", "-0.050"), (k.egg, "SALE", "-1.000")]
    )


def test_order_settlement_deducts_ingredients(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    section = _ok(
        k.owner.post("/restaurant/menu/sections", json={"site_id": k.site, "name": "Plats"}), 201
    )
    item = _ok(
        k.owner.post(
            "/restaurant/menu/items",
            json={"site_id": k.site, "section_id": section["id"], "article_id": k.dish},
        ),
        201,
    )
    order = _ok(
        k.owner.post(
            "/restaurant/orders",
            json={
                "site_id": k.site,
                "service_mode": "COUNTER",
                "idempotency_key": str(uuid.uuid4()),
                "lines": [{"menu_item_id": item["id"], "quantity": "2"}],
            },
        ),
        201,
    )
    # Rien n'est déduit à la commande (D7).
    assert _level(owner_db, k.site, k.egg)[0] == "30.000"
    settled = _ok(
        k.owner.post(
            f"/restaurant/orders/{order['id']}/settle",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "payments": [{"amount": "3000", "method": "CASH"}],
            },
        ),
        201,
    )
    assert sorted(_movements(owner_db, settled["sale_id"])) == sorted(
        [(k.rice, "SALE", "-0.500"), (k.oil, "SALE", "-0.100"), (k.egg, "SALE", "-2.000")]
    )


def test_lot_tracked_ingredient_is_consumed_fefo(kit: SimpleNamespace, owner_db: Session) -> None:
    k = kit
    milk = _article(
        k.owner,
        k.site,
        k.category,
        "LAIT",
        "l",
        decimals=True,
        lot_tracked=True,
        expiry_tracked=True,
    )
    entry = _ok(
        k.owner.post(
            "/stock/entries",
            json={
                "site_id": k.site,
                "supplier_id": k.supplier,
                "lines": [
                    {
                        "article_id": milk,
                        "quantity": "1",
                        "unit_cost": "500",
                        "lot_number": "TARD",
                        "lot_expiry_date": "2099-12-31",
                    },
                    {
                        "article_id": milk,
                        "quantity": "1",
                        "unit_cost": "500",
                        "lot_number": "TOT",
                        "lot_expiry_date": "2098-01-31",
                    },
                ],
            },
        ),
        201,
    )
    _ok(k.owner.post(f"/stock/entries/{entry['id']}/validate"))
    body = _recipe_body(k, yield_quantity="1", lines=[{"article_id": milk, "quantity": "1.5"}])
    _ok(k.owner.post(BASE, json=body), 201)
    sale, validated = _sell(k, [{"article_id": k.dish, "quantity": "1"}])
    _ok(validated)
    lots = owner_db.execute(
        text(
            "SELECT l.number, m.quantity::text FROM stock_movements m "
            "JOIN stock_lots l ON l.id = m.lot_id WHERE m.source_id = :s ORDER BY m.id"
        ),
        {"s": sale["id"]},
    ).all()
    # FEFO : le lot qui périme le premier, puis le suivant (un mouvement par lot).
    assert [(n, q) for n, q in lots] == [("TOT", "-1.000"), ("TARD", "-0.500")]


def test_concurrent_sales_never_overdraw_a_shared_ingredient(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    k = kit
    # 30 œufs : six ventes simultanées de 6 portions (36 œufs) — au plus 5 aboutissent.
    drafts = [
        _ok(
            k.owner.post(
                "/sales",
                json={
                    "site_id": k.site,
                    "customer_id": k.customer,
                    "lines": [{"article_id": k.dish, "quantity": "6"}],
                },
            ),
            201,
        )
        for _ in range(6)
    ]
    results = _race(
        [
            (
                lambda d=d: k.owner.post(
                    f"/sales/{d['id']}/validate",
                    json={"payments": [{"amount": d["total"], "method": "CASH"}]},
                )
            )
            for d in drafts
        ]
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 200, 200, 200, 200, 422]
    assert {_code(r) for r in results if r.status_code == 422} == {
        (422, "recipe_ingredients_unavailable")
    }
    assert _level(owner_db, k.site, k.egg)[0] == "0.000"


# --- Coût de revient, disponibilité, fiche (D11, D12, D13) ---------------------------------------


def test_cost_per_site_and_masking_without_cost_view(
    kit: SimpleNamespace, recipe: dict[str, Any]
) -> None:
    k = kit
    [site] = _ok(k.owner.get(f"{BASE}/{recipe['id']}/costs"))
    assert (site["batch_cost"], site["portion_cost"], site["complete"]) == (
        "1440.0000",
        "360.0000",
        True,
    )
    assert (site["sale_price"], site["margin"], site["margin_rate"]) == (
        "1500.00",
        "1140.00",
        "76.00",
    )
    assert [line["line_cost"] for line in site["lines"]] == ["800.0000", "240.0000", "400.0000"]
    # Consultant : consulte les recettes, jamais les coûts (champs ABSENTS, à toute profondeur).
    viewer = _member(k, "consultant@rcp.example.com", "viewer")
    [masked] = _ok(viewer.get(f"{BASE}/{recipe['id']}/costs"))
    assert not {"batch_cost", "portion_cost", "margin", "margin_rate"} & set(masked)
    assert all("unit_cost" not in line and "line_cost" not in line for line in masked["lines"])
    assert masked["sale_price"] == "1500.00"
    # Ingrédient jamais reçu sur un site : coût incomplet.
    other = add_site(k.owner, "Annexe", "ANX").json()["id"]
    assert set_site_module(k.owner, other, RECIPES, True).status_code == 204
    by_site = {c["site_id"]: c for c in _ok(k.owner.get(f"{BASE}/{recipe['id']}/costs"))}
    assert by_site[other]["complete"] is False and by_site[other]["portion_cost"] == "0.0000"


def test_availability_blocks_the_menu_until_stock_arrives(
    kit: SimpleNamespace, recipe: dict[str, Any]
) -> None:
    k = kit
    [avail] = _ok(k.owner.get(f"{BASE}/{recipe['id']}/availability"))
    # Riz 10 kg → 40 portions ; huile 5 l → 100 ; œufs 30 → 30 : 30 portions, œufs limitants.
    assert (avail["units"], avail["limiting"]) == (30, ["OEUF"])
    section = _ok(
        k.owner.post("/restaurant/menu/sections", json={"site_id": k.site, "name": "Plats"}), 201
    )
    item = _ok(
        k.owner.post(
            "/restaurant/menu/items",
            json={"site_id": k.site, "section_id": section["id"], "article_id": k.dish},
        ),
        201,
    )
    assert item["orderable"] is True
    # Plus d'œufs : élément non commandable (motif calculé), commande refusée.
    _sell(k, [{"article_id": k.dish, "quantity": "30"}])
    row = _ok(k.owner.get(f"/restaurant/menu/items/{item['id']}"))
    assert (row["orderable"], row["blockers"]) == (False, ["ingredients_unavailable"])
    refused = k.owner.post(
        "/restaurant/orders",
        json={
            "site_id": k.site,
            "service_mode": "COUNTER",
            "idempotency_key": str(uuid.uuid4()),
            "lines": [{"menu_item_id": item["id"], "quantity": "1"}],
        },
    )
    assert refused.status_code in (409, 422)
    # Stock reçu : de nouveau commandable.
    _receive(k, [(k.egg, "4", "100")])
    assert _ok(k.owner.get(f"/restaurant/menu/items/{item['id']}"))["orderable"] is True
    # Module désactivé sur le site : la disponibilité des recettes ne s'applique plus.
    _sell(k, [{"article_id": k.dish, "quantity": "4"}])
    assert _ok(k.owner.get(f"/restaurant/menu/items/{item['id']}"))["orderable"] is False
    assert set_site_module(k.owner, k.site, RECIPES, False).status_code == 204
    assert _ok(k.owner.get(f"/restaurant/menu/items/{item['id']}"))["orderable"] is True


def test_kitchen_sheet_scales_the_recipe_without_costs(
    kit: SimpleNamespace, recipe: dict[str, Any]
) -> None:
    k = kit
    sheet = _ok(k.owner.get(f"{BASE}/sheet?article_id={k.dish}&packaging_id={k.family}&quantity=2"))
    assert (sheet["portions"], sheet["packaging_name"], sheet["version_number"]) == (
        "8.000",
        "Familial",
        1,
    )
    assert [(line["reference"], line["quantity"]) for line in sheet["lines"]] == [
        ("RIZ", "2.000"),
        ("HUILE", "0.400"),
        ("OEUF", "8.000"),
    ]
    assert "cost" not in str(sheet)
    missing = k.owner.get(f"{BASE}/sheet?article_id={k.rice}&quantity=1")
    assert _code(missing) == (404, "recipe_not_found")


# --- Permissions, isolation, base de données ------------------------------------------------------


def test_permissions_follow_the_matrix(kit: SimpleNamespace, recipe: dict[str, Any]) -> None:
    k = kit
    seller = _member(k, "vendeur@rcp.example.com", "seller")
    assert _code(seller.get(BASE)) == (403, "permission_denied")
    viewer = _member(k, "lecteur@rcp.example.com", "viewer")
    assert _ok(viewer.get(BASE))["total"] == 1
    assert _code(viewer.post(f"{BASE}/{recipe['id']}/deactivate")) == (403, "permission_denied")
    manager = _member(k, "gestion@rcp.example.com", "manager")
    assert _ok(manager.post(f"{BASE}/{recipe['id']}/deactivate"))["is_active"] is False
    # Module non activé sur le site sélectionné : refus.
    assert set_site_module(k.owner, k.site, RECIPES, False).status_code == 204
    scoped = Api(k.client, k.owner.token, uuid.UUID(k.site))
    assert _code(scoped.get(BASE)) == (403, "module_unavailable")


def test_isolation_between_companies(
    kit: SimpleNamespace, recipe: dict[str, Any], provision: Any, client: TestClient
) -> None:
    k = kit
    t = provision("rcp-other", profile="restaurant.maquis", plan="ENTREPRISE")
    other = _api(client, "owner@rcp-other.example.com")
    assert set_site_module(other, t.site_id, RECIPES, True).status_code == 204
    assert _ok(other.get(BASE))["total"] == 0
    assert _code(other.get(f"{BASE}/{recipe['id']}")) == (404, "recipe_not_found")
    assert _code(other.post(f"{BASE}/{recipe['id']}/deactivate")) == (404, "recipe_not_found")
    # Article d'une autre entreprise : jamais utilisable comme plat ni comme ingrédient.
    assert other.post(BASE, json=_recipe_body(k)).status_code == 404


def test_rls_and_column_rights(
    kit: SimpleNamespace, recipe: dict[str, Any], app_engine: Any, provision: Any
) -> None:
    k = kit
    stranger = provision("rcp-rls", profile="restaurant.maquis", plan="ENTREPRISE")
    factory = create_session_factory(app_engine)
    tables = (
        "restaurant_recipes",
        "restaurant_recipe_versions",
        "restaurant_recipe_lines",
        "restaurant_recipe_consumptions",
    )
    _sell(k, [{"article_id": k.dish, "quantity": "1"}])
    with factory() as session:
        set_db_context(session, tenant_id=stranger.tenant_id)
        for table in tables:
            assert session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    with factory() as session:
        set_db_context(session, tenant_id=k.tenant)
        for table in tables:
            assert session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() > 0
        # Versions, lignes et consommations immuables ; aucune suppression.
        for sql in (
            "UPDATE restaurant_recipe_versions SET yield_quantity = 1",
            "UPDATE restaurant_recipe_lines SET quantity = 1",
            "UPDATE restaurant_recipe_consumptions SET quantity = 1",
            "UPDATE restaurant_recipes SET article_id = article_id",
            "DELETE FROM restaurant_recipes",
            "DELETE FROM restaurant_recipe_consumptions",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                session.execute(text(sql))
            session.rollback()
            set_db_context(session, tenant_id=k.tenant)
        # Écriture pour une autre entreprise refusée par la politique.
        with pytest.raises(DBAPIError, match="row-level security"):
            session.execute(
                text(
                    "INSERT INTO restaurant_recipes (id, tenant_id, article_id, is_active) "
                    "VALUES (gen_random_uuid(), :t, :a, true)"
                ),
                {"t": stranger.tenant_id, "a": k.dish},
            )
        session.rollback()


def test_site_footprint_counts_consumptions_as_history(
    kit: SimpleNamespace, recipe: dict[str, Any], owner_db: Session
) -> None:
    from app.modules.restaurant_recipes.footprint import recipes_footprint

    k = kit
    assert recipes_footprint(owner_db, uuid.UUID(k.site), False).history == {}
    _ok(_sell(k, [{"article_id": k.dish, "quantity": "1"}])[1])
    owner_db.expire_all()
    footprint = recipes_footprint(owner_db, uuid.UUID(k.site), False)
    # Historique seulement : un changement de profil du site n'est jamais bloqué par lui.
    assert footprint.history == {"recipe_consumptions": 3}
    assert not footprint.open and not footprint.active


def test_public_qr_menu_shows_a_dish_without_ingredients_as_sold_out(
    kit: SimpleNamespace,
    recipe: dict[str, Any],
) -> None:
    from tests.test_restaurant_qr_points import POINTS, _token

    k = kit
    assert set_site_module(k.owner, k.site, "restaurant.qr", True).status_code == 204
    section = _ok(
        k.owner.post("/restaurant/menu/sections", json={"site_id": k.site, "name": "Plats"}), 201
    )
    item = _ok(
        k.owner.post(
            "/restaurant/menu/items",
            json={"site_id": k.site, "section_id": section["id"], "article_id": k.dish},
        ),
        201,
    )
    point = _ok(
        k.owner.post(POINTS, json={"site_id": k.site, "kind": "COUNTER", "label": "Comptoir"}),
        201,
    )
    headers = {"X-QR-Token": _token(point)}

    def public_item() -> dict[str, Any]:
        menu = _ok(k.client.get("/api/v1/public/restaurant/qr/menu", headers=headers))
        return next(i for s in menu["sections"] for i in s["items"] if i["id"] == item["id"])

    assert public_item()["available"] is True
    _ok(_sell(k, [{"article_id": k.dish, "quantity": "30"}])[1])
    # Ingrédients épuisés : « épuisé » pour le client (jamais le détail interne), commande refusée.
    sold_out = public_item()
    assert sold_out["available"] is False
    assert "blockers" not in sold_out and "ingredients" not in str(sold_out)
    refused = k.client.post(
        "/api/v1/public/restaurant/qr/orders",
        headers=headers,
        json={
            "client_key": str(uuid.uuid4()),
            "lines": [{"menu_item_id": item["id"], "quantity": 1}],
            "customer_name": "Awa",
        },
    )
    assert _code(refused) == (422, "qr_order_item_unavailable")


def test_kitchen_board_lines_carry_the_ordered_presentation(
    kit: SimpleNamespace, recipe: dict[str, Any]
) -> None:
    k = kit
    assert set_site_module(k.owner, k.site, "restaurant.kitchen", True).status_code == 204
    section = _ok(
        k.owner.post("/restaurant/menu/sections", json={"site_id": k.site, "name": "Plats"}), 201
    )
    item = _ok(
        k.owner.post(
            "/restaurant/menu/items",
            json={
                "site_id": k.site,
                "section_id": section["id"],
                "article_id": k.dish,
                "packaging_id": k.family,
            },
        ),
        201,
    )
    _ok(
        k.owner.post(
            "/restaurant/orders",
            json={
                "site_id": k.site,
                "service_mode": "COUNTER",
                "idempotency_key": str(uuid.uuid4()),
                "lines": [{"menu_item_id": item["id"], "quantity": "1"}],
            },
        ),
        201,
    )
    board = _ok(k.owner.get(f"/restaurant/kitchen/board?site_id={k.site}"))
    [line] = [line for ticket in board["tickets"] for line in ticket["lines"]]
    # Présentation commandée (fiche technique), jamais un prix ni un coût.
    assert (line["article_id"], line["packaging_id"]) == (k.dish, k.family)
    assert not {"unit_price", "price", "cost", "unit_cost"} & set(line)
    sheet = _ok(
        k.owner.get(
            f"{BASE}/sheet?article_id={line['article_id']}&packaging_id={line['packaging_id']}"
            f"&quantity={line['quantity']}"
        )
    )
    assert sheet["portions"] == "4.000"


# --- Rentabilité (palier R10, ADR-0055) -----------------------------------------------------------


def test_profitability_uses_the_real_cost_of_consumed_ingredients(
    kit: SimpleNamespace, recipe: dict[str, Any]
) -> None:
    k = kit
    for _ in range(2):
        _, validated = _sell(k, [{"article_id": k.dish, "quantity": "2"}])
        assert validated.status_code == 200, validated.text
    # Vente annulée : exclue (ses consommations sont restaurées).
    cancelled, validated = _sell(k, [{"article_id": k.dish, "quantity": "1"}], pay=False)
    assert validated.status_code == 200, validated.text
    cancel = k.owner.post(f"/sales/{cancelled['id']}/cancel", json={"reason": "Erreur"})
    assert cancel.status_code == 200, cancel.text
    page = _ok(k.owner.get(f"{BASE}/profitability"))
    [row] = page["items"]
    # 4 portions : 1 kg de riz (800), 0,2 l d'huile (240), 4 œufs (400) = 1 440.
    assert (row["article_id"], row["portions"], row["sales_count"]) == (k.dish, "4.000", 2)
    assert (row["revenue"], row["ingredient_cost"], row["portion_cost"]) == (
        "6000.00",
        "1440.00",
        "360.00",
    )
    assert (row["margin"], row["margin_rate"]) == ("4560.00", "76.00")
    # Consultant : coûts et marges ABSENTS.
    viewer = _member(k, "consultant@rcp.example.com", "viewer")
    [masked] = _ok(viewer.get(f"{BASE}/profitability"))["items"]
    assert not {"ingredient_cost", "portion_cost", "margin", "margin_rate"} & set(masked)
    assert masked["revenue"] == "6000.00"
    assert _code(viewer.get(f"{BASE}/profitability", params={"sort": "-margin"})) == (
        400,
        "invalid_sort",
    )
    # Période passée : vide ; période invalide : refusée.
    past = _ok(
        k.owner.get(
            f"{BASE}/profitability", params={"date_from": "2020-01-01", "date_to": "2020-01-31"}
        )
    )
    assert past["items"] == []
    assert _code(
        k.owner.get(
            f"{BASE}/profitability", params={"date_from": "2020-02-01", "date_to": "2020-01-01"}
        )
    ) == (422, "report_period_invalid")
