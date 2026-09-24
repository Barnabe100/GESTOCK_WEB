"""Module Catalogue et Fournisseurs (sous-phase 2.1). Les identifiants de règles renvoient à
docs/architecture/CATALOGUE_STOCK.md."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from tests.conftest import PASSWORD, Api, login


@pytest.fixture
def owner(provision: Any, api_for: Any) -> Api:
    provision("alpha", profile="quincaillerie", plan="ENTREPRISE")
    api: Api = api_for("owner@alpha.example.com")
    return api


def _category(api: Api, name: str = "Visserie") -> dict[str, Any]:
    response = api.post("/catalog/categories", json={"name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


def _supplier(api: Api, name: str = "Faso Import", **extra: Any) -> dict[str, Any]:
    response = api.post("/suppliers", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return dict(response.json())


def _article(api: Api, category_id: str, **extra: Any) -> dict[str, Any]:
    body = {
        "reference": "VIS-001",
        "designation": "Vis à bois 4x40",
        "category_id": category_id,
        "unit": "boîte",
        "purchase_price": "1500",
        "sale_price": "2000",
        **extra,
    }
    response = api.post("/catalog/articles", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- Catégories -------------------------------------------------------------------------------


def test_category_rules(owner: Api) -> None:
    created = _category(owner, "  Visserie  ")
    assert created["name"] == "Visserie"  # CAT-01 : espaces retirés
    duplicate = owner.post("/catalog/categories", json={"name": "VISSERIE"})
    assert duplicate.status_code == 409  # CAT-02 : insensible à la casse
    assert duplicate.json()["code"] == "category_name_taken"
    assert owner.post("/catalog/categories", json={"name": "   "}).status_code == 422
    assert owner.post("/catalog/categories", json={"name": "x" * 101}).status_code == 422

    renamed = owner.patch(f"/catalog/categories/{created['id']}", json={"name": "Quincaillerie"})
    assert renamed.json()["name"] == "Quincaillerie"
    deactivated = owner.post(f"/catalog/categories/{created['id']}/deactivate")
    assert deactivated.json()["is_active"] is False  # CAT-03
    assert owner.get(f"/catalog/categories/{created['id']}").status_code == 200  # CAT-04

    actions = [i["action"] for i in owner.get("/audit-logs?action=category").json()["items"]]
    assert actions == ["category.deactivated", "category.updated", "category.created"]
    update_entry = owner.get("/audit-logs?action=category.updated").json()["items"][0]
    assert update_entry["data"] == {"name": {"before": "Visserie", "after": "Quincaillerie"}}


def test_category_search_status_and_pagination(owner: Api) -> None:
    for name in ["Peinture", "Plomberie", "Électricité", "Outillage", "Plâtre"]:
        _category(owner, name)
    inactive = owner.get("/catalog/categories?search=pl").json()["items"][0]
    owner.post(f"/catalog/categories/{inactive['id']}/deactivate")

    page = owner.get("/catalog/categories?limit=2&offset=0&sort=name").json()
    assert page["total"] == 5 and page["limit"] == 2
    assert [c["name"] for c in page["items"]] == ["Électricité", "Outillage"]
    assert owner.get("/catalog/categories?search=PL").json()["total"] == 2
    assert owner.get("/catalog/categories?status=active").json()["total"] == 4
    assert owner.get("/catalog/categories?status=inactive").json()["total"] == 1
    desc = owner.get("/catalog/categories?sort=-name&limit=1").json()["items"][0]["name"]
    assert desc == "Plomberie"  # tri linguistique : « Plâtre » < « Plomberie »
    bad_sort = owner.get("/catalog/categories?sort=password")
    assert bad_sort.status_code == 400 and bad_sort.json()["code"] == "invalid_sort"
    # Les jokers SQL saisis sont traités comme du texte.
    assert owner.get("/catalog/categories?search=%25").json()["total"] == 0


# --- Fournisseurs -----------------------------------------------------------------------------


def test_supplier_rules(owner: Api) -> None:
    first = _supplier(owner, "Faso Import", email="Contact@Faso.bf", city="Ouagadougou")
    second = _supplier(owner, "Faso Import")  # SUP-02 : nom non unique
    assert first["id"] != second["id"]
    assert first["email"] == "Contact@faso.bf"
    bad = owner.post("/suppliers", json={"name": "X", "email": "pas-un-email"})
    assert bad.status_code == 422  # SUP-03

    cleared = owner.patch(f"/suppliers/{first['id']}", json={"city": "  "})
    assert cleared.json()["city"] is None  # chaîne vide = champ effacé
    unchanged = owner.patch(f"/suppliers/{first['id']}", json={"phone": "70 00 00 00"})
    assert unchanged.json()["email"] == "Contact@faso.bf"  # champ absent = inchangé
    assert owner.patch(f"/suppliers/{first['id']}", json={"name": None}).status_code == 400

    owner.post(f"/suppliers/{second['id']}/deactivate")
    assert owner.get("/suppliers?status=active").json()["total"] == 1
    assert owner.get("/suppliers?search=70 00").json()["total"] == 1  # SUP-05


# --- Articles ---------------------------------------------------------------------------------


def test_article_creation_and_decimal_contract(owner: Api) -> None:
    category = _category(owner)
    supplier = _supplier(owner)
    article = _article(
        owner,
        category["id"],
        main_supplier_id=supplier["id"],
        min_stock="10",
        max_stock="100.5",
        barcode="6001234567890",
    )
    assert article["purchase_price"] == "1500.00"  # montants en chaînes, jamais en float
    assert article["max_stock"] == "100.500"
    assert article["category_name"] == "Visserie"
    assert article["main_supplier_name"] == "Faso Import"

    for field, value in [
        ("purchase_price", "-1"),
        ("sale_price", "12.345"),  # 2 décimales maximum
        ("min_stock", "1.2345"),  # 3 décimales maximum
    ]:
        response = owner.post(
            "/catalog/articles",
            json={
                "reference": f"X-{field}",
                "designation": "X",
                "category_id": category["id"],
                "unit": "u",
                "purchase_price": "1",
                "sale_price": "1",
                field: value,
            },
        )
        assert response.status_code == 422, field


def test_article_uniqueness_rules(owner: Api) -> None:
    category = _category(owner)
    first = _article(owner, category["id"], barcode="111")
    dup_ref = owner.post(
        "/catalog/articles",
        json={
            "reference": "vis-001",
            "designation": "Doublon",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "1",
        },
    )
    assert dup_ref.json()["code"] == "article_reference_taken"  # ART-01, casse ignorée

    body = {
        "reference": "VIS-002",
        "designation": "Autre",
        "category_id": category["id"],
        "unit": "u",
        "purchase_price": "1",
        "sale_price": "1",
        "barcode": "111",
    }
    assert owner.post("/catalog/articles", json=body).json()["code"] == "article_barcode_taken"
    # ART-09 : un article désactivé libère son code-barres…
    owner.post(f"/catalog/articles/{first['id']}/deactivate")
    second = owner.post("/catalog/articles", json=body)
    assert second.status_code == 201
    # … mais ne peut pas être réactivé tant que le code est pris (ART-16).
    reactivate = owner.post(f"/catalog/articles/{first['id']}/activate")
    assert reactivate.status_code == 409
    assert reactivate.json()["code"] == "article_barcode_taken"
    # ART-15 : la recherche par code-barres ne renvoie que l'article actif.
    found = owner.get("/catalog/articles/by-barcode/111").json()
    assert found["id"] == second.json()["id"]


def test_article_selection_rules(owner: Api) -> None:
    active = _category(owner, "Active")
    inactive = _category(owner, "Ancienne")
    supplier = _supplier(owner)
    article = _article(owner, active["id"], main_supplier_id=supplier["id"])

    owner.post(f"/catalog/categories/{inactive['id']}/deactivate")
    owner.post(f"/suppliers/{supplier['id']}/deactivate")
    refused = owner.post(
        "/catalog/articles",
        json={
            "reference": "NEW",
            "designation": "N",
            "category_id": inactive["id"],
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "1",
        },
    )
    assert refused.json()["code"] == "category_inactive"  # ART-10

    # L'association existante à un fournisseur devenu inactif est conservée.
    kept = owner.patch(f"/catalog/articles/{article['id']}", json={"designation": "Vis 4x45"})
    assert kept.status_code == 200 and kept.json()["main_supplier_id"] == supplier["id"]
    change = owner.patch(f"/catalog/articles/{article['id']}", json={"category_id": inactive["id"]})
    assert change.json()["code"] == "category_inactive"
    thresholds = owner.patch(
        f"/catalog/articles/{article['id']}", json={"min_stock": "10", "max_stock": "5"}
    )
    assert thresholds.json()["code"] == "invalid_stock_thresholds"  # ART-07
    # ART-11 : aucun champ de stock n'est accepté sur l'article.
    ignored = owner.patch(f"/catalog/articles/{article['id']}", json={"stock": "999"})
    assert "stock" not in ignored.json()


def test_article_search_filters_sort(owner: Api) -> None:
    tools = _category(owner, "Outillage")
    paint = _category(owner, "Peinture")
    supplier = _supplier(owner)
    _article(owner, tools["id"], reference="MAR-01", designation="Marteau", sale_price="5000")
    _article(
        owner,
        tools["id"],
        reference="TOU-02",
        designation="Tournevis",
        sale_price="1500",
        barcode="777",
        main_supplier_id=supplier["id"],
    )
    _article(
        owner, paint["id"], reference="PEI-03", designation="Peinture blanche", sale_price="12000"
    )

    def refs(query: str) -> list[str]:
        return [a["reference"] for a in owner.get(f"/catalog/articles?{query}").json()["items"]]

    assert refs("search=tourn") == ["TOU-02"]
    assert refs("search=777") == ["TOU-02"]  # code-barres
    assert refs("search=peinture") == ["PEI-03"]  # catégorie ou désignation
    assert refs(f"category_id={tools['id']}") == ["MAR-01", "TOU-02"]
    assert refs(f"supplier_id={supplier['id']}") == ["TOU-02"]
    assert refs("sort=-sale_price") == ["PEI-03", "MAR-01", "TOU-02"]
    page = owner.get("/catalog/articles?limit=1&offset=1&sort=reference").json()
    assert page["total"] == 3 and [a["reference"] for a in page["items"]] == ["PEI-03"]


def test_supplier_link_requires_suppliers_module(owner: Api, owner_db: Session) -> None:
    category = _category(owner)
    supplier = _supplier(owner)
    owner_db.execute(
        text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'suppliers'")
    )
    owner_db.commit()
    response = owner.post(
        "/catalog/articles",
        json={
            "reference": "A",
            "designation": "A",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "1",
            "main_supplier_id": supplier["id"],
        },
    )
    assert response.json()["code"] == "module_unavailable"
    assert owner.get("/suppliers").json()["code"] == "module_unavailable"


# --- Transactions -----------------------------------------------------------------------------


def test_failed_operation_leaves_nothing_behind(owner: Api, owner_db: Session) -> None:
    category = _category(owner)
    _article(owner, category["id"], barcode="999")
    before = owner_db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()
    response = owner.post(
        "/catalog/articles",
        json={
            "reference": "NEW",
            "designation": "N",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "1",
            "barcode": "999",
        },
    )
    assert response.status_code == 409
    assert owner_db.execute(text("SELECT count(*) FROM catalog_articles")).scalar_one() == 1
    assert owner_db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one() == before


# --- Permissions, rôles, abonnement -----------------------------------------------------------


def _member(owner: Api, client: Any, email: str, template: str) -> Api:
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    created = owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": [{"role_id": roles[template]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def test_permissions_by_system_role(owner: Api, client: Any) -> None:
    category = _category(owner)
    viewer = _member(owner, client, "lecteur@example.com", "viewer")
    manager = _member(owner, client, "stock@example.com", "manager")
    admin = _member(owner, client, "admin@example.com", "administrator")

    assert viewer.get("/catalog/articles").status_code == 200
    denied = viewer.post("/catalog/categories", json={"name": "X"})
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    # Rôles système dynamiques : les nouvelles permissions du catalogue sont incluses.
    assert manager.post("/catalog/categories", json={"name": "Y"}).status_code == 201
    assert admin.post(f"/catalog/categories/{category['id']}/deactivate").status_code == 200
    assert manager.get("/members").status_code == 403  # pas d'administration


def test_expired_subscription_blocks_catalog_writes(owner: Api, owner_db: Session) -> None:
    _category(owner)
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert owner.get("/catalog/categories").status_code == 200
    blocked = owner.post("/catalog/categories", json={"name": "Z"})
    assert blocked.status_code == 403 and blocked.json()["code"] == "subscription_restricted"


def test_missing_system_role_can_be_added_from_template(owner: Api, owner_db: Session) -> None:
    owner_db.execute(text("DELETE FROM roles WHERE template_code = 'manager'"))
    owner_db.commit()
    templates = {t["code"]: t for t in owner.get("/role-templates").json()}
    assert templates["manager"]["instantiated"] is False
    created = owner.post("/roles/from-template", json={"template_code": "manager"})
    assert created.status_code == 201
    assert "catalog.article.create" in created.json()["permission_codes"]
    again = owner.post("/roles/from-template", json={"template_code": "manager"})
    assert again.json()["code"] == "role_template_exists"


# --- Isolation multi-tenant -------------------------------------------------------------------


def test_catalog_isolation_between_tenants(provision: Any, api_for: Any) -> None:
    provision("alpha")
    provision("beta")
    api_a = api_for("owner@alpha.example.com")
    api_b = api_for("owner@beta.example.com")
    cat_b = _category(api_b, "Secret B")
    sup_b = _supplier(api_b, "Fournisseur B")
    art_b = _article(api_b, cat_b["id"], barcode="B-1")
    _category(api_a, "Secret B")  # même nom possible dans un autre tenant

    assert api_a.get("/catalog/categories").json()["total"] == 1
    assert api_a.get("/suppliers").json()["total"] == 0
    assert api_a.get("/catalog/articles").json()["total"] == 0
    for method, path, body in [
        ("get", f"/catalog/categories/{cat_b['id']}", None),
        ("patch", f"/catalog/categories/{cat_b['id']}", {"name": "piraté"}),
        ("post", f"/catalog/categories/{cat_b['id']}/deactivate", None),
        ("get", f"/suppliers/{sup_b['id']}", None),
        ("patch", f"/suppliers/{sup_b['id']}", {"name": "piraté"}),
        ("get", f"/catalog/articles/{art_b['id']}", None),
        ("patch", f"/catalog/articles/{art_b['id']}", {"designation": "piraté"}),
        ("post", f"/catalog/articles/{art_b['id']}/deactivate", None),
        ("get", "/catalog/articles/by-barcode/B-1", None),
    ]:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(api_a, method)(path, **kwargs).status_code == 404, path

    cat_a = api_a.get("/catalog/categories").json()["items"][0]
    foreign_category = api_a.post(
        "/catalog/articles",
        json={
            "reference": "X",
            "designation": "X",
            "category_id": cat_b["id"],
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "1",
        },
    )
    assert foreign_category.json()["code"] == "category_not_found"
    foreign_supplier = api_a.post(
        "/catalog/articles",
        json={
            "reference": "X",
            "designation": "X",
            "category_id": cat_a["id"],
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "1",
            "main_supplier_id": sup_b["id"],
        },
    )
    assert foreign_supplier.json()["code"] == "supplier_not_found"
    assert api_b.get(f"/catalog/articles/{art_b['id']}").json()["designation"] == "Vis à bois 4x40"


@pytest.mark.parametrize("table", ["catalog_categories", "suppliers", "catalog_articles"])
def test_catalog_tables_rls(table: str, provision: Any, api_for: Any, db: Session) -> None:
    a = provision("alpha")
    provision("beta")
    for slug in ("alpha", "beta"):
        api = api_for(f"owner@{slug}.example.com")
        category = _category(api)
        _supplier(api)
        _article(api, category["id"])
    assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    set_db_context(db, tenant_id=a.tenant_id)
    tenants = db.execute(text(f"SELECT DISTINCT tenant_id FROM {table}")).scalars().all()
    assert tenants == [a.tenant_id]
    db.rollback()
    set_db_context(db, tenant_id=a.tenant_id)
    with pytest.raises(Exception, match="permission denied"):
        db.execute(text(f"DELETE FROM {table}"))
