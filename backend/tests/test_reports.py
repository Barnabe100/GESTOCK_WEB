"""Palier R10 (ADR-0055) — rapports des ventes : chiffre d'affaires (remises déduites), coût
RÉEL des sorties (CMUP à la sortie), marge, panier moyen, séries par période, répartitions
(site, vendeur, canal, client, article, catégorie), paiements par moyen ; ventes VALIDÉES
seulement ; coûts masqués sans ``catalog.article.cost_view`` ; périmètre des sites et
permission ``reports.report.view`` ; exports audités ; isolation des tenants."""

import csv
import io
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.reports.manifest import MANIFEST
from app.platform.registry import AccessKind
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

VIEW, EXPORT = "reports.report.view", "reports.report.export"


@pytest.fixture
def shop(world: World) -> World:
    """Articles à 150 (coût d'entrée 100) ; stocks sur les deux sites."""
    sh.validated_entry(world, [(0, "100", "100"), (1, "100", "60")])
    sh.validated_entry(world, [(0, "50", "100")], site_id=world.site2)
    return world


def _sale(
    w: World,
    lines: list[tuple[int, str]],
    *,
    api: Api | None = None,
    site: str | None = None,
    validate: bool = True,
    payments: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    api = api or w.owner
    created = api.post(
        "/sales",
        json={
            "site_id": site or w.site,
            "lines": [{"article_id": w.articles[i], "quantity": q} for i, q in lines],
            **extra,
        },
    )
    assert created.status_code == 201, created.text
    sale = dict(created.json())
    if not validate:
        return sale
    body = {"payments": payments if payments is not None else []}
    validated = api.post(f"/sales/{sale['id']}/validate", json=body)
    assert validated.status_code == 200, validated.text
    return dict(validated.json())


def _cash(amount: str) -> list[dict[str, str]]:
    return [{"method": "CASH", "amount": amount}]


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
            "roles": [{"role_id": role.json()["id"], "site_id": access.pop("role_site", None)}],
            **(access or {"all_sites": True}),
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


@pytest.fixture
def activity(shop: World) -> dict[str, Any]:
    """Site principal : vente A (2 × art. 0 + 1 × art. 1 = 450, remise 50 → 400, payée en
    espèces), vente B (3 × art. 0 = 450, non payée, client), brouillon, vente annulée ;
    dépôt : vente C (1 × art. 0 = 150, payée)."""
    customer = sh.credit_customer(shop)
    a = _sale(
        shop,
        [(0, "2"), (1, "1")],
        discount={"mode": "AMOUNT", "value": "50", "reason": "Fidélité"},
        payments=_cash("400"),
    )
    b = _sale(shop, [(0, "3")], customer_id=customer)
    _sale(shop, [(0, "9")], validate=False)
    cancelled = _sale(shop, [(0, "1")], customer_id=customer)
    assert (
        shop.owner.post(f"/sales/{cancelled['id']}/cancel", json={"reason": "Erreur"}).status_code
        == 200
    )
    c = _sale(shop, [(0, "1")], site=shop.site2, payments=_cash("150"))
    return {"a": a, "b": b, "c": c, "customer": customer}


# --- Déclaration -----------------------------------------------------------------------------


def test_permissions_are_declared() -> None:
    kinds = {p.code: p.access for p in MANIFEST.permissions}
    assert kinds == {VIEW: AccessKind.READ, EXPORT: AccessKind.EXPORT}
    assert set(MANIFEST.depends_on) == {"catalog", "customers", "stock", "sales"}


def test_base_roles(world: World) -> None:
    roles = {r["template_code"]: r for r in world.owner.get("/roles").json()}
    assert {VIEW, EXPORT} <= set(roles["manager"]["permission_codes"])
    assert VIEW in roles["viewer"]["permission_codes"]
    assert EXPORT not in roles["viewer"]["permission_codes"]
    assert not {VIEW, EXPORT} & set(roles["seller"]["permission_codes"])


# --- Synthèse ---------------------------------------------------------------------------------


def test_summary_counts_validated_sales_with_real_costs(
    shop: World, activity: dict[str, Any]
) -> None:
    response = shop.owner.get("/reports/sales/summary")
    assert response.status_code == 200, response.text
    totals = response.json()["totals"]
    # A 400 + B 450 + C 150 ; brouillon et vente annulée exclus.
    assert totals["sales_count"] == 3
    assert (totals["revenue"], totals["subtotal"], totals["discounts"]) == (
        "1000.00",
        "1050.00",
        "50.00",
    )
    # Coût : A 2 × 100 + 1 × 60, B 3 × 100, C 1 × 100 = 660 (CMUP à la sortie).
    assert (totals["cost"], totals["margin"], totals["margin_rate"]) == (
        "660.00",
        "340.00",
        "34.00",
    )
    assert totals["average_basket"] == "333.33"
    assert (totals["paid"], totals["remaining"]) == ("550.00", "450.00")


def test_summary_by_site_filter_and_series(shop: World, activity: dict[str, Any]) -> None:
    depot = shop.owner.get("/reports/sales/summary", params={"site_id": shop.site2}).json()
    assert (depot["totals"]["sales_count"], depot["totals"]["revenue"]) == (1, "150.00")
    assert depot["site_ids"] == [shop.site2]
    summary = shop.owner.get("/reports/sales/summary").json()
    series = summary["series"]
    # Série complète : un point par jour du 1er du mois à aujourd'hui, jours vides compris.
    assert series[0]["period"].endswith("-01")
    assert sum(int(p["sales_count"]) for p in series) == 3
    assert sum(float(p["revenue"]) for p in series) == 1000
    month = shop.owner.get("/reports/sales/summary", params={"granularity": "month"}).json()
    assert len(month["series"]) == 1
    assert month["series"][0]["margin"] == "340.00"


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"date_from": "2026-05-02", "date_to": "2026-05-01"}, "report_period_invalid"),
        ({"date_from": "2020-01-01", "date_to": "2026-01-01"}, "report_period_too_long"),
        ({"granularity": "year"}, "validation_error"),
        ({"channel": "pos; drop"}, "validation_error"),
    ],
)
def test_invalid_parameters(shop: World, params: dict[str, str], code: str) -> None:
    response = shop.owner.get("/reports/sales/summary", params=params)
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_period_excludes_other_dates(shop: World, activity: dict[str, Any]) -> None:
    past = shop.owner.get(
        "/reports/sales/summary", params={"date_from": "2020-01-01", "date_to": "2020-12-31"}
    ).json()
    assert past["totals"]["sales_count"] == 0
    assert past["totals"]["margin_rate"] is None
    assert len(past["series"]) == 366


# --- Répartitions -----------------------------------------------------------------------------


def _breakdown(api: Api, dimension: str, **params: Any) -> Any:
    return api.get("/reports/sales/breakdown", params={"dimension": dimension, **params})


def test_breakdown_by_article_uses_line_amounts_and_direct_costs(
    shop: World, activity: dict[str, Any]
) -> None:
    body = _breakdown(shop.owner, "article").json()
    rows = {r["reference"]: r for r in body["items"]}
    # Article 0 : 2 + 3 + 1 = 6 unités, 900 de lignes, coût 600.
    assert (rows["A-0"]["quantity"], rows["A-0"]["revenue"], rows["A-0"]["cost"]) == (
        "6.000",
        "900.00",
        "600.00",
    )
    assert (rows["A-0"]["margin"], rows["A-0"]["sales_count"], rows["A-0"]["unit"]) == (
        "300.00",
        3,
        "u",
    )
    assert (rows["A-1"]["revenue"], rows["A-1"]["cost"]) == ("150.00", "60.00")
    # Montants des lignes AVANT remise globale (1 050) : part de chaque article.
    assert body["revenue_total"] == "1050.00"
    assert rows["A-0"]["share"] == "85.71"
    assert rows["A-0"]["discounts"] is None
    # Tri serveur par CA décroissant (défaut), puis par quantité croissante.
    assert [r["reference"] for r in body["items"]] == ["A-0", "A-1"]
    by_quantity = _breakdown(shop.owner, "article", sort="quantity").json()["items"]
    assert [r["reference"] for r in by_quantity] == ["A-1", "A-0"]


def test_breakdown_by_sale_dimensions(shop: World, activity: dict[str, Any]) -> None:
    sites = {r["label"]: r for r in _breakdown(shop.owner, "site").json()["items"]}
    assert sites["Dépôt"]["revenue"] == "150.00"
    main = next(r for name, r in sites.items() if name != "Dépôt")
    assert (main["revenue"], main["discounts"], main["cost"], main["sales_count"]) == (
        "850.00",
        "50.00",
        "560.00",
        2,
    )
    [seller] = _breakdown(shop.owner, "seller").json()["items"]
    assert (seller["label"], seller["revenue"]) == ("Owner alpha", "1000.00")
    [channel] = _breakdown(shop.owner, "channel").json()["items"]
    assert (channel["key"], channel["revenue"]) == ("BACKOFFICE", "1000.00")
    customers = {r["key"]: r for r in _breakdown(shop.owner, "customer").json()["items"]}
    assert customers[activity["customer"]]["revenue"] == "450.00"
    assert customers[None]["revenue"] == "550.00"  # sans client : « Ordinaire »
    [category] = _breakdown(shop.owner, "category").json()["items"]
    assert (category["label"], category["revenue"]) == ("Divers", "1050.00")


def test_breakdown_is_paginated(shop: World, activity: dict[str, Any]) -> None:
    page = _breakdown(shop.owner, "article", limit=1, offset=1).json()
    assert (page["total"], len(page["items"])) == (2, 1)
    assert page["items"][0]["reference"] == "A-1"
    invalid = _breakdown(shop.owner, "article", sort="designation; drop")
    assert invalid.json()["code"] == "invalid_sort"


def test_payments_by_method(shop: World, activity: dict[str, Any]) -> None:
    [cash] = shop.owner.get("/reports/payments").json()
    assert (cash["method"], cash["payment_count"], cash["amount"], cash["share"]) == (
        "CASH",
        2,
        "550.00",
        "100.00",
    )


# --- Coûts, permissions, périmètre ------------------------------------------------------------


def test_costs_are_absent_without_cost_view(
    shop: World, activity: dict[str, Any], client: TestClient
) -> None:
    api = _custom_member(shop, client, "rapports@example.com", [VIEW])
    totals = api.get("/reports/sales/summary").json()["totals"]
    assert totals["revenue"] == "1000.00"
    assert not {"cost", "margin", "margin_rate"} & set(totals)
    point = api.get("/reports/sales/summary").json()["series"][0]
    assert not {"cost", "margin"} & set(point)
    row = _breakdown(api, "article").json()["items"][0]
    assert not {"cost", "margin", "margin_rate"} & set(row)
    # Aucun tri révélant l'ordre des coûts.
    assert _breakdown(api, "article", sort="-margin").json()["code"] == "invalid_sort"


def test_permission_and_module_are_required(
    shop: World, activity: dict[str, Any], client: TestClient, owner_db: Session
) -> None:
    seller = sh.member(shop, client, "vendeur@example.com", "seller", all_sites=True)
    assert seller.get("/reports/sales/summary").status_code == 403
    manager = sh.member(shop, client, "gestion@example.com", "manager", all_sites=True)
    assert manager.get("/reports/sales/summary").json()["totals"]["cost"] == "660.00"
    viewer = sh.member(shop, client, "consultant@example.com", "viewer", all_sites=True)
    assert viewer.get("/reports/sales/summary").status_code == 200
    denied = viewer.get("/reports/export", params={"report": "summary", "format": "csv"})
    assert denied.status_code == 403
    # Module désactivé sur le dépôt : ses ventes sortent du périmètre.
    disabled = shop.owner.put(f"/sites/{shop.site2}/modules/reports", json={"enabled": False})
    assert disabled.status_code == 204, disabled.text
    assert shop.owner.get("/reports/sales/summary").json()["totals"]["revenue"] == "850.00"


def test_site_scope_of_a_member(shop: World, activity: dict[str, Any], client: TestClient) -> None:
    depot = _custom_member(
        shop,
        client,
        "depot@example.com",
        [VIEW, "catalog.article.cost_view"],
        role_site=shop.site2,
        all_sites=False,
        site_ids=[shop.site2],
    )
    # Un rôle limité à un site ne vaut que sur ce site, sélectionné (``X-Site-Id``).
    assert depot.get("/reports/sales/summary").status_code == 403
    depot = Api(depot.client, depot.token, uuid.UUID(shop.site2))
    response = depot.get("/reports/sales/summary")
    assert response.status_code == 200, response.text
    summary = response.json()
    assert (summary["site_ids"], summary["totals"]["revenue"]) == ([shop.site2], "150.00")
    # Filtre sur un site non visible : périmètre vide (jamais les données d'un autre site).
    other = depot.get("/reports/sales/summary", params={"site_id": shop.site}).json()
    assert (other["site_ids"], other["totals"]["sales_count"]) == ([], 0)


def test_tenant_isolation(
    shop: World, activity: dict[str, Any], provision: Any, api_for: Any
) -> None:
    provision("beta")
    beta: Api = api_for("owner@beta.example.com")
    assert beta.get("/reports/sales/summary").json()["totals"]["sales_count"] == 0
    assert (
        beta.get("/reports/sales/breakdown", params={"dimension": "article"}).json()["items"] == []
    )
    assert beta.get("/reports/payments").json() == []
    assert (
        beta.get("/reports/sales/summary", params={"site_id": shop.site}).json()["site_ids"] == []
    )


# --- Exports ---------------------------------------------------------------------------------


def _csv(response: Any) -> list[list[str]]:
    assert response.status_code == 200, response.text
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))


def test_exports_formats_and_audit(
    shop: World, activity: dict[str, Any], owner_db: Session
) -> None:
    summary = _csv(shop.owner.get("/reports/export", params={"report": "summary", "format": "csv"}))
    assert summary[0] == ["Période", "Ventes", "Chiffre d'affaires", "Remises", "Coût", "Marge"]
    assert summary[-1] == ["Total", "3", "1000,00", "50,00", "660,00", "340,00"]
    articles = _csv(
        shop.owner.get(
            "/reports/export",
            params={"report": "breakdown", "dimension": "article", "format": "csv"},
        )
    )
    assert articles[0][:5] == ["Article", "Référence", "Ventes", "Quantité", "Unité"]
    assert articles[1][:2] == ["Article 0", "A-0"]
    payments = _csv(
        shop.owner.get("/reports/export", params={"report": "payments", "format": "csv"})
    )
    assert payments[1][1:3] == ["2", "550,00"]
    for fmt, mime in (("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
        response = shop.owner.get("/reports/export", params={"report": "summary", "format": fmt})
        assert response.status_code == 200 and mime in response.headers["content-type"]
    logs = owner_db.execute(
        text(
            "SELECT data FROM audit_logs WHERE action = 'export.generated' "
            "AND data->>'feature' LIKE 'reports.%' ORDER BY occurred_at"
        )
    ).all()
    features = [row[0]["feature"] for row in logs]
    assert features.count("reports.summary") == 3
    assert "reports.breakdown" in features and "reports.payments" in features
    breakdown = next(row[0] for row in logs if row[0]["feature"] == "reports.breakdown")
    assert breakdown["filters"]["dimension"] == "article"


def test_export_without_cost_view_has_no_cost_columns(
    shop: World, activity: dict[str, Any], client: TestClient
) -> None:
    api = _custom_member(shop, client, "export@example.com", [VIEW, EXPORT])
    rows = _csv(api.get("/reports/export", params={"report": "summary", "format": "csv"}))
    assert rows[0] == ["Période", "Ventes", "Chiffre d'affaires", "Remises"]
    articles = _csv(
        api.get(
            "/reports/export",
            params={"report": "breakdown", "dimension": "article", "format": "csv"},
        )
    )
    assert not {"Coût", "Marge", "Taux de marge (%)"} & set(articles[0])


def test_unknown_export_parameters_are_refused(shop: World) -> None:
    response = shop.owner.get("/reports/export", params={"report": "secret", "format": "csv"})
    assert response.status_code == 422
    response = shop.owner.get("/reports/export", params={"report": "summary", "format": "docx"})
    assert response.status_code == 422


def test_no_report_route_writes_anything(shop: World) -> None:
    from app.modules.reports.router import router

    assert {m for route in router.routes for m in getattr(route, "methods", set())} == {"GET"}
