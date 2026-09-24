"""Ventes comptant (Phase 2.4) : création, calculs, validation via StockService, stock
insuffisant, atomicité, idempotence, immutabilité, annulation, concurrence, client, site,
RBAC, audit, isolation API et SQL (rôle applicatif réel, RLS)."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World


def _sale(w: World, lines: list[tuple[int, str]], **extra: Any) -> dict[str, Any]:
    body = {
        "site_id": w.site,
        "lines": [{"article_id": w.articles[i], "quantity": q} for i, q in lines],
        **extra,
    }
    response = w.owner.post("/sales", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _validate(api: Api, sale: dict[str, Any]) -> Any:
    return api.post(f"/sales/{sale['id']}/validate")


def _movements(owner_db: Session, kind: str = "SALE") -> list[tuple[str, str, str]]:
    owner_db.expire_all()
    return [
        (row[0], row[1], row[2])
        for row in owner_db.execute(
            text(
                "SELECT quantity::text, source_number, unit_cost::text FROM stock_movements "
                "WHERE movement_type = :kind ORDER BY occurred_at, id"
            ),
            {"kind": kind},
        )
    ]


def _customer(w: World, name: str = "Awa Traoré") -> dict[str, Any]:
    response = w.owner.post("/customers", json={"customer_type": "INDIVIDUAL", "name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- Création et calculs ---------------------------------------------------------------------


def test_create_draft_with_server_side_prices(world: World, owner_db: Session) -> None:
    sale = _sale(
        world,
        [(0, "2.5"), (1, "1")],
        # Prix et totaux envoyés par le client : ignorés (le serveur fait foi).
        total="1",
        subtotal="1",
    )
    assert (sale["number"], sale["status"]) == ("VTE-000001", "DRAFT")
    assert sale["customer_id"] is None  # vente comptant anonyme
    first, second = sale["lines"]
    assert (first["quantity"], first["unit_price"], first["line_total"]) == (
        "2.500",
        "150.00",
        "375.00",
    )
    assert second["line_total"] == "150.00"
    assert (sale["subtotal"], sale["total"]) == ("525.00", "525.00")
    assert sh.level(owner_db, world, 0) == ("none", "none")  # brouillon : aucun effet
    assert _sale(world, [(0, "1")])["number"] == "VTE-000002"


def test_line_total_rounding_is_decimal(world: World) -> None:
    world.owner.patch(f"/catalog/articles/{world.articles[0]}", json={"sale_price": "0.35"})
    sale = _sale(world, [(0, "0.333")])
    # 0.333 × 0.35 = 0.11655 → 0.12 (arrondi au centime, demi supérieur), jamais de float.
    assert sale["lines"][0]["line_total"] == "0.12" and sale["total"] == "0.12"


@pytest.mark.parametrize(
    ("lines", "code"),
    [
        ([], "validation_error"),
        ([{"quantity": "0"}], "validation_error"),
        ([{"quantity": "-1"}], "validation_error"),
        ([{"quantity": "1.2345"}], "validation_error"),
    ],
)
def test_invalid_lines(world: World, lines: list[dict[str, Any]], code: str) -> None:
    body_lines = [{"article_id": world.articles[0], **line} for line in lines]
    response = world.owner.post("/sales", json={"site_id": world.site, "lines": body_lines})
    assert response.status_code == 422 and response.json()["code"] == code


def test_business_rules_on_create(world: World) -> None:
    def create(**body: Any) -> Any:
        payload = {
            "site_id": world.site,
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
            **body,
        }
        return world.owner.post("/sales", json=payload)

    unknown = create(lines=[{"article_id": str(uuid.uuid4()), "quantity": "1"}])
    assert unknown.status_code == 422 and unknown.json()["code"] == "article_not_found"
    duplicate = create(
        lines=[
            {"article_id": world.articles[0], "quantity": "1"},
            {"article_id": world.articles[0], "quantity": "2"},
        ]
    )
    assert duplicate.json()["code"] == "duplicate_article_line"
    assert create(customer_id=str(uuid.uuid4())).json()["code"] == "customer_not_found"
    inactive = _customer(world, "Inactif")
    world.owner.post(f"/customers/{inactive['id']}/deactivate")
    assert create(customer_id=inactive["id"]).json()["code"] == "customer_inactive"
    assert create(sale_date="2999-01-01").json()["code"] == "future_operation_date"
    world.owner.post(f"/catalog/articles/{world.articles[0]}/deactivate")
    assert create().json()["code"] == "article_inactive"
    assert world.owner.get("/sales").json()["total"] == 0  # rien n'a été créé


def test_sale_with_active_customer(world: World) -> None:
    customer = _customer(world)
    sale = _sale(world, [(0, "1")], customer_id=customer["id"])
    assert (sale["customer_code"], sale["customer_name"]) == ("CLI-000001", "Awa Traoré")
    found = world.owner.get("/sales", params={"search": "awa"}).json()["items"]
    assert [s["number"] for s in found] == [sale["number"]]


# --- Validation et stock -----------------------------------------------------------------------


def test_validation_moves_stock_through_stock_service(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    sale = _sale(world, [(0, "3")])
    validated = _validate(world.owner, sale)
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["status"] == "VALIDATED" and body["validated_at"] and body["validated_by_name"]
    assert sh.level(owner_db, world, 0) == ("7.000", "100.0000")  # CMUP inchangé
    # Mouvement SALE : quantité négative, coût = CMUP du site, numéro de la vente.
    assert _movements(owner_db) == [("-3.000", "VTE-000001", "100.0000")]
    row = owner_db.execute(
        text(
            "SELECT site_id::text, article_id::text, source_type FROM stock_movements "
            "WHERE movement_type = 'SALE'"
        )
    ).one()
    assert tuple(row) == (world.site, world.articles[0], "sale")
    journal = world.owner.get("/stock/movements", params={"movement_type": "SALE"}).json()
    assert journal["items"][0]["document_number"] == "VTE-000001"


def test_insufficient_stock_leaves_everything_unchanged(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "2", "100")])
    sale = _sale(world, [(0, "3")])
    refused = _validate(world.owner, sale)
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"
    assert refused.json()["articles"] == [
        {
            "article_id": world.articles[0],
            "site_id": world.site,
            "reference": "A-0",
            "available": "2.000",
        }
    ]
    assert world.owner.get(f"/sales/{sale['id']}").json()["status"] == "DRAFT"
    assert sh.level(owner_db, world, 0)[0] == "2.000"
    assert _movements(owner_db) == []
    audit = world.owner.get("/audit-logs", params={"action": "sale.validated"}).json()
    assert audit["total"] == 0  # aucune trace d'une opération annulée


def test_validation_is_atomic_across_lines(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "1", "100"), (2, "10", "100")])
    sale = _sale(world, [(0, "2"), (1, "3"), (2, "1")])
    refused = _validate(world.owner, sale)
    assert refused.json()["code"] == "insufficient_stock"
    assert [a["reference"] for a in refused.json()["articles"]] == ["A-1"]
    for index, quantity in ((0, "10.000"), (1, "1.000"), (2, "10.000")):
        assert sh.level(owner_db, world, index)[0] == quantity  # aucune ligne sortie
    assert _movements(owner_db) == []


def test_second_validation_is_refused(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    sale = _sale(world, [(0, "3")])
    assert _validate(world.owner, sale).status_code == 200
    again = _validate(world.owner, sale)
    assert again.status_code == 409 and again.json()["code"] == "sale_not_draft"
    assert sh.level(owner_db, world, 0)[0] == "7.000"
    assert len(_movements(owner_db)) == 1


def test_validated_sale_is_immutable(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "10", "100")])
    customer = _customer(world)
    sale = _sale(world, [(0, "1")])
    _validate(world.owner, sale)
    for body in (
        {"lines": [{"article_id": world.articles[0], "quantity": "5"}]},
        {"lines": [{"article_id": world.articles[1], "quantity": "1"}]},
        {
            "customer_id": customer["id"],
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        },
        {
            "site_id": world.site2,
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        },
    ):
        response = world.owner.put(f"/sales/{sale['id']}", json=body)
        assert response.status_code == 409 and response.json()["code"] == "sale_not_draft"
    after = world.owner.get(f"/sales/{sale['id']}").json()
    assert (after["total"], after["customer_id"], after["site_id"]) == ("150.00", None, world.site)


def test_draft_update_and_price_change_before_validation(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "10", "100")])
    customer = _customer(world)
    sale = _sale(world, [(0, "1")])
    updated = world.owner.put(
        f"/sales/{sale['id']}",
        json={
            "customer_id": customer["id"],
            "notes": "Livraison",
            "lines": [
                {"article_id": world.articles[1], "quantity": "2"},
                {"article_id": world.articles[0], "quantity": "1"},
            ],
        },
    ).json()
    assert updated["total"] == "450.00" and updated["customer_code"] == "CLI-000001"
    assert [line["line_no"] for line in updated["lines"]] == [1, 2]
    # Le site d'une vente n'est jamais modifiable (champ ignoré en modification).
    assert updated["site_id"] == world.site

    world.owner.patch(f"/catalog/articles/{world.articles[1]}", json={"sale_price": "200"})
    changed = _validate(world.owner, sale)
    assert changed.status_code == 409 and changed.json()["code"] == "sale_prices_changed"
    assert changed.json()["articles"] == ["A-1"]
    resaved = world.owner.put(
        f"/sales/{sale['id']}",
        json={
            "customer_id": customer["id"],
            "lines": [
                {"article_id": world.articles[1], "quantity": "2"},
                {"article_id": world.articles[0], "quantity": "1"},
            ],
        },
    ).json()
    assert resaved["total"] == "550.00"  # prix relus dans le catalogue
    assert _validate(world.owner, sale).status_code == 200
    # Prix figé : un changement de catalogue ultérieur ne modifie pas la vente historique.
    world.owner.patch(f"/catalog/articles/{world.articles[1]}", json={"sale_price": "999"})
    assert world.owner.get(f"/sales/{sale['id']}").json()["total"] == "550.00"


def test_customer_deactivated_after_draft_blocks_validation(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    customer = _customer(world)
    sale = _sale(world, [(0, "1")], customer_id=customer["id"])
    world.owner.post(f"/customers/{customer['id']}/deactivate")
    refused = _validate(world.owner, sale)
    assert refused.status_code == 422 and refused.json()["code"] == "customer_inactive"
    world.owner.post(f"/catalog/articles/{world.articles[0]}/deactivate")
    world.owner.post(f"/customers/{customer['id']}/activate")
    assert _validate(world.owner, sale).json()["code"] == "article_inactive"


# --- Annulation --------------------------------------------------------------------------------


def test_cancel_draft_and_validated_sale(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    draft = _sale(world, [(0, "1")])
    short = world.owner.post(f"/sales/{draft['id']}/cancel", json={"reason": "non"})
    assert short.status_code == 422
    abandoned = world.owner.post(f"/sales/{draft['id']}/cancel", json={"reason": "Client parti"})
    assert abandoned.json()["status"] == "CANCELLED"
    assert _movements(owner_db, "CANCELLATION") == []  # aucun effet sur le stock
    assert _validate(world.owner, draft).json()["code"] == "sale_not_draft"

    sale = _sale(world, [(0, "4")])
    _validate(world.owner, sale)
    assert sh.level(owner_db, world, 0)[0] == "6.000"
    cancelled = world.owner.post(
        f"/sales/{sale['id']}/cancel", json={"reason": "Erreur de caisse"}
    ).json()
    assert cancelled["status"] == "CANCELLED" and cancelled["cancellation_reason"]
    # Remise en stock par mouvement inverse, au coût de la sortie ; CMUP inchangé.
    assert sh.level(owner_db, world, 0) == ("10.000", "100.0000")
    assert _movements(owner_db, "CANCELLATION") == [("4.000", "VTE-000002", "100.0000")]
    linked = owner_db.execute(
        text(
            "SELECT count(*) FROM stock_movements c JOIN stock_movements o "
            "ON c.origin_movement_id = o.id "
            "WHERE c.movement_type = 'CANCELLATION' AND o.movement_type = 'SALE'"
        )
    ).scalar_one()
    assert linked == 1
    # Le mouvement SALE d'origine reste intact (historique).
    assert _movements(owner_db) == [("-4.000", "VTE-000002", "100.0000")]
    again = world.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Encore une fois"})
    assert again.status_code == 409 and again.json()["code"] == "sale_already_cancelled"


# --- Concurrence -------------------------------------------------------------------------------


def test_concurrent_sales_never_oversell(world: World, app: Any, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "5", "100")])
    sales = [_sale(world, [(0, "4")]) for _ in range(2)]
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def validate(sale: dict[str, Any]) -> None:
        with TestClient(app) as client:
            api = Api(client, world.owner.token)
            barrier.wait()
            statuses.append(_validate(api, sale).status_code)

    threads = [threading.Thread(target=validate, args=(s,)) for s in sales]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(statuses) == [200, 422]
    assert sh.level(owner_db, world, 0)[0] == "1.000"
    assert len(_movements(owner_db)) == 1


def test_concurrent_double_validation_of_same_sale(
    world: World, app: Any, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    sale = _sale(world, [(0, "3")])
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def validate() -> None:
        with TestClient(app) as client:
            api = Api(client, world.owner.token)
            barrier.wait()
            statuses.append(_validate(api, sale).status_code)

    threads = [threading.Thread(target=validate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(statuses) == [200, 409]
    assert sh.level(owner_db, world, 0)[0] == "7.000"
    assert len(_movements(owner_db)) == 1


# --- Liste --------------------------------------------------------------------------------------


def test_list_filters_and_sort(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    customer = _customer(world, "Moussa Ouédraogo")
    first = _sale(world, [(0, "1")])
    second = _sale(world, [(0, "3")], customer_id=customer["id"])
    _validate(world.owner, second)
    other_site = world.owner.post(
        "/sales",
        json={
            "site_id": world.site2,
            "lines": [{"article_id": world.articles[1], "quantity": "1"}],
        },
    ).json()

    def numbers(**params: str) -> list[str]:
        response = world.owner.get("/sales", params=params)
        assert response.status_code == 200, response.text
        return [s["number"] for s in response.json()["items"]]

    assert numbers() == [other_site["number"], second["number"], first["number"]]
    assert numbers(status="VALIDATED") == [second["number"]]
    assert numbers(site_id=world.site2) == [other_site["number"]]
    assert numbers(customer_id=customer["id"]) == [second["number"]]
    assert numbers(search="ouédraogo") == [second["number"]]
    assert numbers(search="vte-000001") == [first["number"]]
    assert numbers(sort="-total")[0] == second["number"]
    assert numbers(date_from="2999-01-01") == []
    page = world.owner.get("/sales", params={"limit": 1}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert world.owner.get("/sales", params={"sort": "site"}).json()["code"] == "invalid_sort"


# --- RBAC, site, abonnement --------------------------------------------------------------------


def test_permissions_by_base_role(world: World, client: Any) -> None:
    sh.validated_entry(world, [(0, "20", "100")])
    admin = sh.member(world, client, "admin@example.com", "administrator", all_sites=True)
    manager = sh.member(world, client, "gestion@example.com", "manager", all_sites=True)
    seller = sh.member(world, client, "vendeur@example.com", "seller", all_sites=True)
    viewer = sh.member(world, client, "consultant@example.com", "viewer", all_sites=True)
    body = {"site_id": world.site, "lines": [{"article_id": world.articles[0], "quantity": "1"}]}

    # Vendeur, Gestionnaire, Administrateur : vente complète (création → validation).
    for api in (seller, manager, admin):
        created = api.post("/sales", json=body)
        assert created.status_code == 201, created.text
        sale = created.json()
        assert api.put(f"/sales/{sale['id']}", json=body).status_code == 200
        assert _validate(api, sale).status_code == 200
    # Annulation : administration seulement.
    sale = world.owner.get("/sales", params={"status": "VALIDATED"}).json()["items"][0]
    for api in (seller, manager, viewer):
        denied = api.post(f"/sales/{sale['id']}/cancel", json={"reason": "Tentative"})
        assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    assert admin.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur"}).status_code == 200
    # Consultant : consultation seulement.
    assert viewer.get("/sales").status_code == 200
    assert viewer.get(f"/sales/{sale['id']}").status_code == 200
    assert viewer.post("/sales", json=body).json()["code"] == "permission_denied"


def test_site_scope(world: World, client: Any) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    depot_sale = world.owner.post(
        "/sales",
        json={
            "site_id": world.site2,
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        },
    ).json()
    shop_seller = sh.member(world, client, "boutique@example.com", "seller", site_ids=[world.site])
    denied = shop_seller.post(
        "/sales",
        json={
            "site_id": world.site2,
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        },
    )
    assert denied.status_code == 403 and denied.json()["code"] == "site_access_denied"
    assert shop_seller.get("/sales").json()["total"] == 0
    for response in (
        shop_seller.get(f"/sales/{depot_sale['id']}"),
        shop_seller.put(
            f"/sales/{depot_sale['id']}",
            json={"lines": [{"article_id": world.articles[0], "quantity": "2"}]},
        ),
        _validate(shop_seller, depot_sale),
    ):
        assert response.status_code == 404 and response.json()["code"] == "sale_not_found"
    own = shop_seller.post(
        "/sales",
        json={"site_id": world.site, "lines": [{"article_id": world.articles[0], "quantity": "1"}]},
    )
    assert own.status_code == 201
    assert _validate(shop_seller, own.json()).status_code == 200


def test_expired_subscription_blocks_sales(world: World, owner_db: Session) -> None:
    sale = _sale(world, [(0, "1")])
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert world.owner.get("/sales").status_code == 200
    blocked = _validate(world.owner, sale)
    assert blocked.status_code == 403 and blocked.json()["code"] == "subscription_restricted"


# --- Audit -----------------------------------------------------------------------------------


def test_audit_trail(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    sale = _sale(world, [(0, "1")])
    world.owner.put(
        f"/sales/{sale['id']}", json={"lines": [{"article_id": world.articles[0], "quantity": "2"}]}
    )
    _validate(world.owner, sale)
    world.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur de saisie"})
    items = world.owner.get("/audit-logs", params={"action": "sale."}).json()["items"]
    assert [i["action"] for i in items] == [
        "sale.cancelled",
        "sale.validated",
        "sale.updated",
        "sale.created",
    ]
    cancelled, validated, updated, created = (i["data"] for i in items)
    assert created["number"] == "VTE-000001" and created["total"] == "150.00"
    assert updated["before"]["lines"][0]["quantity"] == "1.000"
    assert updated["after"]["lines"][0]["quantity"] == "2.000"
    assert (validated["previous_status"], validated["status"], validated["total"]) == (
        "DRAFT",
        "VALIDATED",
        "300.00",
    )
    assert (cancelled["previous_status"], cancelled["stock_restored"]) == ("VALIDATED", True)
    assert all(i["site_id"] == world.site and i["entity_type"] == "sale" for i in items)
    assert all(i["user_name"] for i in items)


# --- Multi-tenant ----------------------------------------------------------------------------


def test_isolation_between_tenants_api(world: World, provision: Any, api_for: Any) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    a_customer = _customer(world)
    a_sale = _sale(world, [(0, "1")], customer_id=a_customer["id"])
    provision("beta")
    beta: Api = api_for("owner@beta.example.com")
    assert beta.get("/sales").json()["total"] == 0
    url = f"/sales/{a_sale['id']}"
    for response in (
        beta.get(url),
        beta.put(url, json={"lines": [{"article_id": world.articles[0], "quantity": "9"}]}),
        _validate(beta, a_sale),
        beta.post(f"{url}/cancel", json={"reason": "Piratage"}),
    ):
        assert response.status_code == 404 and response.json()["code"] == "sale_not_found"
    beta_site = beta.get("/sites").json()[0]["id"]
    foreign_article = beta.post(
        "/sales",
        json={"site_id": beta_site, "lines": [{"article_id": world.articles[0], "quantity": "1"}]},
    )
    assert foreign_article.json()["code"] == "article_not_found"
    category = beta.post("/catalog/categories", json={"name": "B"}).json()
    beta_article = beta.post(
        "/catalog/articles",
        json={
            "reference": "B-1",
            "designation": "B",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "2",
        },
    ).json()["id"]
    line = [{"article_id": beta_article, "quantity": "1"}]
    foreign_customer = beta.post(
        "/sales", json={"site_id": beta_site, "customer_id": a_customer["id"], "lines": line}
    )
    assert foreign_customer.json()["code"] == "customer_not_found"
    foreign_site = beta.post("/sales", json={"site_id": world.site, "lines": line})
    assert foreign_site.json()["code"] == "site_access_denied"
    assert world.owner.get(url).json()["status"] == "DRAFT"


def test_isolation_with_app_role_and_rls(
    world: World, provision: Any, app_engine: Engine, owner_db: Session
) -> None:
    sale = _sale(world, [(0, "1")])
    b = provision("beta")
    tenant_a = owner_db.execute(text("SELECT tenant_id FROM sales")).scalar_one()
    with create_session_factory(app_engine)() as db:
        assert db.execute(text("SELECT count(*) FROM sales")).scalar_one() == 0  # sans contexte
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.execute(text("SELECT count(*) FROM sales")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM sale_lines")).scalar_one() == 0
        updated = db.execute(text("UPDATE sales SET total = 0 WHERE id = :id"), {"id": sale["id"]})
        assert updated.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO sales (id, tenant_id, number, site_id, status, sale_date, "
                    "subtotal, total) VALUES (:id, :tenant, 'VTE-X', :site, 'DRAFT', "
                    "current_date, 0, 0)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_a, "site": world.site},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM sales"))  # jamais de suppression physique
    # Clés composites : une vente ne peut pas référencer le site d'un autre tenant, même
    # en contournant l'application (session propriétaire).
    with pytest.raises(IntegrityError):
        owner_db.execute(
            text(
                "INSERT INTO sales (id, tenant_id, number, site_id, status, sale_date, "
                "subtotal, total) VALUES (:id, :tenant, 'VTE-Y', :site, 'DRAFT', "
                "current_date, 0, 0)"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a, "site": b.site_id},
        )
    owner_db.rollback()
