"""Lot 3-E (ADR-0043) — fiche fournisseur : réceptions, synthèse, articles, chronologie.

Lot de CONSULTATION : agrégats calculés sur les seules réceptions (``PURCHASE``) VALIDÉES des
sites visibles (portée de ``stock.entry.view``) ; brouillons et réceptions annulées affichés
dans l'historique mais exclus des agrégats et du « dernier coût » ; coûts absents sans
``catalog.article.cost_view`` ; chronologie = journal d'audit réel (``audit.log.view``) ; aucune
écriture (prix d'achat de référence inchangé)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

COST_VIEW = "catalog.article.cost_view"
COST_KEYS = {"received_total", "last_unit_cost", "amount", "total_amount", "unit_cost"}


def _day(offset: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=offset)).isoformat()


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _entry(w: World, lines: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    body = {"site_id": w.site, "supplier_id": w.supplier, "lines": lines, **extra}
    return dict(_ok(w.owner.post("/stock/entries", json=body), 201))


def _validated(w: World, lines: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    document = _entry(w, lines, **extra)
    return dict(_ok(w.owner.post(f"/stock/entries/{document['id']}/validate")))


def _line(w: World, index: int, quantity: str, cost: str, packaging: str | None = None) -> Any:
    line: dict[str, Any] = {"article_id": w.articles[index], "quantity": quantity}
    line["unit_cost"] = cost
    if packaging:
        line["packaging_id"] = packaging
    return line


def _summary(w: World, api: Api | None = None, supplier: str | None = None, **params: Any) -> Any:
    return (api or w.owner).get(f"/stock/suppliers/{supplier or w.supplier}/summary", params=params)


def _articles(w: World, api: Api | None = None, supplier: str | None = None, **params: Any) -> Any:
    return (api or w.owner).get(
        f"/stock/suppliers/{supplier or w.supplier}/articles", params=params
    )


def _member(w: World, client: TestClient, email: str, permissions: list[str], **access: Any) -> Api:
    role = _ok(
        w.owner.post("/roles", json={"name": f"Rôle {email}", "permissions": permissions}), 201
    )
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{"role_id": role["id"]}],
        **(access or {"all_sites": True}),
    }
    _ok(w.owner.post("/members", json=body), 201)
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


@pytest.fixture
def received(world: World) -> dict[str, Any]:
    """Historique de réceptions du fournisseur du monde de test (site principal) :

    - R1 (J-10, validée) : article 0 × 10 à 100, article 1 × 5 à 200 (total 2 000) ;
    - R2 (J-5, validée) : article 0 × 4 à 150 (total 600) — dernière réception de l'article 0 ;
    - R3 (J-2, brouillon) : article 0 × 1 à 999 ;
    - R4 (J-1, validée PUIS annulée) : article 0 × 1 à 888, article 2 × 3 à 777.
    """
    r1 = _validated(
        world, [_line(world, 0, "10", "100"), _line(world, 1, "5", "200")], operation_date=_day(10)
    )
    r2 = _validated(world, [_line(world, 0, "4", "150")], operation_date=_day(5))
    r3 = _entry(world, [_line(world, 0, "1", "999")], operation_date=_day(2))
    r4 = _validated(
        world, [_line(world, 0, "1", "888"), _line(world, 2, "3", "777")], operation_date=_day(1)
    )
    _ok(world.owner.post(f"/stock/entries/{r4['id']}/cancel", json={"reason": "Erreur de saisie"}))
    return {"r1": r1, "r2": r2, "r3": r3, "r4": r4}


# --- F3 : synthèse --------------------------------------------------------------------------------


def test_summary_counts_only_validated_receptions(world: World, received: dict[str, Any]) -> None:
    summary = _ok(_summary(world))
    assert summary == {
        "supplier_id": world.supplier,
        "validated_count": 2,  # brouillon R3 et R4 annulée exclus
        "last_received_on": _day(5),
        "received_total": "2600.00",
    }
    # Un autre fournisseur et le stock initial (sans fournisseur) n'y entrent pas.
    other = _ok(world.owner.post("/suppliers", json={"name": "Autre"}), 201)["id"]
    _validated(world, [_line(world, 0, "1", "50")], supplier_id=other)
    _validated(world, [_line(world, 1, "1", "50")], kind="INITIAL_STOCK", supplier_id=None)
    assert _ok(_summary(world))["validated_count"] == 2
    assert _ok(_summary(world, supplier=other))["received_total"] == "50.00"


def test_summary_of_supplier_without_receptions(world: World) -> None:
    assert _ok(_summary(world)) == {
        "supplier_id": world.supplier,
        "validated_count": 0,
        "last_received_on": None,
        "received_total": "0.00",
    }
    assert _ok(_articles(world)) == {"items": [], "total": 0, "limit": 25, "offset": 0}


def test_unknown_supplier(world: World) -> None:
    unknown = "01a0f000-0000-7000-8000-000000000000"
    for response in (
        _summary(world, supplier=unknown),
        _articles(world, supplier=unknown),
        world.owner.get(f"/suppliers/{unknown}/history"),
    ):
        assert (response.status_code, response.json()["code"]) == (404, "supplier_not_found")


# --- F4 : articles reçus et fournisseur principal -------------------------------------------------


def test_received_articles_last_cost_from_last_validated(
    world: World, received: dict[str, Any]
) -> None:
    page = _ok(_articles(world))
    assert page["total"] == 2  # article 2 : seulement dans la réception annulée
    rows = {row["article_id"]: row for row in page["items"]}
    first = rows[world.articles[0]]
    assert first == {
        "article_id": world.articles[0],
        "article_reference": "A-0",
        "article_designation": "Article 0",
        "unit": "u",
        "article_active": True,
        "receipt_count": 2,
        "received_base_quantity": "14.000",
        "last_received_on": _day(5),
        "last_entry_id": received["r2"]["id"],
        "last_entry_number": received["r2"]["number"],
        # Dernière réception VALIDÉE (R2), jamais le brouillon R3 ni l'annulée R4.
        "last_unit_cost": "150.0000",
    }
    assert rows[world.articles[1]]["last_unit_cost"] == "200.0000"
    # Tri par défaut : dernière réception d'abord ; recherche et tri en liste blanche.
    assert [r["article_id"] for r in page["items"]] == world.articles[:2]
    found = _ok(_articles(world, search="article 1"))
    assert [r["article_id"] for r in found["items"]] == [world.articles[1]]
    by_name = _ok(_articles(world, sort="designation"))
    assert [r["article_reference"] for r in by_name["items"]] == ["A-0", "A-1"]
    assert _articles(world, sort="last_unit_cost").status_code == 400  # coût : jamais triable


def test_last_cost_by_operation_date_and_packagings(world: World) -> None:
    carton = _ok(
        world.owner.post(
            f"/catalog/articles/{world.articles[0]}/packagings",
            json={"name": "Carton 24", "conversion": "24"},
        ),
        201,
    )["id"]
    # Réception la plus récente (J-3) : 1 carton à 2 400 (100 / u) + 6 u à 120.
    recent = _validated(
        world,
        [_line(world, 0, "1", "2400", carton), _line(world, 0, "6", "120")],
        operation_date=_day(3),
    )
    # Validée APRÈS, mais datée plus tôt (J-8) : n'est pas la dernière réception.
    _validated(world, [_line(world, 0, "2", "500")], operation_date=_day(8))
    row = _ok(_articles(world))["items"][0]
    assert row["last_entry_id"] == recent["id"]
    assert row["receipt_count"] == 2
    assert row["received_base_quantity"] == "32.000"  # 24 + 6 + 2, en unité de base
    # Coût par unité de base de la réception, pondéré : (24 × 100 + 6 × 120) / 30 = 104.
    assert row["last_unit_cost"] == "104.0000"


def test_main_supplier_tab_and_reference_price_unchanged(
    world: World, received: dict[str, Any], owner_db: Session
) -> None:
    # Fournisseur principal : article 2 seulement (jamais reçu de lui, réception annulée).
    _ok(
        world.owner.patch(
            f"/catalog/articles/{world.articles[2]}", json={"main_supplier_id": world.supplier}
        )
    )
    _ok(
        world.owner.patch(
            f"/catalog/articles/{world.articles[0]}", json={"main_supplier_id": world.supplier}
        )
    )
    main = _ok(world.owner.get("/catalog/articles", params={"supplier_id": world.supplier}))
    assert {a["id"] for a in main["items"]} == {world.articles[0], world.articles[2]}
    received_ids = {r["article_id"] for r in _ok(_articles(world))["items"]}
    assert world.articles[0] in received_ids  # présent dans les deux onglets
    assert world.articles[2] not in received_ids
    # D2 : les réceptions ne touchent jamais le prix d'achat de référence du catalogue.
    owner_db.expire_all()
    prices = owner_db.execute(
        text("SELECT purchase_price FROM catalog_articles WHERE id = ANY(:ids)"),
        {"ids": world.articles},
    ).scalars()
    assert set(prices) == {Decimal("100.00")}


# --- F2 / F6 : réceptions du fournisseur, recherche par nom ---------------------------------------


def test_receptions_list_shows_cancelled_and_drafts(world: World, received: dict[str, Any]) -> None:
    page = _ok(
        world.owner.get(
            "/stock/entries", params={"supplier_id": world.supplier, "kind": "PURCHASE"}
        )
    )
    statuses = {e["number"]: e["status"] for e in page["items"]}
    assert statuses == {
        received["r1"]["number"]: "VALIDATED",
        received["r2"]["number"]: "VALIDATED",
        received["r3"]["number"]: "DRAFT",
        received["r4"]["number"]: "CANCELLED",
    }


def test_entries_search_by_supplier_name(world: World) -> None:
    sahel = _ok(world.owner.post("/suppliers", json={"name": "Sahel Distribution"}), 201)["id"]
    faso = _entry(world, [_line(world, 0, "1", "10")], document_reference="BL-77")
    other = _entry(world, [_line(world, 0, "1", "10")], supplier_id=sahel)

    def numbers(search: str) -> set[str]:
        page = _ok(world.owner.get("/stock/entries", params={"search": search}))
        return {e["number"] for e in page["items"]}

    assert numbers("SAHEL") == {other["number"]}  # insensible à la casse
    assert numbers("faso imp") == {faso["number"]}
    assert numbers("BL-77") == {faso["number"]}  # référence de pièce inchangée
    assert numbers(faso["number"]) == {faso["number"]}  # numéro inchangé
    assert numbers("introuvable") == set()
    # Les sorties ne sont pas concernées (aucun fournisseur).
    assert _ok(world.owner.get("/stock/exits", params={"search": "sahel"}))["total"] == 0


# --- D4 : coûts et permissions --------------------------------------------------------------------


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_costs_absent_without_cost_view(
    world: World, received: dict[str, Any], client: TestClient
) -> None:
    viewer = _member(
        world,
        client,
        "sans-couts@example.com",
        ["suppliers.supplier.view", "stock.entry.view", "catalog.article.view"],
    )
    responses = {
        "summary": _summary(world, api=viewer),
        "articles": _articles(world, api=viewer),
        "entries": viewer.get("/stock/entries", params={"supplier_id": world.supplier}),
        "entry": viewer.get(f"/stock/entries/{received['r1']['id']}"),
    }
    for name, response in responses.items():
        assert response.status_code == 200, (name, response.text)
        assert not _keys(response.json()) & COST_KEYS, name
    assert responses["summary"].json()["validated_count"] == 2
    assert responses["articles"].json()["items"][0]["received_base_quantity"] == "14.000"
    costs = _member(
        world, client, "couts@example.com", ["stock.entry.view", COST_VIEW], all_sites=True
    )
    assert _ok(_summary(world, api=costs))["received_total"] == "2600.00"
    assert _ok(_articles(world, api=costs))["items"][0]["last_unit_cost"] == "150.0000"


def test_permissions(world: World, received: dict[str, Any], client: TestClient) -> None:
    # Fiche fournisseur sans stock.entry.view : ni synthèse ni articles reçus.
    supplier_only = _member(world, client, "fournisseurs@example.com", ["suppliers.supplier.view"])
    assert _ok(supplier_only.get(f"/suppliers/{world.supplier}"))["name"] == "Faso Import"
    assert _summary(world, api=supplier_only).status_code == 403
    assert _articles(world, api=supplier_only).status_code == 403
    # Chronologie : audit.log.view ET suppliers.supplier.view.
    assert supplier_only.get(f"/suppliers/{world.supplier}/history").status_code == 403
    audit_only = _member(world, client, "audit@example.com", ["audit.log.view"])
    assert audit_only.get(f"/suppliers/{world.supplier}/history").status_code == 403
    both = _member(
        world, client, "les-deux@example.com", ["audit.log.view", "suppliers.supplier.view"]
    )
    assert both.get(f"/suppliers/{world.supplier}/history").status_code == 200
    # Vendeur (rôle de base) : aucun accès fournisseurs ni réceptions.
    seller = sh.member(world, client, "vendeur@example.com", "seller")
    assert _summary(world, api=seller).status_code == 403
    assert seller.get(f"/suppliers/{world.supplier}/history").status_code == 403


# --- F5 : chronologie -----------------------------------------------------------------------------


def test_history_only_real_events_in_order(world: World, received: dict[str, Any]) -> None:
    supplier = world.supplier
    _ok(world.owner.patch(f"/suppliers/{supplier}", json={"phone": "70 11 22 33"}))
    _ok(world.owner.patch(f"/suppliers/{supplier}", json={"phone": "70 11 22 33"}))  # sans effet
    _ok(world.owner.post(f"/suppliers/{supplier}/deactivate"))
    _ok(world.owner.post(f"/suppliers/{supplier}/activate"))
    other = _ok(world.owner.post("/suppliers", json={"name": "Autre"}), 201)["id"]
    _ok(world.owner.patch(f"/suppliers/{other}", json={"city": "Bobo"}))
    events = _ok(world.owner.get(f"/suppliers/{supplier}/history"))
    assert [e["action"] for e in events] == [
        "supplier.created",
        "supplier.updated",
        "supplier.deactivated",
        "supplier.activated",
    ]
    assert events[1]["data"]["phone"] == {"before": None, "after": "70 11 22 33"}
    assert all(e["user_name"] for e in events)
    # Aucun évènement créé par la consultation ; les réceptions ont leur propre journal.
    assert len(_ok(world.owner.get(f"/suppliers/{supplier}/history"))) == 4


# --- D4 : portée des sites ------------------------------------------------------------------------


def test_site_scope(world: World, received: dict[str, Any], client: TestClient) -> None:
    on_depot = _validated(world, [_line(world, 1, "7", "300")], site_id=world.site2)
    assert _ok(_summary(world))["validated_count"] == 3
    assert _ok(_summary(world, site_id=world.site2))["received_total"] == "2100.00"
    # Membre limité au site principal : les réceptions du dépôt n'existent pas pour lui.
    member = _member(
        world,
        client,
        "site-principal@example.com",
        ["stock.entry.view", COST_VIEW, "suppliers.supplier.view"],
        site_ids=[world.site],
    )
    summary = _ok(_summary(world, api=member))
    assert (summary["validated_count"], summary["received_total"]) == (2, "2600.00")
    article_1 = next(
        r
        for r in _ok(_articles(world, api=member))["items"]
        if r["article_id"] == world.articles[1]
    )
    assert article_1["received_base_quantity"] == "5.000"
    assert article_1["last_entry_id"] == received["r1"]["id"]  # pas la réception du dépôt
    # Filtre sur un site non visible : rien, jamais d'erreur révélant son contenu.
    assert _ok(_summary(world, api=member, site_id=world.site2))["validated_count"] == 0
    assert _ok(_articles(world, api=member, site_id=world.site2))["total"] == 0
    hidden = member.get("/stock/entries", params={"supplier_id": world.supplier})
    assert on_depot["number"] not in {e["number"] for e in _ok(hidden)["items"]}


# --- Isolation entre entreprises (API et RLS) -----------------------------------------------------


def test_tenant_isolation(
    world: World,
    received: dict[str, Any],
    provision: Any,
    api_for: Any,
    db: Session,
    owner_db: Session,
) -> None:
    beta = provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    other = api_for("owner@beta.example.com")
    for response in (
        other.get(f"/stock/suppliers/{world.supplier}/summary"),
        other.get(f"/stock/suppliers/{world.supplier}/articles"),
        other.get(f"/suppliers/{world.supplier}/history"),
    ):
        assert (response.status_code, response.json()["code"]) == (404, "supplier_not_found")
    assert _ok(other.get("/stock/entries", params={"search": "Faso"}))["total"] == 0
    # Un fournisseur homonyme de l'autre entreprise ne voit que ses propres réceptions.
    own = _ok(other.post("/suppliers", json={"name": "Faso Import"}), 201)["id"]
    assert _ok(other.get(f"/stock/suppliers/{own}/summary"))["validated_count"] == 0
    # SQL (rôle applicatif) : la RLS masque les réceptions et les lignes de l'autre tenant.
    set_db_context(db, tenant_id=beta.tenant_id)
    for table in ("stock_entries", "stock_entry_lines"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    db.rollback()
    alpha = owner_db.execute(
        text("SELECT tenant_id FROM suppliers WHERE id = :id"), {"id": world.supplier}
    ).scalar_one()
    set_db_context(db, tenant_id=alpha)
    count = db.execute(
        text("SELECT count(*) FROM stock_entries WHERE supplier_id = :s"), {"s": world.supplier}
    ).scalar_one()
    assert count == 4
    db.rollback()
    indexes = owner_db.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'stock_entries'")
    ).scalars()
    assert "ix_stock_entries_tenant_supplier" in set(indexes)
