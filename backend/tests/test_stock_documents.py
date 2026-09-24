"""Entrées, sorties et motifs de sortie (ENT-*, SOR-*, Q1 à Q7)."""

import threading
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World


def test_system_exit_reasons_are_created_and_protected(world: World) -> None:
    assert {"CONSOMMATION_INTERNE", "DOTATION", "PERTE", "CASSE", "ECHANTILLON", "AUTRE"} <= set(
        world.reasons
    )
    perte = world.reasons["PERTE"]
    renamed = world.owner.patch(f"/stock/exit-reasons/{perte}", json={"label": "Vol"})
    assert renamed.status_code == 403 and renamed.json()["code"] == "system_exit_reason"
    # Désactivation / réactivation : non destructives, autorisées.
    assert world.owner.post(f"/stock/exit-reasons/{perte}/deactivate").json()["is_active"] is False
    assert world.owner.post(f"/stock/exit-reasons/{perte}/activate").json()["is_active"] is True

    custom = world.owner.post("/stock/exit-reasons", json={"label": "Don"}).json()
    assert custom["is_system"] is False and custom["code"] is None
    assert world.owner.post("/stock/exit-reasons", json={"label": "DON"}).status_code == 409
    updated = world.owner.patch(
        f"/stock/exit-reasons/{custom['id']}", json={"label": "Don caritatif"}
    )
    assert updated.json()["label"] == "Don caritatif"


# --- Entrées -----------------------------------------------------------------------------------


def test_entry_lifecycle_updates_stock_and_cost(world: World, owner_db: Session) -> None:
    draft = sh.entry(world, [(0, "10", "1500"), (1, "4", "250.50")])
    assert draft["number"] == "ENT-000001" and draft["status"] == "DRAFT"
    assert draft["lines"][1]["amount"] == "1002.00"
    assert draft["total_amount"] == "16002.00"
    assert sh.level(owner_db, world, 0) == ("none", "none")  # brouillon : aucun impact (ENT-06)

    edited = world.owner.put(
        f"/stock/entries/{draft['id']}",
        json={
            "supplier_id": world.supplier,
            "lines": [{"article_id": world.articles[0], "quantity": "10", "unit_cost": "1500"}],
        },
    )
    assert edited.status_code == 200 and edited.json()["line_count"] == 1

    validated = world.owner.post(f"/stock/entries/{draft['id']}/validate").json()
    assert validated["status"] == "VALIDATED" and validated["validated_by_name"] == "Owner alpha"
    assert sh.level(owner_db, world, 0) == ("10.000", "1500.0000")

    sh.validated_entry(world, [(0, "5", "1800")])
    assert sh.level(owner_db, world, 0) == ("15.000", "1600.0000")  # CMUP recalculé (STK-05)

    again = world.owner.post(f"/stock/entries/{draft['id']}/validate")
    assert again.status_code == 409 and again.json()["code"] == "document_not_draft"
    locked = world.owner.put(f"/stock/entries/{draft['id']}", json={"supplier_id": world.supplier})
    assert locked.json()["code"] == "document_not_draft"
    assert sh.count(owner_db, "SELECT count(*) FROM stock_movements") == 2


def test_initial_stock_is_a_traceable_entry(world: World, owner_db: Session) -> None:
    """Q5 : stock initial = entrée normale de type INITIAL_STOCK, sans fournisseur."""
    entry = world.owner.post(
        "/stock/entries",
        json={
            "site_id": world.site,
            "kind": "INITIAL_STOCK",
            "lines": [{"article_id": world.articles[0], "quantity": "12", "unit_cost": "90"}],
        },
    ).json()
    world.owner.post(f"/stock/entries/{entry['id']}/validate")
    assert sh.level(owner_db, world, 0) == ("12.000", "90.0000")
    movement = owner_db.execute(
        text("SELECT movement_type, source_type, user_id IS NOT NULL FROM stock_movements")
    ).one()
    assert tuple(movement) == ("ENTRY", "stock_entry", True)
    purchase = world.owner.post("/stock/entries", json={"site_id": world.site, "kind": "PURCHASE"})
    assert purchase.json()["code"] == "supplier_required"


def test_entry_validation_rules(world: World, owner_db: Session) -> None:
    owner = world.owner
    assert (
        owner.post(
            "/stock/entries",
            json={
                "site_id": world.site,
                "supplier_id": world.supplier,
                "operation_date": "2999-01-01",
            },
        ).json()["code"]
        == "future_operation_date"
    )
    duplicate = owner.post(
        "/stock/entries",
        json={
            "site_id": world.site,
            "supplier_id": world.supplier,
            "lines": [{"article_id": world.articles[0], "quantity": "1", "unit_cost": "1"}] * 2,
        },
    )
    assert duplicate.json()["code"] == "duplicate_article_line"
    for quantity in ("0", "-1", "1.2345"):
        bad = owner.post(
            "/stock/entries",
            json={
                "site_id": world.site,
                "supplier_id": world.supplier,
                "lines": [
                    {"article_id": world.articles[0], "quantity": quantity, "unit_cost": "1"}
                ],
            },
        )
        assert bad.status_code == 422, quantity
    empty = sh.entry(world, [])
    assert owner.post(f"/stock/entries/{empty['id']}/validate").json()["code"] == "document_empty"

    draft = sh.entry(world, [(0, "1", "1")])
    owner.post(f"/catalog/articles/{world.articles[0]}/deactivate")
    refused = owner.post(f"/stock/entries/{draft['id']}/validate")
    assert refused.json()["code"] == "article_inactive"
    assert sh.count(owner_db, "SELECT count(*) FROM stock_movements") == 0
    assert owner.post("/stock/entries", json={"supplier_id": world.supplier}).json()["code"] == (
        "site_required"
    )


def test_entry_cancellation(world: World, owner_db: Session) -> None:
    entry = sh.validated_entry(world, [(0, "10", "100")])
    short = world.owner.post(f"/stock/entries/{entry['id']}/cancel", json={"reason": "oups"})
    assert short.status_code == 422  # motif : 5 caractères minimum (ENT-08)

    sh.validated_entry(world, [(0, "10", "200")])  # stock 20, CMUP 150
    exit_doc = sh.exit_doc(world, [(0, "15")])
    world.owner.post(f"/stock/exits/{exit_doc['id']}/validate")  # stock 5
    audits_before = sh.count(owner_db, "SELECT count(*) FROM audit_logs")
    refused = world.owner.post(
        f"/stock/entries/{entry['id']}/cancel", json={"reason": "Erreur de saisie"}
    )
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"
    assert world.owner.get(f"/stock/entries/{entry['id']}").json()["status"] == "VALIDATED"
    assert (
        sh.count(owner_db, "SELECT count(*) FROM audit_logs") == audits_before
    )  # pas d'audit mensonger

    world.owner.post(
        f"/stock/exits/{exit_doc['id']}/cancel", json={"reason": "Sortie saisie par erreur"}
    )
    cancelled = world.owner.post(
        f"/stock/entries/{entry['id']}/cancel", json={"reason": "Erreur de saisie"}
    ).json()
    assert (
        cancelled["status"] == "CANCELLED"
        and cancelled["cancellation_reason"] == "Erreur de saisie"
    )
    assert sh.level(owner_db, world, 0) == ("10.000", "150.0000")  # CMUP inchangé (STK-06)
    origin = owner_db.execute(
        text(
            "SELECT count(*) FROM stock_movements WHERE movement_type = 'CANCELLATION' "
            "AND origin_movement_id IS NOT NULL"
        )
    ).scalar_one()
    assert origin == 2
    assert (
        world.owner.post(
            f"/stock/entries/{entry['id']}/cancel", json={"reason": "Deuxième fois"}
        ).json()["code"]
        == "document_not_validated"
    )


# --- Sorties -----------------------------------------------------------------------------------


def test_exit_uses_site_average_cost_and_never_goes_negative(
    world: World, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "3", "100"), (1, "10", "50")])
    sh.validated_entry(world, [(0, "3", "101")])  # CMUP = 100.5
    too_much = sh.exit_doc(world, [(1, "2"), (0, "7")])
    audits = sh.count(owner_db, "SELECT count(*) FROM audit_logs")
    refused = world.owner.post(f"/stock/exits/{too_much['id']}/validate")
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"
    assert refused.json()["articles"][0]["reference"] == "A-0"
    # Rien d'écrit : ni stock (même pour la ligne valide), ni mouvement, ni audit.
    assert sh.level(owner_db, world, 1) == ("10.000", "50.0000")
    assert world.owner.get(f"/stock/exits/{too_much['id']}").json()["status"] == "DRAFT"
    assert sh.count(owner_db, "SELECT count(*) FROM audit_logs") == audits

    ok = sh.exit_doc(world, [(0, "4")])
    validated = world.owner.post(f"/stock/exits/{ok['id']}/validate").json()
    line = validated["lines"][0]
    assert (line["unit_cost"], line["amount"]) == ("100.5000", "402.00")  # figés (SOR-03)
    assert sh.level(owner_db, world, 0) == ("2.000", "100.5000")  # CMUP inchangé (Q1)
    assert validated["number"] == "SOR-000002"


def test_inactive_reason_cannot_be_used(world: World) -> None:
    world.owner.post(f"/stock/exit-reasons/{world.reasons['CASSE']}/deactivate")
    response = world.owner.post(
        "/stock/exits", json={"site_id": world.site, "reason_id": world.reasons["CASSE"]}
    )
    assert response.json()["code"] == "exit_reason_inactive"


def test_stock_is_per_site(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    depot_entry = world.owner.post(
        "/stock/entries",
        json={
            "site_id": world.site2,
            "supplier_id": world.supplier,
            "lines": [{"article_id": world.articles[0], "quantity": "4", "unit_cost": "300"}],
        },
    ).json()
    world.owner.post(f"/stock/entries/{depot_entry['id']}/validate")
    assert sh.level(owner_db, world, 0) == ("10.000", "100.0000")
    assert sh.level(owner_db, world, 0, world.site2) == ("4.000", "300.0000")  # CMUP par site (Q1)
    depot_exit = world.owner.post(
        "/stock/exits",
        json={
            "site_id": world.site2,
            "reason_id": world.reasons["PERTE"],
            "lines": [{"article_id": world.articles[0], "quantity": "5"}],
        },
    ).json()
    refused = world.owner.post(f"/stock/exits/{depot_exit['id']}/validate")
    assert refused.json()["code"] == "insufficient_stock"  # le stock de l'autre site ne compte pas


# --- Concurrence par l'API ------------------------------------------------------------------------


def test_concurrent_double_validation_applies_once(
    world: World, app: Any, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    document = sh.exit_doc(world, [(0, "6")])
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def validate() -> None:
        with TestClient(app) as client:
            api = Api(client, world.owner.token)
            barrier.wait()
            statuses.append(api.post(f"/stock/exits/{document['id']}/validate").status_code)

    threads = [threading.Thread(target=validate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(statuses) == [200, 409]
    assert sh.level(owner_db, world, 0)[0] == "4.000"
    assert (
        sh.count(owner_db, "SELECT count(*) FROM stock_movements WHERE movement_type = 'EXIT'") == 1
    )


# --- Permissions, sites, abonnement, isolation ------------------------------------------


def test_permissions_by_role(world: World, client: Any) -> None:
    entry = sh.validated_entry(world, [(0, "10", "100")])
    manager = sh.member(world, client, "stock@example.com", "stock_manager", all_sites=True)
    viewer = sh.member(world, client, "lecteur@example.com", "viewer", all_sites=True)

    draft = manager.post(
        "/stock/exits",
        json={
            "site_id": world.site,
            "reason_id": world.reasons["PERTE"],
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        },
    )
    assert draft.status_code == 201
    assert manager.get("/stock/exit-reasons?status=active").status_code == 200  # choix du motif
    assert manager.post("/stock/exit-reasons", json={"label": "X"}).json()["code"] == (
        "permission_denied"
    )  # administration des motifs réservée (SOR-05)
    assert manager.post(f"/stock/exits/{draft.json()['id']}/validate").status_code == 200
    cancel = manager.post(f"/stock/entries/{entry['id']}/cancel", json={"reason": "Erreur"})
    assert cancel.status_code == 403  # annulation : hors Gestionnaire de stock

    assert viewer.get("/stock/entries").json()["total"] == 1
    assert viewer.post("/stock/entries", json={"site_id": world.site}).status_code == 403


def test_documents_are_restricted_to_accessible_sites(world: World, client: Any) -> None:
    depot_entry = world.owner.post(
        "/stock/entries",
        json={
            "site_id": world.site2,
            "supplier_id": world.supplier,
        },
    ).json()
    shop_only = sh.member(
        world, client, "boutique@example.com", "stock_manager", site_ids=[world.site]
    )
    listing = shop_only.get("/stock/entries").json()
    assert listing["total"] == 0
    assert shop_only.get(f"/stock/entries/{depot_entry['id']}").status_code == 404
    denied = shop_only.post(
        "/stock/entries", json={"site_id": world.site2, "supplier_id": world.supplier}
    )
    assert denied.json()["code"] == "site_access_denied"
    shop_only.site_id = world.site  # type: ignore[assignment]
    mismatch = shop_only.post(
        "/stock/entries", json={"site_id": world.site2, "supplier_id": world.supplier}
    )
    assert mismatch.json()["code"] == "site_mismatch"
    created = shop_only.post("/stock/entries", json={"supplier_id": world.supplier})
    assert created.status_code == 201 and created.json()["site_id"] == world.site


def test_expired_subscription_blocks_stock_operations(world: World, owner_db: Session) -> None:
    draft = sh.entry(world, [(0, "1", "1")])
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert world.owner.get("/stock/entries").status_code == 200
    blocked = world.owner.post(f"/stock/entries/{draft['id']}/validate")
    assert blocked.status_code == 403 and blocked.json()["code"] == "subscription_restricted"


def test_isolation_between_tenants(world: World, provision: Any, api_for: Any) -> None:
    entry = sh.entry(world, [(0, "1", "1")])
    provision("beta")
    other = api_for("owner@beta.example.com")
    assert other.get(f"/stock/entries/{entry['id']}").status_code == 404
    assert other.post(f"/stock/entries/{entry['id']}/validate").status_code == 404
    assert other.get("/stock/entries").json()["total"] == 0
    beta_site = other.get("/sites").json()[0]["id"]
    beta_supplier = other.post("/suppliers", json={"name": "B"}).json()["id"]
    foreign_article = other.post(
        "/stock/entries",
        json={
            "site_id": beta_site,
            "supplier_id": beta_supplier,
            "lines": [{"article_id": world.articles[0], "quantity": "1", "unit_cost": "1"}],
        },
    )
    assert foreign_article.json()["code"] == "article_not_found"
    assert other.post("/stock/entries", json={"site_id": world.site}).json()["code"] == (
        "site_access_denied"
    )
    assert (
        len(
            {r["id"] for r in other.get("/stock/exit-reasons").json()["items"]}
            & set(world.reasons.values())
        )
        == 0
    )


def test_audit_trail(world: World) -> None:
    entry = sh.validated_entry(world, [(0, "2", "10")])
    world.owner.post(f"/stock/entries/{entry['id']}/cancel", json={"reason": "Doublon de saisie"})
    items = world.owner.get("/audit-logs?action=stock_entry").json()["items"]
    actions = [i["action"] for i in items]
    assert actions == ["stock_entry.cancelled", "stock_entry.validated", "stock_entry.created"]
    assert items[0]["data"] == {"number": "ENT-000001", "reason": "Doublon de saisie"}
    assert items[1]["data"]["total"] == "20.00"
    assert items[0]["site_id"] == world.site
