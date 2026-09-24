"""Transferts inter-sites (Phase 2.5) : cycle de vie, stock des deux sites, CMUP, atomicité,
double validation, concurrence (transfert / vente, transferts croisés), annulation, plan
(fonctionnalité ``stock.transfers``), RBAC, périmètre des sites, audit, isolation API et SQL
(rôle applicatif réel, RLS)."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.platform.subscriptions.service import change_plan
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

TRANSFER_PERMISSIONS = {
    "stock.transfer.view",
    "stock.transfer.create",
    "stock.transfer.update",
    "stock.transfer.validate",
    "stock.transfer.cancel",
}


def _body(w: World, lines: list[tuple[int, str]], **extra: Any) -> dict[str, Any]:
    return {
        "source_site_id": w.site,
        "destination_site_id": w.site2,
        "lines": [{"article_id": w.articles[i], "quantity": q} for i, q in lines],
        **extra,
    }


def _transfer(w: World, lines: list[tuple[int, str]], api: Api | None = None, **extra: Any) -> Any:
    response = (api or w.owner).post("/stock/transfers", json=_body(w, lines, **extra))
    assert response.status_code == 201, response.text
    return response.json()


def _validate(api: Api, transfer: dict[str, Any]) -> Any:
    return api.post(f"/stock/transfers/{transfer['id']}/validate")


def _movements(owner_db: Session, *kinds: str) -> list[tuple[str, str, str, str | None]]:
    """(type, site, quantité, coût) des mouvements des transferts, dans l'ordre d'écriture."""
    owner_db.expire_all()
    return [
        (row[0], str(row[1]), row[2], row[3])
        for row in owner_db.execute(
            text(
                "SELECT movement_type, site_id, quantity::text, unit_cost::text "
                "FROM stock_movements WHERE source_type = 'stock_transfer' "
                "AND movement_type = ANY(:kinds) ORDER BY occurred_at, id"
            ),
            {"kinds": list(kinds or ("TRANSFER_OUT", "TRANSFER_IN", "CANCELLATION"))},
        )
    ]


def _scoped_member(w: World, client: TestClient, email: str, roles: list[dict[str, Any]]) -> Api:
    """Membre ayant accès aux deux sites, avec des rôles éventuellement limités à un site."""
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": roles,
        "site_ids": [w.site, w.site2],
    }
    created = w.owner.post("/members", json=body)
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _role_ids(w: World) -> dict[str, str]:
    return {r["template_code"]: r["id"] for r in w.owner.get("/roles").json()}


# --- Création et brouillon ---------------------------------------------------------------------


def test_create_draft_without_stock_effect(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    transfer = _transfer(world, [(0, "3"), (1, "2.5")], comment="Réassort dépôt")
    assert transfer["number"] == "TRF-000001"
    assert transfer["status"] == "DRAFT"
    assert (transfer["source_site_id"], transfer["destination_site_id"]) == (
        world.site,
        world.site2,
    )
    assert transfer["source_site_name"] and transfer["destination_site_name"] == "Dépôt"
    assert [(li["line_no"], li["quantity"]) for li in transfer["lines"]] == [
        (1, "3.000"),
        (2, "2.500"),
    ]
    # Coût et valeur : inconnus tant que le transfert n'est pas validé.
    assert transfer["lines"][0]["unit_cost"] is None and transfer["total_amount"] is None
    assert sh.level(owner_db, world, 0) == ("10.000", "100.0000")
    assert sh.level(owner_db, world, 0, world.site2) == ("none", "none")
    assert _movements(owner_db) == []
    assert _transfer(world, [(0, "1")])["number"] == "TRF-000002"


def test_selected_site_is_the_default_source(world: World) -> None:
    world.owner.site_id = uuid.UUID(world.site)
    body = _body(world, [(0, "1")])
    del body["source_site_id"]
    created = world.owner.post("/stock/transfers", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["source_site_id"] == world.site
    world.owner.site_id = None
    missing = world.owner.post("/stock/transfers", json=body)
    assert missing.status_code == 422 and missing.json()["code"] == "site_required"


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"destination_site_id": "SOURCE"}, 422, "same_site_transfer"),
        ({"destination_site_id": None}, 422, "validation_error"),
        ({"lines": []}, 422, "validation_error"),
        ({"lines": [{"article_id": "A0", "quantity": "0"}]}, 422, "validation_error"),
        ({"lines": [{"article_id": "A0", "quantity": "-1"}]}, 422, "validation_error"),
        (
            {
                "lines": [
                    {"article_id": "A0", "quantity": "1"},
                    {"article_id": "A0", "quantity": "2"},
                ]
            },
            422,
            "duplicate_article_line",
        ),
        ({"lines": [{"article_id": "UNKNOWN", "quantity": "1"}]}, 422, "article_not_found"),
        ({"destination_site_id": "UNKNOWN"}, 403, "site_access_denied"),
        ({"operation_date": "2999-01-01"}, 422, "future_operation_date"),
    ],
)
def test_invalid_transfers(world: World, change: dict[str, Any], status: int, code: str) -> None:
    body = _body(world, [(0, "1")])
    for key, value in change.items():
        if value == "SOURCE":
            value = world.site
        elif value == "UNKNOWN":
            value = str(uuid.uuid4())
        elif isinstance(value, list):
            value = [
                {
                    **line,
                    "article_id": {"A0": world.articles[0], "UNKNOWN": str(uuid.uuid4())}[
                        line["article_id"]
                    ],
                }
                for line in value
            ]
        body[key] = value
    response = world.owner.post("/stock/transfers", json=body)
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def test_inactive_article_and_site_are_refused(world: World, owner_db: Session) -> None:
    transfer = _transfer(world, [(0, "1")])
    assert world.owner.post(f"/catalog/articles/{world.articles[0]}/deactivate").status_code == 200
    refused = world.owner.post("/stock/transfers", json=_body(world, [(0, "1")]))
    assert refused.json()["code"] == "article_inactive"
    # Un brouillon dont l'article a été désactivé ne peut plus être validé.
    assert _validate(world.owner, transfer).json()["code"] == "article_inactive"
    world.owner.post(f"/catalog/articles/{world.articles[0]}/activate")
    # Site destination désactivé : hors des sites accessibles.
    assert world.owner.patch(f"/sites/{world.site2}", json={"is_active": False}).status_code == 200
    denied = world.owner.post("/stock/transfers", json=_body(world, [(1, "1")]))
    assert denied.status_code == 403 and denied.json()["code"] == "site_access_denied"


def test_update_draft_only(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "10", "50")])
    transfer = _transfer(world, [(0, "1")])
    url = f"/stock/transfers/{transfer['id']}"
    updated = world.owner.put(
        url,
        json={
            "destination_site_id": world.site2,
            "comment": "Deux articles",
            "lines": [
                {"article_id": world.articles[1], "quantity": "4"},
                {"article_id": world.articles[0], "quantity": "2"},
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    assert [li["article_id"] for li in updated.json()["lines"]] == [
        world.articles[1],
        world.articles[0],
    ]
    # Le site source est fixé à la création : même site en destination refusé.
    same = world.owner.put(url, json=_body(world, [(0, "1")], destination_site_id=world.site))
    assert same.json()["code"] == "same_site_transfer"
    assert _validate(world.owner, transfer).status_code == 200
    locked = world.owner.put(url, json=_body(world, [(0, "9")]))
    assert locked.status_code == 409 and locked.json()["code"] == "document_not_draft"


# --- Validation : stock, CMUP, atomicité, idempotence -------------------------------------------


def test_validation_moves_stock_between_sites_and_keeps_cmup(
    world: World, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    sh.validated_entry(world, [(0, "5", "200")], site_id=world.site2)
    transfer = _transfer(world, [(0, "3")])
    validated = _validate(world.owner, transfer)
    assert validated.status_code == 200, validated.text
    body = validated.json()
    assert body["status"] == "VALIDATED" and body["validated_by_name"]
    # Source 10 − 3 = 7, CMUP inchangé ; destination 5 + 3 = 8, CMUP pondéré :
    # (5 × 200 + 3 × 100) / 8 = 162,5.
    assert sh.level(owner_db, world, 0) == ("7.000", "100.0000")
    assert sh.level(owner_db, world, 0, world.site2) == ("8.000", "162.5000")
    # Coût du transfert = CMUP du site source, figé sur la ligne ; valeur transférée.
    assert body["lines"][0]["unit_cost"] == "100.0000"
    assert body["lines"][0]["amount"] == "300.00" and body["total_amount"] == "300.00"
    assert sorted(_movements(owner_db)) == [
        ("TRANSFER_IN", world.site2, "3.000", "100.0000"),
        ("TRANSFER_OUT", world.site, "-3.000", "100.0000"),
    ]
    # Journal : les deux mouvements portent le numéro du transfert.
    journal = world.owner.get("/stock/movements", params={"search": "TRF-000001"}).json()
    assert {(m["movement_type"], m["site_id"], m["document_number"]) for m in journal["items"]} == {
        ("TRANSFER_OUT", world.site, "TRF-000001"),
        ("TRANSFER_IN", world.site2, "TRF-000001"),
    }
    only_in = world.owner.get("/stock/movements", params={"movement_type": "TRANSFER_IN"})
    assert only_in.json()["total"] == 1


def test_destination_without_stock_takes_source_cost(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "4", "150")])
    assert _validate(world.owner, _transfer(world, [(0, "4")])).status_code == 200
    assert sh.level(owner_db, world, 0) == ("0.000", "150.0000")
    assert sh.level(owner_db, world, 0, world.site2) == ("4.000", "150.0000")


def test_insufficient_source_stock_changes_nothing(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "2", "100")])
    sh.validated_entry(world, [(0, "5", "100")], site_id=world.site2)
    transfer = _transfer(world, [(0, "3")])
    refused = _validate(world.owner, transfer)
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"
    assert refused.json()["articles"] == [
        {
            "article_id": world.articles[0],
            "site_id": world.site,
            "reference": "A-0",
            "available": "2.000",
        }
    ]
    assert world.owner.get(f"/stock/transfers/{transfer['id']}").json()["status"] == "DRAFT"
    assert sh.level(owner_db, world, 0) == ("2.000", "100.0000")
    assert sh.level(owner_db, world, 0, world.site2) == ("5.000", "100.0000")
    assert _movements(owner_db) == []


def test_validation_is_atomic_across_lines(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "2", "50")])
    transfer = _transfer(world, [(0, "5"), (1, "20")])
    refused = _validate(world.owner, transfer)
    assert refused.json()["code"] == "insufficient_stock"
    assert [a["reference"] for a in refused.json()["articles"]] == ["A-1"]
    # Ni sortie ni entrée, pour aucun des deux articles ; aucun niveau créé sur la destination.
    assert sh.level(owner_db, world, 0) == ("10.000", "100.0000")
    assert sh.level(owner_db, world, 1) == ("2.000", "50.0000")
    assert sh.level(owner_db, world, 0, world.site2) == ("none", "none")
    assert sh.level(owner_db, world, 1, world.site2) == ("none", "none")
    assert _movements(owner_db) == []


def test_second_validation_is_refused(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    transfer = _transfer(world, [(0, "3")])
    assert _validate(world.owner, transfer).status_code == 200
    again = _validate(world.owner, transfer)
    assert again.status_code == 409 and again.json()["code"] == "document_not_draft"
    assert len(_movements(owner_db)) == 2
    assert sh.level(owner_db, world, 0)[0] == "7.000"


# --- Concurrence -------------------------------------------------------------------------------


def _run_concurrently(app: Any, token: str, calls: list[tuple[str, str]]) -> list[int]:
    barrier = threading.Barrier(len(calls))
    statuses: list[int] = []

    def run(path: str) -> None:
        with TestClient(app) as client:
            api = Api(client, token)
            barrier.wait()
            statuses.append(api.post(path).status_code)

    threads = [threading.Thread(target=run, args=(path,)) for _, path in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return sorted(statuses)


def test_concurrent_transfer_and_sale_never_oversell(
    world: World, app: Any, owner_db: Session
) -> None:
    """Stock 5 sur A : transfert A → B de 4 et vente de 4 sur A, validés en même temps."""
    sh.validated_entry(world, [(0, "5", "100")])
    transfer = _transfer(world, [(0, "4")])
    sale = world.owner.post(
        "/sales",
        json={"site_id": world.site, "lines": [{"article_id": world.articles[0], "quantity": "4"}]},
    ).json()
    statuses = _run_concurrently(
        app,
        world.owner.token,
        [
            ("transfer", f"/stock/transfers/{transfer['id']}/validate"),
            ("sale", f"/sales/{sale['id']}/validate"),
        ],
    )
    assert statuses == [200, 422]
    source = sh.level(owner_db, world, 0)[0]
    assert source == "1.000"  # jamais négatif, une seule consommation
    moved = _movements(owner_db, "TRANSFER_IN")
    assert sh.level(owner_db, world, 0, world.site2)[0] == ("4.000" if moved else "none")


def test_crossed_transfers_do_not_deadlock(world: World, app: Any, owner_db: Session) -> None:
    """A → B et B → A simultanés sur les mêmes articles : verrous pris dans le même ordre."""
    sh.validated_entry(world, [(0, "5", "100"), (1, "5", "100")])
    sh.validated_entry(world, [(0, "5", "100"), (1, "5", "100")], site_id=world.site2)
    forward = _transfer(world, [(0, "3"), (1, "2")])
    backward = _transfer(
        world, [(1, "4"), (0, "1")], source_site_id=world.site2, destination_site_id=world.site
    )
    statuses = _run_concurrently(
        app,
        world.owner.token,
        [
            ("forward", f"/stock/transfers/{forward['id']}/validate"),
            ("backward", f"/stock/transfers/{backward['id']}/validate"),
        ],
    )
    assert statuses == [200, 200]
    assert sh.level(owner_db, world, 0)[0] == "3.000"  # 5 − 3 + 1
    assert sh.level(owner_db, world, 1)[0] == "7.000"  # 5 − 2 + 4
    assert sh.level(owner_db, world, 0, world.site2)[0] == "7.000"
    assert sh.level(owner_db, world, 1, world.site2)[0] == "3.000"


def test_concurrent_double_validation_applies_once(
    world: World, app: Any, owner_db: Session
) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    transfer = _transfer(world, [(0, "3")])
    path = f"/stock/transfers/{transfer['id']}/validate"
    assert _run_concurrently(app, world.owner.token, [("a", path), ("b", path)]) == [200, 409]
    assert len(_movements(owner_db)) == 2
    assert sh.level(owner_db, world, 0)[0] == "7.000"
    assert sh.level(owner_db, world, 0, world.site2)[0] == "3.000"


# --- Annulation --------------------------------------------------------------------------------


def test_cancel_validated_transfer_restores_both_sites(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    sh.validated_entry(world, [(0, "5", "200")], site_id=world.site2)
    transfer = _transfer(world, [(0, "3")])
    _validate(world.owner, transfer)
    url = f"/stock/transfers/{transfer['id']}/cancel"
    assert world.owner.post(url, json={"reason": "abc"}).status_code == 422  # motif trop court
    cancelled = world.owner.post(url, json={"reason": "Erreur de destination"})
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "CANCELLED" and body["cancellation_reason"] == "Erreur de destination"
    # Stocks rétablis ; CMUP inchangés (STK-06) : pas de reconstruction rétroactive.
    assert sh.level(owner_db, world, 0) == ("10.000", "100.0000")
    assert sh.level(owner_db, world, 0, world.site2) == ("5.000", "162.5000")
    assert sorted(_movements(owner_db, "CANCELLATION"), key=lambda m: m[2]) == [
        ("CANCELLATION", world.site2, "-3.000", "100.0000"),
        ("CANCELLATION", world.site, "3.000", "100.0000"),
    ]
    # Mouvements inverses reliés aux mouvements d'origine, jamais modifiés.
    owner_db.expire_all()
    links = owner_db.execute(
        text(
            "SELECT c.site_id, o.movement_type FROM stock_movements c "
            "JOIN stock_movements o ON o.id = c.origin_movement_id "
            "WHERE c.movement_type = 'CANCELLATION'"
        )
    ).all()
    assert {(str(site), kind) for site, kind in links} == {
        (world.site2, "TRANSFER_IN"),
        (world.site, "TRANSFER_OUT"),
    }
    assert len(_movements(owner_db, "TRANSFER_OUT", "TRANSFER_IN")) == 2
    again = world.owner.post(url, json={"reason": "Deuxième tentative"})
    assert again.status_code == 409 and again.json()["code"] == "transfer_already_cancelled"


def test_cancel_draft_has_no_stock_effect(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    transfer = _transfer(world, [(0, "3")])
    url = f"/stock/transfers/{transfer['id']}"
    cancelled = world.owner.post(f"{url}/cancel", json={"reason": "Brouillon abandonné"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert _movements(owner_db) == []
    assert _validate(world.owner, transfer).json()["code"] == "document_not_draft"


def test_cancel_refused_when_destination_stock_is_gone(world: World, owner_db: Session) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    transfer = _transfer(world, [(0, "3")])
    _validate(world.owner, transfer)
    exit_doc = world.owner.post(
        "/stock/exits",
        json={
            "site_id": world.site2,
            "reason_id": world.reasons["PERTE"],
            "lines": [{"article_id": world.articles[0], "quantity": "2"}],
        },
    ).json()
    assert world.owner.post(f"/stock/exits/{exit_doc['id']}/validate").status_code == 200
    refused = world.owner.post(
        f"/stock/transfers/{transfer['id']}/cancel", json={"reason": "Trop tard"}
    )
    assert refused.status_code == 422 and refused.json()["code"] == "insufficient_stock"
    assert refused.json()["articles"][0]["site_id"] == world.site2
    assert world.owner.get(f"/stock/transfers/{transfer['id']}").json()["status"] == "VALIDATED"
    assert sh.level(owner_db, world, 0)[0] == "7.000"
    assert sh.level(owner_db, world, 0, world.site2)[0] == "1.000"
    assert _movements(owner_db, "CANCELLATION") == []


# --- Liste --------------------------------------------------------------------------------------


def test_list_filters_and_sort(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    sh.validated_entry(world, [(0, "10", "100")], site_id=world.site2)
    first = _transfer(world, [(0, "1")])
    _validate(world.owner, first)
    _transfer(world, [(0, "2")], source_site_id=world.site2, destination_site_id=world.site)

    def numbers(**params: Any) -> list[str]:
        response = world.owner.get("/stock/transfers", params=params)
        assert response.status_code == 200, response.text
        return [t["number"] for t in response.json()["items"]]

    assert numbers() == ["TRF-000002", "TRF-000001"]
    assert numbers(sort="number") == ["TRF-000001", "TRF-000002"]
    assert numbers(status="VALIDATED") == ["TRF-000001"]
    assert numbers(source_site_id=world.site2) == ["TRF-000002"]
    assert numbers(destination_site_id=world.site2) == ["TRF-000001"]
    assert numbers(search="000002") == ["TRF-000002"]
    assert numbers(date_from="2000-01-01", date_to="2000-01-02") == []
    item = world.owner.get("/stock/transfers").json()["items"][1]
    assert item["line_count"] == 1 and item["lines"] == []
    assert world.owner.get("/stock/transfers", params={"sort": "total"}).status_code == 400


def test_levels_can_be_filtered_by_articles(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100"), (1, "4", "50")])
    response = world.owner.get(
        "/stock/levels",
        params={"site_id": world.site, "article_id": [world.articles[1], world.articles[2]]},
    )
    assert response.status_code == 200
    items = {r["reference"]: r for r in response.json()["items"]}
    assert set(items) == {"A-1", "A-2"}
    assert items["A-1"]["quantity"] == "4.000" and items["A-2"]["state"] == "not_stocked"


# --- Plan, RBAC, périmètre des sites ---------------------------------------------------------


OPERATION_PERMISSIONS = TRANSFER_PERMISSIONS - {"stock.transfer.view"}


def test_standard_plan_allows_consultation_only(provision: Any, api_for: Any) -> None:
    provision("std", profile="quincaillerie", plan="STANDARD")
    owner: Api = api_for("owner@std.example.com")
    caps = owner.get("/me/capabilities").json()
    assert "stock.transfers" not in caps["features"]
    # Consultation (historique) toujours accordée ; opérations liées à la fonctionnalité.
    assert "stock.transfer.view" in caps["permissions"]
    assert not OPERATION_PERMISSIONS & set(caps["permissions"])
    assert "stock.entry.create" in caps["permissions"]  # le reste du stock est disponible
    # Opérations ni proposées à l'édition des rôles, ni accordables à un rôle personnalisé.
    available = {p["code"] for p in owner.get("/permissions").json()}
    assert "stock.transfer.view" in available and not OPERATION_PERMISSIONS & available
    role = owner.post(
        "/roles", json={"name": "Logisticien", "permissions": ["stock.transfer.create"]}
    )
    assert role.status_code == 422 and role.json()["code"] == "unknown_permission"
    assert owner.get("/stock/transfers").json()["total"] == 0
    site = owner.get("/sites").json()[0]["id"]
    created = owner.post(
        "/stock/transfers",
        json={"source_site_id": site, "destination_site_id": site, "lines": []},
    )
    assert created.status_code == 403 and created.json()["code"] == "feature_unavailable"


def test_downgrade_to_standard_keeps_history_read_only(
    world: World,
    client: Any,
    provision: Any,
    api_for: Any,
    app_engine: Engine,
    owner_db: Session,
) -> None:
    """ENTREPRISE → STANDARD : historique conservé et consultable ; plus aucune opération."""
    sh.validated_entry(world, [(0, "10", "100")])
    validated = _transfer(world, [(0, "3")])
    assert _validate(world.owner, validated).status_code == 200
    draft = _transfer(world, [(0, "1")])
    viewer = sh.member(world, client, "consultant@example.com", "viewer", all_sites=True)
    seller = sh.member(world, client, "vendeur@example.com", "seller", all_sites=True)
    shop = sh.member(world, client, "boutique@example.com", "viewer", site_ids=[world.site2])
    tenant_id = owner_db.execute(text("SELECT tenant_id FROM stock_transfers LIMIT 1")).scalar()

    with create_session_factory(app_engine)() as db:
        assert change_plan(db, tenant_id, "STANDARD", actor="test") == ("ENTREPRISE", "STANDARD")
        db.commit()

    caps = world.owner.get("/me/capabilities").json()
    assert "stock.transfers" not in caps["features"]
    assert "stock.transfer.view" in caps["permissions"]
    assert not OPERATION_PERMISSIONS & set(caps["permissions"])

    # Historique conservé et consultable (liste, détail, lignes, coûts, journal).
    for api in (world.owner, viewer):
        listed = api.get("/stock/transfers")
        assert listed.status_code == 200 and listed.json()["total"] == 2
        detail = api.get(f"/stock/transfers/{validated['id']}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "VALIDATED"
        assert detail.json()["lines"][0]["unit_cost"] == "100.0000"
    journal = world.owner.get("/stock/movements", params={"search": validated["number"]})
    assert journal.json()["total"] == 2

    # Plus aucune opération, quel que soit le rôle.
    url = f"/stock/transfers/{draft['id']}"
    for response in (
        world.owner.post("/stock/transfers", json=_body(world, [(0, "1")])),
        world.owner.put(url, json=_body(world, [(0, "2")])),
        _validate(world.owner, draft),
        world.owner.post(f"{url}/cancel", json={"reason": "Tentative après rétrogradation"}),
        world.owner.post(
            f"/stock/transfers/{validated['id']}/cancel", json={"reason": "Annulation interdite"}
        ),
    ):
        assert response.status_code == 403 and response.json()["code"] == "feature_unavailable"
    assert sh.level(owner_db, world, 0)[0] == "7.000"
    assert sh.level(owner_db, world, 0, world.site2)[0] == "3.000"
    assert world.owner.get(url).json()["status"] == "DRAFT"

    # RBAC et sites toujours appliqués : Vendeur sans consultation ; membre limité au dépôt :
    # les transferts touchant un site inaccessible restent invisibles.
    assert seller.get("/stock/transfers").json()["code"] == "permission_denied"
    assert shop.get("/stock/transfers").json()["total"] == 0
    assert shop.get(url).status_code == 404

    # RLS : une autre entreprise ne voit toujours rien.
    provision("beta", profile="quincaillerie", plan="STANDARD")
    beta: Api = api_for("owner@beta.example.com")
    assert beta.get("/stock/transfers").json()["total"] == 0
    assert beta.get(url).status_code == 404

    # Retour à ENTREPRISE : les opérations redeviennent possibles sur les mêmes données.
    with create_session_factory(app_engine)() as db:
        change_plan(db, tenant_id, "ENTREPRISE", actor="test")
        db.commit()
    assert _validate(world.owner, draft).status_code == 200


def test_enterprise_plan_grants_transfers(world: World) -> None:
    caps = world.owner.get("/me/capabilities").json()
    assert "stock.transfers" in caps["features"]
    assert set(caps["permissions"]) >= TRANSFER_PERMISSIONS


def test_permissions_by_base_role(world: World, client: Any) -> None:
    sh.validated_entry(world, [(0, "20", "100")])
    admin = sh.member(world, client, "admin@example.com", "administrator", all_sites=True)
    manager = sh.member(world, client, "gestion@example.com", "manager", all_sites=True)
    seller = sh.member(world, client, "vendeur@example.com", "seller", all_sites=True)
    viewer = sh.member(world, client, "consultant@example.com", "viewer", all_sites=True)
    body = _body(world, [(0, "1")])

    # Gestionnaire et Administrateur : création, modification, validation.
    for api in (manager, admin):
        created = api.post("/stock/transfers", json=body)
        assert created.status_code == 201, created.text
        transfer = created.json()
        assert api.put(f"/stock/transfers/{transfer['id']}", json=body).status_code == 200
        assert _validate(api, transfer).status_code == 200
    # Annulation : Administrateur seulement (comme les ventes et les documents de stock).
    transfer = world.owner.get("/stock/transfers", params={"status": "VALIDATED"}).json()
    target = transfer["items"][0]["id"]
    for api in (manager, seller, viewer):
        denied = api.post(f"/stock/transfers/{target}/cancel", json={"reason": "Tentative"})
        assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    assert (
        admin.post(f"/stock/transfers/{target}/cancel", json={"reason": "Erreur"}).status_code
        == 200
    )
    # Vendeur : aucun accès aux transferts.
    assert seller.get("/stock/transfers").json()["code"] == "permission_denied"
    assert seller.post("/stock/transfers", json=body).json()["code"] == "permission_denied"
    # Consultant : consultation seulement.
    assert viewer.get("/stock/transfers").status_code == 200
    assert viewer.get(f"/stock/transfers/{target}").status_code == 200
    assert viewer.post("/stock/transfers", json=body).json()["code"] == "permission_denied"


def test_member_limited_to_one_site(world: World, client: Any) -> None:
    """Accès au seul site A : A → B, B → A et les transferts impliquant B sont refusés."""
    sh.validated_entry(world, [(0, "10", "100")])
    sh.validated_entry(world, [(0, "10", "100")], site_id=world.site2)
    other = _transfer(world, [(0, "1")])  # A → B, créé par le propriétaire
    shop = sh.member(world, client, "boutique@example.com", "manager", site_ids=[world.site])

    a_to_b = shop.post("/stock/transfers", json=_body(world, [(0, "1")]))
    assert a_to_b.status_code == 403 and a_to_b.json()["code"] == "site_access_denied"
    b_to_a = shop.post(
        "/stock/transfers",
        json=_body(world, [(0, "1")], source_site_id=world.site2, destination_site_id=world.site),
    )
    assert b_to_a.status_code == 403 and b_to_a.json()["code"] == "site_access_denied"
    a_to_a = shop.post(
        "/stock/transfers", json=_body(world, [(0, "1")], destination_site_id=world.site)
    )
    assert a_to_a.status_code == 422 and a_to_a.json()["code"] == "same_site_transfer"
    # Un transfert touchant un site inaccessible est introuvable, quelle que soit l'action.
    assert shop.get("/stock/transfers").json()["total"] == 0
    url = f"/stock/transfers/{other['id']}"
    for response in (
        shop.get(url),
        shop.put(url, json=_body(world, [(0, "2")])),
        _validate(shop, other),
    ):
        assert response.status_code == 404 and response.json()["code"] == "stock_transfer_not_found"


def test_site_scoped_role_requires_permission_on_both_sites(
    world: World, client: Any, owner_db: Session
) -> None:
    """Accès aux deux sites, rôle Gestionnaire limité au site A : la permission manque sur B."""
    sh.validated_entry(world, [(0, "10", "100")])
    roles = _role_ids(world)
    scoped = _scoped_member(
        world, client, "site-a@example.com", [{"role_id": roles["manager"], "site_id": world.site}]
    )
    # Sans site sélectionné : le rôle limité au site A ne vaut pas pour tout le tenant.
    assert scoped.get("/stock/transfers").json()["code"] == "permission_denied"
    scoped.site_id = uuid.UUID(world.site)
    denied = scoped.post("/stock/transfers", json=_body(world, [(0, "1")]))
    assert denied.status_code == 403 and denied.json()["code"] == "site_permission_denied"

    both = _scoped_member(
        world,
        client,
        "deux-sites@example.com",
        [
            {"role_id": roles["manager"], "site_id": world.site},
            {"role_id": roles["manager"], "site_id": world.site2},
        ],
    )
    both.site_id = uuid.UUID(world.site)
    created = both.post("/stock/transfers", json=_body(world, [(0, "2")]))
    assert created.status_code == 201, created.text
    assert _validate(both, created.json()).status_code == 200
    assert sh.level(owner_db, world, 0, world.site2)[0] == "2.000"


def test_selected_site_must_be_part_of_the_transfer(world: World) -> None:
    third = world.owner.post("/sites", json={"name": "Annexe", "code": "ANNEXE", "kind": "store"})
    assert third.status_code == 201, third.text
    world.owner.site_id = uuid.UUID(third.json()["id"])
    mismatch = world.owner.post("/stock/transfers", json=_body(world, [(0, "1")]))
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "site_mismatch"
    # Site sélectionné = destination : autorisé (réception demandée par le site qui reçoit).
    world.owner.site_id = uuid.UUID(world.site2)
    assert world.owner.post("/stock/transfers", json=_body(world, [(0, "1")])).status_code == 201
    assert world.owner.get("/stock/transfers").json()["total"] == 1


def test_expired_subscription_allows_read_only(world: World, owner_db: Session) -> None:
    transfer = _transfer(world, [(0, "1")])
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert world.owner.get(f"/stock/transfers/{transfer['id']}").status_code == 200
    blocked = _validate(world.owner, transfer)
    assert blocked.status_code == 403 and blocked.json()["code"] == "subscription_restricted"


# --- Audit -------------------------------------------------------------------------------------


def test_audit_trail(world: World) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    transfer = _transfer(world, [(0, "1")])
    url = f"/stock/transfers/{transfer['id']}"
    world.owner.put(url, json=_body(world, [(0, "3")]))
    _validate(world.owner, transfer)
    world.owner.post(f"{url}/cancel", json={"reason": "Erreur de saisie"})
    items = world.owner.get("/audit-logs", params={"limit": 50}).json()["items"]
    events = {i["action"]: i for i in items if i["action"].startswith("stock_transfer.")}
    assert set(events) == {
        "stock_transfer.created",
        "stock_transfer.updated",
        "stock_transfer.validated",
        "stock_transfer.cancelled",
    }
    created = events["stock_transfer.created"]["data"]
    assert created["number"] == "TRF-000001"
    assert (created["source_site_id"], created["destination_site_id"]) == (world.site, world.site2)
    assert created["lines"] == [
        {"article_id": world.articles[0], "reference": "A-0", "quantity": "1.000"}
    ]
    updated = events["stock_transfer.updated"]["data"]
    assert updated["before"]["lines"][0]["quantity"] == "1.000"
    assert updated["after"]["lines"][0]["quantity"] == "3.000"
    validated = events["stock_transfer.validated"]["data"]
    assert (validated["previous_status"], validated["status"]) == ("DRAFT", "VALIDATED")
    assert validated["total"] == "300.00"
    cancelled = events["stock_transfer.cancelled"]["data"]
    assert (cancelled["previous_status"], cancelled["status"]) == ("VALIDATED", "CANCELLED")
    assert cancelled["reason"] == "Erreur de saisie" and cancelled["stock_restored"] is True
    assert all(
        e["entity_type"] == "stock_transfer" and e["site_id"] == world.site and e["user_name"]
        for e in events.values()
    )


# --- Multi-tenant ----------------------------------------------------------------------------


def test_isolation_between_tenants_api(world: World, provision: Any, api_for: Any) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    a_transfer = _transfer(world, [(0, "1")])
    provision("beta", profile="quincaillerie", plan="ENTREPRISE")
    beta: Api = api_for("owner@beta.example.com")
    assert beta.get("/stock/transfers").json()["total"] == 0
    url = f"/stock/transfers/{a_transfer['id']}"
    for response in (
        beta.get(url),
        beta.put(url, json=_body(world, [(0, "9")])),
        _validate(beta, a_transfer),
        beta.post(f"{url}/cancel", json={"reason": "Piratage"}),
    ):
        assert response.status_code == 404 and response.json()["code"] == "stock_transfer_not_found"
    # Sites d'un autre tenant : jamais source ni destination.
    beta_site = beta.get("/sites").json()[0]["id"]
    for source, destination in ((beta_site, world.site2), (world.site, beta_site)):
        response = beta.post(
            "/stock/transfers",
            json={
                "source_site_id": source,
                "destination_site_id": destination,
                "lines": [{"article_id": world.articles[0], "quantity": "1"}],
            },
        )
        assert response.status_code == 403 and response.json()["code"] == "site_access_denied"
    assert world.owner.get(url).json()["status"] == "DRAFT"


def test_isolation_with_app_role_and_rls(
    world: World, provision: Any, app_engine: Engine, owner_db: Session
) -> None:
    transfer = _transfer(world, [(0, "1")])
    b = provision("beta")
    tenant_a = owner_db.execute(text("SELECT tenant_id FROM stock_transfers")).scalar_one()
    with create_session_factory(app_engine)() as db:
        assert db.execute(text("SELECT count(*) FROM stock_transfers")).scalar_one() == 0
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.execute(text("SELECT count(*) FROM stock_transfers")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM stock_transfer_lines")).scalar_one() == 0
        updated = db.execute(
            text("UPDATE stock_transfers SET comment = 'x' WHERE id = :id"),
            {"id": transfer["id"]},
        )
        assert updated.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO stock_transfers (id, tenant_id, number, source_site_id, "
                    "destination_site_id, status, operation_date) VALUES (:id, :tenant, "
                    "'TRF-X', :a, :b, 'DRAFT', current_date)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_a, "a": world.site, "b": world.site2},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM stock_transfers"))  # jamais de suppression physique
    # Contraintes en base, même en contournant l'application (session propriétaire) :
    # site d'un autre tenant (FK composite) et source = destination (CHECK).
    for source, destination in ((world.site, str(b.site_id)), (world.site, world.site)):
        with pytest.raises(IntegrityError):
            owner_db.execute(
                text(
                    "INSERT INTO stock_transfers (id, tenant_id, number, source_site_id, "
                    "destination_site_id, status, operation_date) VALUES (:id, :tenant, "
                    ":number, :a, :b, 'DRAFT', current_date)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "number": f"TRF-{uuid.uuid4().hex[:6]}",
                    "a": source,
                    "b": destination,
                },
            )
        owner_db.rollback()
