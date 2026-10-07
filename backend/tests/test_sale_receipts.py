"""Palier POS — reçu de vente : construit depuis la vente PERSISTÉE (lignes et présentations
figées, paiements, montant reçu, monnaie rendue, reste dû, crédit), sans aucune donnée interne ;
consultation par ``sales.sale.view`` et sa portée ; première impression
(``sales.sale.receipt_print``) et réimpression (``sales.sale.reprint``) contrôlées par le
serveur et journalisées (``sale.receipt_printed``)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.sales.manifest import MANIFEST
from app.platform.registry import AccessKind
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World


@pytest.fixture
def shop(world: World, owner_db: Session) -> World:
    """Article 0 à 1 500 (stock 200) ; article 1 vendu aussi en « Carton 24 » à 10 500."""
    owner_db.execute(
        text("UPDATE catalog_articles SET sale_price = 1500 WHERE id = :id"),
        {"id": world.articles[0]},
    )
    owner_db.commit()
    carton = world.owner.post(
        f"/catalog/articles/{world.articles[1]}/packagings",
        json={"name": "Carton 24", "conversion": "24", "sale_price": "10500"},
    )
    assert carton.status_code == 201, carton.text
    world.reasons["carton"] = carton.json()["id"]
    sh.validated_entry(world, [(0, "200", "600"), (1, "480", "300")])
    return world


def _sale(
    w: World,
    api: Api | None = None,
    *,
    lines: list[dict[str, Any]] | None = None,
    payments: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    api = api or w.owner
    created = api.post(
        "/sales",
        json={
            "site_id": w.site,
            "lines": lines or [{"article_id": w.articles[0], "quantity": "1"}],
            **extra,
        },
    )
    assert created.status_code == 201, created.text
    sale = created.json()
    body = {"payments": payments if payments is not None else []}
    validated = api.post(f"/sales/{sale['id']}/validate", json=body)
    assert validated.status_code == 200, validated.text
    return dict(validated.json())


def _cash(received: str) -> dict[str, str]:
    return {"method": "CASH", "amount_received": received}


def _custom_member(w: World, client: TestClient, email: str, permissions: list[str]) -> Api:
    role = w.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions})
    assert role.status_code == 201, role.text
    created = w.owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": [{"role_id": role.json()["id"]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _prints(owner_db: Session, sale_id: str) -> list[dict[str, Any]]:
    rows = owner_db.execute(
        text(
            "SELECT data FROM audit_logs WHERE action = 'sale.receipt_printed' "
            "AND entity_id = :id ORDER BY occurred_at"
        ),
        {"id": sale_id},
    ).all()
    return [dict(r[0]) for r in rows]


# --- Contenu --------------------------------------------------------------------------------------


def test_receipt_of_cash_sale_with_change_and_packaging(shop: World) -> None:
    """Total 1 500 + 2 Carton 24 × 10 500 = 22 500 ; reçu 25 000 → monnaie rendue 2 500."""
    sale = _sale(
        shop,
        lines=[
            {"article_id": shop.articles[0], "quantity": "1"},
            {
                "article_id": shop.articles[1],
                "packaging_id": shop.reasons["carton"],
                "quantity": "2",
            },
        ],
        payments=[_cash("25000")],
    )
    response = shop.owner.get(f"/sales/{sale['id']}/receipt")
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["number"] == sale["number"]
    assert receipt["site_name"] == sale["site_name"]
    assert receipt["cashier_name"] == "Owner alpha"
    assert receipt["issuer"]["name"] == "Entreprise alpha"
    assert receipt["total"] == "22500.00"
    assert [
        (line["designation"], line["quantity"], line["packaging_name"], line["unit_price"])
        for line in receipt["lines"]
    ] == [
        ("Article 0", "1.000", None, "1500.00"),
        # Présentation réellement vendue, prix du conditionnement figé.
        ("Article 1", "2.000", "Carton 24", "10500.00"),
    ]
    assert receipt["lines"][1]["line_total"] == "21000.00"
    assert receipt["lines"][1]["packaging_conversion"] == "24.000"
    [payment] = receipt["payments"]
    assert (payment["method"], payment["amount"]) == ("CASH", "22500.00")
    assert (payment["amount_received"], payment["change_given"]) == ("25000.00", "2500.00")
    assert (receipt["amount_received"], receipt["change_given"]) == ("25000.00", "2500.00")
    assert (receipt["paid_amount"], receipt["remaining_amount"]) == ("22500.00", "0.00")
    assert (receipt["payment_status"], receipt["is_credit"]) == ("PAID", False)
    assert receipt["print_count"] == 0
    # Aucune donnée interne : coûts, CMUP, lots, identifiants d'articles.
    for forbidden in ("cost", "average", "cmup", "lot", "article_id", "base_quantity"):
        assert forbidden not in response.text


def test_receipt_header_carries_the_tenant_logo_when_configured(shop: World) -> None:
    """Logo : ``logo_url`` existant du tenant (identité documentaire), aucun autre stockage."""
    sale = _sale(shop, payments=[_cash("1500")])
    issuer = shop.owner.get(f"/sales/{sale['id']}/receipt").json()["issuer"]
    assert (issuer["name"], issuer["logo_url"]) == ("Entreprise alpha", None)
    updated = shop.owner.patch("/tenant", json={"logo_url": "https://cdn.example.com/logo.png"})
    assert updated.status_code == 200, updated.text
    issuer = shop.owner.get(f"/sales/{sale['id']}/receipt").json()["issuer"]
    # Nom de l'entreprise conservé à côté du logo.
    assert (issuer["name"], issuer["logo_url"]) == (
        "Entreprise alpha",
        "https://cdn.example.com/logo.png",
    )


def test_exact_cash_payment_gives_no_change(shop: World) -> None:
    sale = _sale(shop, payments=[_cash("1500")])
    receipt = shop.owner.get(f"/sales/{sale['id']}/receipt").json()
    assert (receipt["amount_received"], receipt["change_given"]) == ("1500.00", "0.00")
    assert receipt["remaining_amount"] == "0.00"


def test_partial_payment_is_credit_with_remaining_due(shop: World) -> None:
    sale = _sale(
        shop,
        lines=[{"article_id": shop.articles[0], "quantity": "2"}],
        payments=[_cash("1000")],
        customer_id=sh.credit_customer(shop),
    )
    receipt = shop.owner.get(f"/sales/{sale['id']}/receipt").json()
    assert receipt["total"] == "3000.00"
    assert (receipt["amount_received"], receipt["change_given"]) == ("1000.00", "0.00")
    assert (receipt["paid_amount"], receipt["remaining_amount"]) == ("1000.00", "2000.00")
    assert (receipt["payment_status"], receipt["is_credit"]) == ("PARTIALLY_PAID", True)
    assert receipt["customer_name"] == "Client comptoir"


def test_receipt_only_for_a_validated_sale(shop: World) -> None:
    draft = shop.owner.post(
        "/sales",
        json={"site_id": shop.site, "lines": [{"article_id": shop.articles[0], "quantity": "1"}]},
    ).json()
    for response in (
        shop.owner.get(f"/sales/{draft['id']}/receipt"),
        shop.owner.post(f"/sales/{draft['id']}/receipt/print"),
    ):
        assert response.status_code == 409
        assert response.json()["code"] == "receipt_unavailable"


def test_payment_detail_needs_payment_view(shop: World, client: TestClient) -> None:
    sale = _sale(shop, payments=[_cash("2000")])
    reader = _custom_member(
        shop, client, "lecteur@alpha.example.com", ["sales.sale.view", "sales.sale.view_all"]
    )
    receipt = reader.get(f"/sales/{sale['id']}/receipt").json()
    assert receipt["payments"] is None
    assert (receipt["amount_received"], receipt["change_given"]) == (None, None)
    # Le reste dû fait partie de la vente (déjà visible avec ``sales.sale.view``).
    assert receipt["remaining_amount"] == "0.00"


# --- Impression, réimpression, audit ------------------------------------------------------------


def test_first_print_then_reprints_are_audited(shop: World, owner_db: Session) -> None:
    sale = _sale(shop, payments=[_cash("5000")])
    first = shop.owner.post(f"/sales/{sale['id']}/receipt/print")
    assert first.status_code == 200, first.text
    assert (first.json()["print_count"], first.json()["change_given"]) == (1, "3500.00")
    second = shop.owner.post(f"/sales/{sale['id']}/receipt/print")
    assert second.json()["print_count"] == 2
    assert shop.owner.get(f"/sales/{sale['id']}/receipt").json()["print_count"] == 2
    assert _prints(owner_db, sale["id"]) == [
        {"number": sale["number"], "print_number": 1, "reprint": False},
        {"number": sale["number"], "print_number": 2, "reprint": True},
    ]
    # Consulter n'imprime pas : aucune entrée de plus.
    shop.owner.get(f"/sales/{sale['id']}/receipt")
    assert len(_prints(owner_db, sale["id"])) == 2


def test_print_and_reprint_permissions_are_distinct(shop: World, client: TestClient) -> None:
    view = ["sales.sale.view", "sales.sale.view_all"]
    printer = _custom_member(
        shop, client, "imprime@alpha.example.com", [*view, "sales.sale.receipt_print"]
    )
    reprinter = _custom_member(
        shop, client, "reimprime@alpha.example.com", [*view, "sales.sale.reprint"]
    )
    sale = _sale(shop, payments=[_cash("1500")])
    denied = reprinter.post(f"/sales/{sale['id']}/receipt/print")
    assert (denied.status_code, denied.json()["code"]) == (403, "receipt_print_denied")
    assert printer.post(f"/sales/{sale['id']}/receipt/print").status_code == 200
    denied = printer.post(f"/sales/{sale['id']}/receipt/print")
    assert (denied.status_code, denied.json()["code"]) == (403, "receipt_reprint_denied")
    assert reprinter.post(f"/sales/{sale['id']}/receipt/print").status_code == 200


@pytest.mark.parametrize("template", ["administrator", "manager", "seller"])
def test_base_roles_print_and_reprint(shop: World, client: TestClient, template: str) -> None:
    api = sh.member(shop, client, f"{template}@alpha.example.com", template, all_sites=True)
    # Ses propres ventes (portée du Vendeur), comme au point de vente.
    sale = _sale(shop, api, payments=[_cash("2000")])
    assert api.get(f"/sales/{sale['id']}/receipt").status_code == 200
    first = api.post(f"/sales/{sale['id']}/receipt/print")
    assert (first.status_code, first.json()["print_count"]) == (200, 1)
    again = api.post(f"/sales/{sale['id']}/receipt/print")
    assert (again.status_code, again.json()["print_count"]) == (200, 2)


def test_viewer_reads_but_never_prints(shop: World, client: TestClient) -> None:
    viewer = sh.member(shop, client, "consultant@alpha.example.com", "viewer", all_sites=True)
    sale = _sale(shop, payments=[_cash("1500")])
    assert viewer.get(f"/sales/{sale['id']}/receipt").status_code == 200
    denied = viewer.post(f"/sales/{sale['id']}/receipt/print")
    assert (denied.status_code, denied.json()["code"]) == (403, "receipt_print_denied")
    assert shop.owner.post(f"/sales/{sale['id']}/receipt/print").status_code == 200
    denied = viewer.post(f"/sales/{sale['id']}/receipt/print")
    assert (denied.status_code, denied.json()["code"]) == (403, "receipt_reprint_denied")


def test_receipt_respects_sales_scope_and_tenant(
    shop: World, client: TestClient, provision: Any, api_for: Any
) -> None:
    seller = sh.member(shop, client, "vendeur@alpha.example.com", "seller", all_sites=True)
    others = _sale(shop, payments=[_cash("1500")])  # vente du propriétaire
    for response in (
        seller.get(f"/sales/{others['id']}/receipt"),
        seller.post(f"/sales/{others['id']}/receipt/print"),
    ):
        assert (response.status_code, response.json()["code"]) == (404, "sale_not_found")
    provision("beta")
    stranger = api_for("owner@beta.example.com")
    assert stranger.get(f"/sales/{others['id']}/receipt").status_code == 404
    assert stranger.post(f"/sales/{others['id']}/receipt/print").status_code == 404


def test_receipt_permissions_are_declared_read_and_granted_by_templates(shop: World) -> None:
    natures = {p.code: p.access for p in MANIFEST.permissions}
    assert natures["sales.sale.receipt_print"] is AccessKind.READ
    assert natures["sales.sale.reprint"] is AccessKind.READ
    roles = {r["template_code"]: r for r in shop.owner.get("/roles").json()}
    wanted = {"sales.sale.receipt_print", "sales.sale.reprint"}
    for template in ("administrator", "manager", "seller"):
        assert wanted <= set(roles[template]["permission_codes"]), template
    assert not wanted & set(roles["viewer"]["permission_codes"])
