"""Palier R9 (ADR-0054) — remise globale sur une vente : montant ou pourcentage calculé par le
SERVEUR, motif obligatoire, permission ``sales.sale.discount`` (nature ``write``, Administrateur
seulement par ``*``) sur le site de la vente ; ``total = sous-total − remise`` garanti par la base
et seule base des paiements, du crédit et du reçu ; retrait sur un brouillon ; audit ; point de
vente ; règlement d'une origine sans remise."""

import csv
import io
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.sales.manifest import MANIFEST
from app.platform.registry import AccessKind
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

DISCOUNT = "sales.sale.discount"


@pytest.fixture
def shop(world: World) -> World:
    """Articles à 150 ; stock 100 de l'article 0 et de l'article 1."""
    sh.validated_entry(world, [(0, "100", "100"), (1, "100", "100")])
    return world


def _body(w: World, quantity: str = "10", **extra: Any) -> dict[str, Any]:
    return {
        "site_id": w.site,
        "lines": [{"article_id": w.articles[0], "quantity": quantity}],
        **extra,
    }


def _discount(value: str, mode: str = "AMOUNT", reason: str = "Client fidèle") -> dict[str, Any]:
    return {"mode": mode, "value": value, "reason": reason}


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


def _audits(owner_db: Session, sale_id: str) -> list[tuple[str, dict[str, Any]]]:
    rows = owner_db.execute(
        text("SELECT action, data FROM audit_logs WHERE entity_id = :id ORDER BY occurred_at, id"),
        {"id": sale_id},
    ).all()
    return [(r[0], dict(r[1])) for r in rows]


# --- Déclaration -----------------------------------------------------------------------------


def test_discount_permission_is_declared_as_write() -> None:
    [permission] = [p for p in MANIFEST.permissions if p.code == DISCOUNT]
    assert permission.access is AccessKind.WRITE


def test_only_the_administrator_template_grants_the_discount(world: World, client: Any) -> None:
    """Rôles de base : ``*`` (Administrateur) seulement ; Gestionnaire et Vendeur ne l'ont pas."""
    roles = {r["template_code"]: r for r in world.owner.get("/roles").json()}
    assert DISCOUNT in roles["administrator"]["permission_codes"]
    for template in ("manager", "seller", "viewer"):
        assert DISCOUNT not in roles[template]["permission_codes"]


# --- Calcul par le serveur ---------------------------------------------------------------------


def test_amount_discount_sets_total_reason_and_author(shop: World, owner_db: Session) -> None:
    created = shop.owner.post("/sales", json=_body(shop, discount=_discount("200")))
    assert created.status_code == 201, created.text
    sale = created.json()
    assert (sale["subtotal"], sale["discount_amount"], sale["total"]) == (
        "1500.00",
        "200.00",
        "1300.00",
    )
    assert sale["discount_reason"] == "Client fidèle"
    author = owner_db.execute(
        text("SELECT u.email FROM sales s JOIN users u ON u.id = s.discount_by WHERE s.id = :id"),
        {"id": sale["id"]},
    ).scalar_one()
    assert author == "owner@alpha.example.com"


def test_percent_discount_is_rounded_by_the_server(shop: World) -> None:
    # 1 × 150 = 150 ; 33,33 % = 49,995 → 50,00 (arrondi au centime, demi supérieur).
    sale = shop.owner.post(
        "/sales", json=_body(shop, "1", discount=_discount("33.33", "PERCENT"))
    ).json()
    assert (sale["discount_amount"], sale["total"]) == ("50.00", "100.00")
    # 100 % : vente offerte, total nul.
    full = shop.owner.post(
        "/sales", json=_body(shop, "1", discount=_discount("100", "PERCENT"))
    ).json()
    assert (full["discount_amount"], full["total"]) == ("150.00", "0.00")


@pytest.mark.parametrize(
    ("discount", "status", "code"),
    [
        (_discount("1500.01"), 422, "sale_discount_too_large"),
        (_discount("100.01", "PERCENT"), 422, "sale_discount_too_large"),
        (_discount("0"), 422, "validation_error"),
        (_discount("-5"), 422, "validation_error"),
        (_discount("10.001"), 422, "validation_error"),
        (_discount("10", reason="  "), 422, "validation_error"),
        (_discount("10", reason="x" * 201), 422, "validation_error"),
        ({"mode": "AMOUNT", "value": "10"}, 422, "validation_error"),
        ({**_discount("10"), "mode": "FIXED"}, 422, "validation_error"),
        # Aucun champ calculé n'est accepté du client.
        ({**_discount("10"), "discount_by": str(uuid.uuid4())}, 422, "validation_error"),
    ],
)
def test_invalid_discounts_are_refused(
    shop: World, discount: dict[str, Any], status: int, code: str
) -> None:
    response = shop.owner.post("/sales", json=_body(shop, discount=discount))
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def test_client_supplied_totals_are_ignored(shop: World) -> None:
    body = _body(shop, discount=_discount("200"))
    body["total"] = "1"
    response = shop.owner.post("/sales", json=body)
    # Champ inconnu : refusé ou ignoré, jamais pris comme total.
    if response.status_code == 201:
        assert response.json()["total"] == "1300.00"
    else:
        assert response.json()["code"] == "validation_error"


def test_database_guarantees_total_and_reason(shop: World, owner_db: Session) -> None:
    sale = shop.owner.post("/sales", json=_body(shop)).json()
    for statement in (
        "UPDATE sales SET discount_amount = 10 WHERE id = :id",  # total ≠ sous-total − remise
        "UPDATE sales SET discount_amount = 10, total = total - 10 WHERE id = :id",  # sans motif
        "UPDATE sales SET discount_amount = subtotal + 1, total = -1 WHERE id = :id",
    ):
        with pytest.raises(IntegrityError):
            owner_db.execute(text(statement), {"id": sale["id"]})
        owner_db.rollback()


# --- Permission et portée ----------------------------------------------------------------------


def test_discount_requires_the_permission(shop: World, client: Any) -> None:
    seller = sh.member(shop, client, "vendeur@example.com", "seller", all_sites=True)
    manager = sh.member(shop, client, "gestion@example.com", "manager", all_sites=True)
    for api in (seller, manager):
        denied = api.post("/sales", json=_body(shop, discount=_discount("10")))
        assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
        # Sans remise : vente permise comme avant.
        assert api.post("/sales", json=_body(shop)).status_code == 201
    granted = _custom_member(
        shop,
        client,
        "remise@example.com",
        ["sales.sale.view", "sales.sale.create", "sales.sale.update", DISCOUNT],
    )
    response = granted.post("/sales", json=_body(shop, discount=_discount("10")))
    assert response.status_code == 201, response.text
    assert response.json()["total"] == "1490.00"


def test_discount_permission_is_checked_for_the_site_of_the_sale(shop: World, client: Any) -> None:
    role = shop.owner.post(
        "/roles",
        json={
            "name": "Remise dépôt",
            "permissions": ["sales.sale.view", "sales.sale.create", DISCOUNT],
        },
    ).json()
    seller_role = {r["template_code"]: r["id"] for r in shop.owner.get("/roles").json()}["seller"]
    created = shop.owner.post(
        "/members",
        json={
            "email": "depot@example.com",
            "full_name": "Dépôt",
            "password": "Provisoire-123",
            "roles": [
                {"role_id": seller_role},
                {"role_id": role["id"], "site_id": shop.site2},
            ],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, "depot@example.com", "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    api = Api(client, login(client, "depot@example.com").json()["access_token"])
    # Permission accordée sur le dépôt seulement.
    main = api.post("/sales", json=_body(shop, discount=_discount("10")))
    assert main.status_code == 403, main.text
    sh.validated_entry(shop, [(0, "10", "100")], site_id=shop.site2)
    depot = api.post(
        "/sales", json={**_body(shop, "1", discount=_discount("10")), "site_id": shop.site2}
    )
    assert depot.status_code == 201, depot.text


# --- Brouillon : modification et retrait --------------------------------------------------------


def test_update_replaces_or_removes_the_discount(shop: World, owner_db: Session) -> None:
    sale = shop.owner.post("/sales", json=_body(shop, discount=_discount("200"))).json()
    changed = shop.owner.put(
        f"/sales/{sale['id']}", json=_body(shop, "2", discount=_discount("10", "PERCENT"))
    ).json()
    assert (changed["subtotal"], changed["discount_amount"], changed["total"]) == (
        "300.00",
        "30.00",
        "270.00",
    )
    removed = shop.owner.put(f"/sales/{sale['id']}", json=_body(shop, "2")).json()
    assert (removed["discount_amount"], removed["discount_reason"], removed["total"]) == (
        "0.00",
        None,
        "300.00",
    )
    assert (
        owner_db.execute(
            text("SELECT discount_by FROM sales WHERE id = :id"), {"id": sale["id"]}
        ).scalar_one()
        is None
    )
    # Une remise dépassant le nouveau sous-total est refusée, le brouillon reste intact.
    too_large = shop.owner.put(
        f"/sales/{sale['id']}", json=_body(shop, "1", discount=_discount("151"))
    )
    assert too_large.json()["code"] == "sale_discount_too_large"
    assert shop.owner.get(f"/sales/{sale['id']}").json()["total"] == "300.00"


def test_validated_sale_discount_is_frozen(shop: World) -> None:
    sale = shop.owner.post("/sales", json=_body(shop, discount=_discount("200"))).json()
    validated = shop.owner.post(
        f"/sales/{sale['id']}/validate",
        json={"payments": [{"method": "CASH", "amount": "1300"}]},
    )
    assert validated.status_code == 200, validated.text
    update = shop.owner.put(f"/sales/{sale['id']}", json=_body(shop))
    assert update.json()["code"] == "sale_not_draft"


# --- Paiements, crédit, reçu ---------------------------------------------------------------------


def test_payments_are_based_on_the_discounted_total(shop: World) -> None:
    sale = shop.owner.post("/sales", json=_body(shop, discount=_discount("200"))).json()
    # Paiement du sous-total : surpaiement refusé (le total dû est 1 300).
    over = shop.owner.post(
        f"/sales/{sale['id']}/validate",
        json={"payments": [{"method": "CASH", "amount": "1500"}]},
    )
    assert over.status_code == 422, over.text
    # Espèces : montant reçu 2 000 → monnaie rendue sur le total remisé (700).
    done = shop.owner.post(
        f"/sales/{sale['id']}/validate",
        json={"payments": [{"method": "CASH", "amount_received": "2000"}]},
    )
    assert done.status_code == 200, done.text
    receipt = shop.owner.get(f"/sales/{sale['id']}/receipt").json()
    assert (receipt["subtotal"], receipt["discount_amount"], receipt["total"]) == (
        "1500.00",
        "200.00",
        "1300.00",
    )
    assert (receipt["change_given"], receipt["remaining_amount"]) == ("700.00", "0.00")
    assert receipt["payment_status"] == "PAID"


def test_credit_exposure_uses_the_discounted_total(shop: World, owner_db: Session) -> None:
    customer = shop.owner.post(
        "/customers",
        json={"customer_type": "INDIVIDUAL", "name": "Awa", "credit_limit": "1300"},
    ).json()
    # 1 500 − 200 = 1 300 : exactement la limite → permis.
    sale = shop.owner.post(
        "/sales", json=_body(shop, customer_id=customer["id"], discount=_discount("200"))
    ).json()
    response = shop.owner.post(f"/sales/{sale['id']}/validate", json={"payments": []})
    assert response.status_code == 200, response.text
    receivable = shop.owner.get(f"/sales/{sale['id']}").json()
    assert receivable["remaining_amount"] == "1300.00"


def test_stock_is_moved_for_the_full_quantity(shop: World, owner_db: Session) -> None:
    """La remise porte sur le prix, jamais sur la quantité : stock et CMUP inchangés par elle."""
    before = sh.level(owner_db, shop, 0)
    sale = shop.owner.post("/sales", json=_body(shop, discount=_discount("100", "PERCENT"))).json()
    assert shop.owner.post(f"/sales/{sale['id']}/validate", json={}).status_code == 200
    after = sh.level(owner_db, shop, 0)
    assert Decimal(before[0]) - Decimal(after[0]) == Decimal(10)
    assert before[1] == after[1]


# --- Audit -----------------------------------------------------------------------------------


def test_discount_is_audited_on_create_update_and_validation(
    shop: World, owner_db: Session
) -> None:
    sale = shop.owner.post("/sales", json=_body(shop, discount=_discount("200"))).json()
    shop.owner.put(f"/sales/{sale['id']}", json=_body(shop, discount=_discount("300")))
    shop.owner.post(
        f"/sales/{sale['id']}/validate",
        json={"payments": [{"method": "CASH", "amount": "1200"}]},
    )
    audits = dict(_audits(owner_db, sale["id"]))
    assert audits["sale.created"]["discount_amount"] == "200.00"
    assert audits["sale.created"]["discount_reason"] == "Client fidèle"
    assert audits["sale.updated"]["before"]["discount_amount"] == "200.00"
    assert audits["sale.updated"]["after"]["discount_amount"] == "300.00"
    assert audits["sale.validated"]["total"] == "1200.00"


# --- Point de vente et origines ------------------------------------------------------------------


def test_pos_checkout_with_discount(shop: World, client: Any) -> None:
    body = {
        **_body(shop, "2", discount=_discount("50")),
        "payments": [{"method": "CASH", "amount_received": "300"}],
        "idempotency_key": str(uuid.uuid4()),
    }
    response = shop.owner.post("/pos/checkout", json=body)
    assert response.status_code == 201, response.text
    sale = response.json()["sale"]
    assert (sale["discount_amount"], sale["total"]) == ("50.00", "250.00")
    seller = sh.member(shop, client, "caisse@example.com", "seller", all_sites=True)
    denied = seller.post("/pos/checkout", json={**body, "idempotency_key": str(uuid.uuid4())})
    assert denied.status_code == 403, denied.text


def test_export_shows_the_discount_next_to_the_total(shop: World) -> None:
    sale = shop.owner.post("/sales", json=_body(shop, discount=_discount("200"))).json()
    response = shop.owner.get("/sales/export", params={"format": "csv"})
    assert response.status_code == 200, response.text
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
    header = rows[0]
    total, discount = header.index("Total"), header.index("Dont remise")
    assert discount == total + 1
    [row] = [r for r in rows[1:] if r[0] == "Non numérotée"]
    assert (row[total], row[discount]) == ("1300,00", "200,00")
    assert sale["total"] == "1300.00"
