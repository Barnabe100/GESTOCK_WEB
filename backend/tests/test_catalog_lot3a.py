"""Lot 3-A (ADR-0039) — catalogue : scan EXACT du code-barres au point de vente, articles gérés
ou non en stock (``stock_managed``), historique des prix lu dans l'audit, permissions
distinctes ``catalog.article.price_update`` (prix) et ``catalog.article.cost_view`` (coûts
internes absents des réponses sans elle)."""

import threading
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

PRICE_UPDATE = "catalog.article.price_update"
COST_VIEW = "catalog.article.cost_view"
COST_KEYS = {
    "purchase_price",
    "average_cost",
    "average_cost_before",
    "average_cost_after",
    "stock_value",
    "unit_cost",
    "amount",
    "total_amount",
    "adjustment_value",
    "surplus_value",
    "shortage_value",
}


@pytest.fixture
def priced(world: World, owner_db: Session) -> World:
    """Article 0 à 10 000 (code-barres 3017620422003), 50 en boutique ; article 1 à 2 500."""
    for index, price, barcode in ((0, 10000, "3017620422003"), (1, 2500, None)):
        owner_db.execute(
            text("UPDATE catalog_articles SET sale_price = :p, barcode = :b WHERE id = :id"),
            {"p": price, "b": barcode, "id": world.articles[index]},
        )
    owner_db.commit()
    sh.validated_entry(world, [(0, "50", "6000"), (1, "20", "1000")])
    return world


def _custom_member(
    w: World, client: TestClient, email: str, permissions: list[str], **access: Any
) -> Api:
    role = w.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions})
    assert role.status_code == 201, role.text
    created = w.owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": [{"role_id": role.json()["id"]}],
            **(access or {"all_sites": True}),
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _service(w: World, reference: str = "SRV-1", price: str = "5000") -> dict[str, Any]:
    category = w.owner.get("/catalog/categories").json()["items"][0]["id"]
    response = w.owner.post(
        "/catalog/articles",
        json={
            "reference": reference,
            "designation": f"Service {reference}",
            "category_id": category,
            "unit": "forfait",
            "sale_price": price,
            "stock_managed": False,
            "site_ids": [w.site, w.site2],  # assortiment des deux sites (ADR-0046)
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["stock_managed"] is False
    return dict(response.json())


def _scan(api: Api, w: World, barcode: str) -> Any:
    return api.get("/pos/articles/by-barcode", params={"site_id": w.site, "barcode": barcode})


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for item in value for k in _keys(item)}
    return set()


def _movements(owner_db: Session, article_id: str) -> int:
    owner_db.expire_all()
    return int(
        owner_db.execute(
            text("SELECT count(*) FROM stock_movements WHERE article_id = :a"), {"a": article_id}
        ).scalar_one()
    )


def _sell(w: World, article_id: str, quantity: str, api: Api | None = None) -> Any:
    sale = (api or w.owner).post(
        "/sales",
        json={"site_id": w.site, "lines": [{"article_id": article_id, "quantity": quantity}]},
    )
    assert sale.status_code == 201, sale.text
    return (api or w.owner).post(
        f"/sales/{sale.json()['id']}/validate",
        json={"payments": [{"amount": sale.json()["total"], "method": "CASH"}]},
    )


# --- 1. Scan exact du code-barres --------------------------------------------------------------


def test_scan_exact_barcode(priced: World) -> None:
    found = _scan(priced.owner, priced, "3017620422003")
    assert found.status_code == 200, found.text
    body = found.json()
    assert (body["article_id"], body["quantity"], body["stock_managed"]) == (
        priced.articles[0],
        "50.000",
        True,
    )
    # Espaces autour du code (douchette) tolérés ; jamais de correspondance partielle.
    assert _scan(priced.owner, priced, " 3017620422003 ").status_code == 200
    for unknown in ("0000000000000", "301762042200", "3017620422003X", "A-0", "Article 0", "A"):
        response = _scan(priced.owner, priced, unknown)
        assert response.status_code == 404, unknown
        assert response.json()["code"] == "barcode_unknown"


def test_scan_inactive_article_is_unknown(priced: World) -> None:
    assert (
        priced.owner.post(f"/catalog/articles/{priced.articles[0]}/deactivate").status_code == 200
    )
    response = _scan(priced.owner, priced, "3017620422003")
    assert response.status_code == 404 and response.json()["code"] == "barcode_unknown"


def test_scan_is_isolated_between_tenants(
    priced: World, provision: Any, api_for: Any, client: TestClient
) -> None:
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    response = other.get(
        "/pos/articles/by-barcode",
        params={"site_id": str(beta.site_id), "barcode": "3017620422003"},
    )
    assert response.status_code == 404 and response.json()["code"] == "barcode_unknown"
    # Sans le droit d'utiliser le point de vente : refusé.
    viewer = sh.member(priced, client, "consultant@example.com", "viewer", all_sites=True)
    assert _scan(viewer, priced, "3017620422003").status_code == 403


def test_scanned_unmanaged_article(priced: World) -> None:
    service = _service(priced)
    assert (
        priced.owner.patch(
            f"/catalog/articles/{service['id']}", json={"barcode": "9990001112223"}
        ).status_code
        == 200
    )
    body = _scan(priced.owner, priced, "9990001112223").json()
    assert (body["article_id"], body["stock_managed"]) == (service["id"], False)
    assert Decimal(body["quantity"]) == 0


# --- 2. Articles gérés ou non en stock ---------------------------------------------------------


def test_stock_managed_defaults_to_true(world: World, owner_db: Session) -> None:
    category = world.owner.get("/catalog/categories").json()["items"][0]["id"]
    created = world.owner.post(
        "/catalog/articles",
        json={"reference": "N-1", "designation": "Nouveau", "category_id": category, "unit": "u"},
    )
    assert created.status_code == 201 and created.json()["stock_managed"] is True
    # Articles existants (avant la migration 0026) : tous gérés en stock ; colonne obligatoire.
    assert all(a["stock_managed"] for a in world.owner.get("/catalog/articles").json()["items"])
    column = owner_db.execute(
        text(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'catalog_articles' AND column_name = 'stock_managed'"
        )
    ).one()
    assert column[0] == "NO" and column[1] == "true"


def test_unmanaged_article_is_sold_without_stock(priced: World, owner_db: Session) -> None:
    service = _service(priced)
    sold = _sell(priced, service["id"], "3")
    assert sold.status_code == 200, sold.text
    assert sold.json()["status"] == "VALIDATED"
    assert _movements(owner_db, service["id"]) == 0
    # Vente mixte : seul l'article géré sort du stock.
    sale = priced.owner.post(
        "/sales",
        json={
            "site_id": priced.site,
            "lines": [
                {"article_id": service["id"], "quantity": "1"},
                {"article_id": priced.articles[1], "quantity": "2"},
            ],
        },
    ).json()
    validated = priced.owner.post(
        f"/sales/{sale['id']}/validate",
        json={"payments": [{"amount": sale["total"], "method": "CASH"}]},
    )
    assert validated.status_code == 200, validated.text
    assert _movements(owner_db, service["id"]) == 0
    assert sh.level(owner_db, priced, 1)[0] == "18.000"
    # Annulation : seul le stock réellement sorti revient.
    payments = priced.owner.get(f"/sales/{sale['id']}/payments").json()["items"]
    for payment in payments:
        priced.owner.post(
            f"/sales/{sale['id']}/payments/{payment['id']}/cancel", json={"reason": "Erreur caisse"}
        )
    cancelled = priced.owner.post(f"/sales/{sale['id']}/cancel", json={"reason": "Erreur caisse"})
    assert cancelled.status_code == 200, cancelled.text
    assert sh.level(owner_db, priced, 1)[0] == "20.000"
    assert _movements(owner_db, service["id"]) == 0


def test_unmanaged_article_at_pos(priced: World, owner_db: Session) -> None:
    service = _service(priced)
    checkout = priced.owner.post(
        "/pos/checkout",
        json={
            "site_id": priced.site,
            "lines": [{"article_id": service["id"], "quantity": "10"}],
            "payments": [{"amount": "50000", "method": "CASH"}],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert checkout.status_code == 201, checkout.text
    assert _movements(owner_db, service["id"]) == 0
    found = priced.owner.get("/pos/articles", params={"site_id": priced.site, "search": "SRV"})
    assert [a["stock_managed"] for a in found.json()] == [False]


def test_unmanaged_article_refused_in_stock_operations(priced: World) -> None:
    service = _service(priced)
    lines = [{"article_id": service["id"], "quantity": "1"}]
    attempts = [
        priced.owner.post(
            "/stock/entries",
            json={
                "site_id": priced.site,
                "supplier_id": priced.supplier,
                "lines": [{"article_id": service["id"], "quantity": "1", "unit_cost": "10"}],
            },
        ),
        priced.owner.post(
            "/stock/exits",
            json={"site_id": priced.site, "reason_id": priced.reasons["PERTE"], "lines": lines},
        ),
        priced.owner.post(
            "/stock/transfers",
            json={
                "source_site_id": priced.site,
                "destination_site_id": priced.site2,
                "lines": lines,
            },
        ),
        priced.owner.post(
            "/inventories",
            json={
                "site_id": priced.site,
                "inventory_type": "TARGETED",
                "article_ids": [service["id"]],
            },
        ),
        priced.owner.put(
            f"/stock/levels/{priced.site}/{service['id']}/thresholds",
            json={"min_stock": "1", "max_stock": None},
        ),
    ]
    for response in attempts:
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == "article_not_stock_managed"
        assert body["articles"] == ["SRV-1"]


def test_unmanaged_article_out_of_levels_alerts_and_inventories(priced: World) -> None:
    service = _service(priced)
    levels = priced.owner.get("/stock/levels", params={"limit": 100}).json()["items"]
    assert service["id"] not in {level["article_id"] for level in levels}
    alerts = priced.owner.get("/alerts/stock", params={"limit": 100}).json()["items"]
    assert service["id"] not in {a["article_id"] for a in alerts}
    candidates = priced.owner.get(
        "/inventories/candidates", params={"site_id": priced.site, "limit": 100}
    ).json()["items"]
    assert service["id"] not in {c["article_id"] for c in candidates}


def test_managed_to_unmanaged_requires_zero_stock_everywhere(
    priced: World, owner_db: Session
) -> None:
    article = priced.articles[1]  # 20 en boutique
    refused = priced.owner.patch(f"/catalog/articles/{article}", json={"stock_managed": False})
    assert refused.status_code == 409 and refused.json()["code"] == "article_has_stock"
    assert refused.json()["sites"] == ["Boutique"] or len(refused.json()["sites"]) == 1
    # Ramené à zéro sur le site principal, mais présent au dépôt : toujours refusé.
    exit_doc = sh.exit_doc(priced, [(1, "20")])
    assert priced.owner.post(f"/stock/exits/{exit_doc['id']}/validate").status_code == 200
    sh.validated_entry(priced, [(1, "4", "1000")], site_id=priced.site2)
    refused = priced.owner.patch(f"/catalog/articles/{article}", json={"stock_managed": False})
    assert refused.status_code == 409 and len(refused.json()["sites"]) == 1
    depot_exit = priced.owner.post(
        "/stock/exits",
        json={
            "site_id": priced.site2,
            "reason_id": priced.reasons["PERTE"],
            "lines": [{"article_id": article, "quantity": "4"}],
        },
    ).json()
    assert priced.owner.post(f"/stock/exits/{depot_exit['id']}/validate").status_code == 200
    movements = _movements(owner_db, article)
    allowed = priced.owner.patch(f"/catalog/articles/{article}", json={"stock_managed": False})
    assert allowed.status_code == 200 and allowed.json()["stock_managed"] is False
    # Aucun ajustement ni mouvement artificiel.
    assert _movements(owner_db, article) == movements


def test_unmanaged_to_managed_creates_nothing(priced: World, owner_db: Session) -> None:
    service = _service(priced)
    _sell(priced, service["id"], "2")
    managed = priced.owner.patch(f"/catalog/articles/{service['id']}", json={"stock_managed": True})
    assert managed.status_code == 200 and managed.json()["stock_managed"] is True
    assert _movements(owner_db, service["id"]) == 0
    level = priced.owner.get(
        "/stock/levels", params={"site_id": priced.site, "search": "SRV-1"}
    ).json()["items"]
    assert [(lv["article_id"], Decimal(lv["quantity"])) for lv in level] == [(service["id"], 0)]
    # Désormais suivi : la vente exige du stock.
    refused = _sell(priced, service["id"], "1")
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"


def test_active_status_is_independent(priced: World, owner_db: Session) -> None:
    exit_doc = sh.exit_doc(priced, [(1, "20")])
    priced.owner.post(f"/stock/exits/{exit_doc['id']}/validate")
    article = priced.owner.get(f"/catalog/articles/{priced.articles[1]}").json()
    assert article["is_active"] is True  # stock nul ≠ article inactif
    service = _service(priced)
    assert priced.owner.get(f"/catalog/articles/{service['id']}").json()["is_active"] is True


# --- 3. Prix : permission distincte et historique ----------------------------------------------


def test_price_update_permission(priced: World, client: TestClient) -> None:
    article = priced.articles[0]
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    refused = manager.patch(f"/catalog/articles/{article}", json={"sale_price": "12000"})
    assert refused.status_code == 403 and refused.json()["code"] == "price_update_not_allowed"
    refused = manager.patch(f"/catalog/articles/{article}", json={"purchase_price": "1"})
    assert refused.status_code == 403
    # Informations générales : autorisées ; prix renvoyé inchangé (formulaire) : accepté.
    renamed = manager.patch(
        f"/catalog/articles/{article}", json={"designation": "Article zéro", "sale_price": "10000"}
    )
    assert renamed.status_code == 200 and renamed.json()["designation"] == "Article zéro"
    # Création : prix seulement avec price_update ; sans prix, article créé à 0.
    category = priced.owner.get("/catalog/categories").json()["items"][0]["id"]
    base = {"designation": "Nouveau", "category_id": category, "unit": "u"}
    refused = manager.post(
        "/catalog/articles", json={**base, "reference": "M-1", "sale_price": "5"}
    )
    assert refused.status_code == 403 and refused.json()["code"] == "price_update_not_allowed"
    created = manager.post("/catalog/articles", json={**base, "reference": "M-2"})
    assert created.status_code == 201 and created.json()["sale_price"] == "0.00"

    # Prix seulement : le prix oui, les informations générales non.
    pricer = _custom_member(
        priced, client, "prix@example.com", ["catalog.article.view", PRICE_UPDATE]
    )
    changed = pricer.patch(f"/catalog/articles/{article}", json={"sale_price": "11000"})
    assert changed.status_code == 200 and changed.json()["sale_price"] == "11000.00"
    refused = pricer.patch(f"/catalog/articles/{article}", json={"designation": "Autre"})
    assert refused.status_code == 403 and refused.json()["code"] == "permission_denied"
    viewer = sh.member(priced, client, "consultant@example.com", "viewer", all_sites=True)
    assert viewer.patch(f"/catalog/articles/{article}", json={"sale_price": "1"}).status_code == 403


def test_price_history_from_audit(
    priced: World, client: TestClient, provision: Any, api_for: Any
) -> None:
    category = priced.owner.get("/catalog/categories").json()["items"][0]["id"]
    article = priced.owner.post(
        "/catalog/articles",
        json={
            "reference": "H-1",
            "designation": "Historique",
            "category_id": category,
            "unit": "u",
            "sale_price": "100",
            "purchase_price": "60",
        },
    ).json()
    url = f"/catalog/articles/{article['id']}"
    priced.owner.patch(url, json={"sale_price": "120"})
    priced.owner.patch(url, json={"designation": "Historique 2"})  # pas un changement de prix
    priced.owner.patch(url, json={"purchase_price": "70", "sale_price": "130"})
    history = priced.owner.get(f"{url}/price-history")
    assert history.status_code == 200, history.text
    items = history.json()["items"]
    assert history.json()["total"] == 3
    assert [(i["sale_price_before"], i["sale_price_after"]) for i in items] == [
        ("120.00", "130.00"),
        ("100.00", "120.00"),
        (None, "100.00"),
    ]
    assert [(i["purchase_price_before"], i["purchase_price_after"]) for i in items] == [
        ("60.00", "70.00"),
        (None, None),
        (None, "60.00"),
    ]
    assert items[0]["user_name"] == "Owner alpha" and items[0]["occurred_at"]
    paged = priced.owner.get(f"{url}/price-history", params={"limit": 1, "offset": 1}).json()
    assert paged["total"] == 3 and [i["sale_price_after"] for i in paged["items"]] == ["120.00"]

    # Habilité aux prix sans cost_view : prix de vente seulement, prix d'achat absent.
    pricer = _custom_member(
        priced, client, "prix@example.com", ["catalog.article.view", PRICE_UPDATE]
    )
    limited = pricer.get(f"{url}/price-history").json()
    assert limited["total"] == 3
    assert all("purchase_price_before" not in i for i in limited["items"])
    # Consultation simple (ni prix ni journal d'audit) : refusé.
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    assert seller.get(f"{url}/price-history").status_code == 403
    # Autre entreprise : introuvable.
    provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    assert other.get(f"{url}/price-history").status_code == 404


# --- 4. Coûts internes ---------------------------------------------------------------------------


def test_costs_visible_with_cost_view(priced: World) -> None:
    article = priced.owner.get(f"/catalog/articles/{priced.articles[0]}").json()
    assert article["purchase_price"] == "100.00"
    level = priced.owner.get(
        "/stock/levels", params={"site_id": priced.site, "search": "A-0"}
    ).json()["items"][0]
    assert level["average_cost"] == "6000.0000"
    movements = priced.owner.get("/stock/movements").json()["items"]
    assert movements and "unit_cost" in movements[0]


def test_costs_absent_without_cost_view(priced: World, client: TestClient) -> None:
    sh.validated_entry(priced, [(0, "5", "6100")])
    exit_doc = sh.exit_doc(priced, [(1, "1")])
    priced.owner.post(f"/stock/exits/{exit_doc['id']}/validate")
    inventory = priced.owner.post(
        "/inventories",
        json={
            "site_id": priced.site,
            "inventory_type": "TARGETED",
            "article_ids": [priced.articles[0]],
        },
    ).json()
    # Rôle personnalisé : mêmes consultations qu'un Gestionnaire, SANS cost_view.
    manager = _custom_member(
        priced,
        client,
        "sans-couts@example.com",
        [
            "catalog.article.view",
            "catalog.article.update",
            "stock.level.view",
            "alerts.stock.view",
            "stock.movement.view",
            "stock.entry.view",
            "stock.exit.view",
            "inventory_count.inventory.view",
            "pos.terminal.use",
        ],
    )
    entry = priced.owner.get("/stock/entries").json()["items"][0]
    responses = {
        "articles": manager.get("/catalog/articles"),
        "article": manager.get(f"/catalog/articles/{priced.articles[0]}"),
        "barcode": manager.get("/catalog/articles/by-barcode/3017620422003"),
        "update": manager.patch(
            f"/catalog/articles/{priced.articles[0]}", json={"designation": "Renommé"}
        ),
        "levels": manager.get("/stock/levels"),
        "alerts": manager.get("/alerts/stock"),
        "movements": manager.get("/stock/movements"),
        "entries": manager.get("/stock/entries"),
        "entry": manager.get(f"/stock/entries/{entry['id']}"),
        "exit": manager.get(f"/stock/exits/{exit_doc['id']}"),
        "inventory": manager.get(f"/inventories/{inventory['id']}"),
        "inventory_lines": manager.get(f"/inventories/{inventory['id']}/lines"),
        "pos": manager.get("/pos/articles", params={"site_id": priced.site}),
    }
    for name, response in responses.items():
        assert response.status_code == 200, (name, response.text)
        leaked = _keys(response.json()) & COST_KEYS
        assert not leaked, (name, leaked)
    # Trier par prix d'achat révélerait l'ordre des coûts : refusé.
    assert manager.get("/catalog/articles", params={"sort": "purchase_price"}).status_code == 400
    # Avec cost_view : les mêmes réponses portent les coûts.
    costs = _custom_member(
        priced,
        client,
        "couts@example.com",
        ["catalog.article.view", COST_VIEW, "stock.level.view", "stock.movement.view"],
    )
    assert "purchase_price" in costs.get(f"/catalog/articles/{priced.articles[0]}").json()
    assert "average_cost" in costs.get("/stock/levels").json()["items"][0]


def test_manager_sees_costs_but_cannot_change_prices(priced: World, client: TestClient) -> None:
    """Rôle de base Gestionnaire (décision du Lot 3-A) : coûts opérationnels visibles
    (``cost_view``), prix catalogue non modifiables (pas de ``price_update``)."""
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    article = manager.get(f"/catalog/articles/{priced.articles[0]}").json()
    assert article["purchase_price"] == "100.00"
    level = manager.get("/stock/levels", params={"site_id": priced.site, "search": "A-0"})
    assert level.json()["items"][0]["average_cost"] == "6000.0000"
    entry = manager.get("/stock/entries").json()["items"][0]
    detail = manager.get(f"/stock/entries/{entry['id']}").json()
    assert detail["lines"][0]["unit_cost"] and "total_amount" in detail
    assert "unit_cost" in manager.get("/stock/movements").json()["items"][0]
    assert manager.get("/catalog/articles", params={"sort": "purchase_price"}).status_code == 200
    for body in ({"sale_price": "12000"}, {"purchase_price": "1"}):
        refused = manager.patch(f"/catalog/articles/{priced.articles[0]}", json=body)
        assert refused.status_code == 403
        assert refused.json()["code"] == "price_update_not_allowed"


def test_audit_log_costs_require_cost_view(priced: World, client: TestClient) -> None:
    priced.owner.patch(f"/catalog/articles/{priced.articles[0]}", json={"purchase_price": "150"})
    auditor = _custom_member(priced, client, "audit@example.com", ["audit.log.view"])
    logs = auditor.get("/audit-logs", params={"action": "article.updated"}).json()["items"]
    assert logs and not (_keys(logs) & COST_KEYS)
    owner_logs = priced.owner.get("/audit-logs", params={"action": "article.updated"}).json()
    assert owner_logs["items"][0]["data"]["purchase_price"] == {
        "before": "100.00",
        "after": "150.00",
    }


# --- RBAC ----------------------------------------------------------------------------------------


def test_default_roles(world: World) -> None:
    roles = {r["template_code"]: r for r in world.owner.get("/roles").json()}
    permissions = {code: set(role["permission_codes"]) for code, role in roles.items() if code}
    assert {PRICE_UPDATE, COST_VIEW, "catalog.article.update"} <= permissions["administrator"]
    # Gestionnaire : informations générales et coûts, jamais les prix.
    assert {"catalog.article.update", COST_VIEW} <= permissions["manager"]
    assert PRICE_UPDATE not in permissions["manager"]
    for code in ("seller", "viewer"):
        assert not ({PRICE_UPDATE, COST_VIEW} & permissions[code]), code
    assert "catalog.article.view" in permissions["seller"]


def _race(app: Any, token: str, calls: list[tuple[str, str, str, Any]]) -> dict[str, int]:
    """Requêtes lancées en même temps (barrière), chacune avec sa propre connexion."""
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


def test_concurrent_unmanage_and_stock_entry_never_both_succeed(
    priced: World, app: Any, owner_db: Session
) -> None:
    """Passage « géré » → « non géré » et validation d'une entrée simultanés : l'un attend
    l'autre (verrou exclusif / partagé de l'article) ; jamais d'article non géré avec du stock."""
    category = priced.owner.get("/catalog/categories").json()["items"][0]["id"]
    for attempt in range(3):
        article = priced.owner.post(
            "/catalog/articles",
            json={
                "reference": f"C-{attempt}",
                "designation": "Concurrence",
                "category_id": category,
                "unit": "u",
                "site_ids": [priced.site],
            },
        ).json()
        entry = priced.owner.post(
            "/stock/entries",
            json={
                "site_id": priced.site,
                "supplier_id": priced.supplier,
                "lines": [{"article_id": article["id"], "quantity": "5", "unit_cost": "10"}],
            },
        ).json()
        statuses = _race(
            app,
            priced.owner.token,
            [
                (
                    "unmanage",
                    "patch",
                    f"/catalog/articles/{article['id']}",
                    {"stock_managed": False},
                ),
                ("entry", "post", f"/stock/entries/{entry['id']}/validate", None),
            ],
        )
        assert sorted(statuses.values()) in ([200, 409], [200, 422]), statuses
        owner_db.expire_all()
        managed, quantity = owner_db.execute(
            text(
                "SELECT a.stock_managed, coalesce(sum(l.quantity), 0) FROM catalog_articles a "
                "LEFT JOIN stock_levels l ON l.article_id = a.id WHERE a.id = :a "
                "GROUP BY a.stock_managed"
            ),
            {"a": article["id"]},
        ).one()
        assert managed or quantity == 0
