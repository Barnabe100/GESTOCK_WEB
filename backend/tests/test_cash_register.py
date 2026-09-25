"""Caisse (Phase 2.9, ADR-0022) : caisses d'un site, sessions (ouverture, fond initial, une
seule session ouverte), encaissements espèces des ventes (paiement CASH → mouvement de caisse
dans la même transaction, aucun mouvement pour les autres moyens, caisse ouverte exigée),
entrées et sorties manuelles, journal et solde, clôture (comptage, écart), session fermée
immuable, annulation d'un paiement espèces, idempotence, concurrence, RBAC, sites,
abonnement, module, audit, isolation API et SQL (RLS)."""

import threading
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

# --- Aides ---------------------------------------------------------------------------------------


@pytest.fixture
def priced(world: World, owner_db: Session) -> World:
    """Article 0 vendu 10 000 ; 200 en stock sur le site principal, 100 au dépôt. Aucune
    caisse."""
    owner_db.execute(
        text("UPDATE catalog_articles SET sale_price = 10000 WHERE id = :id"),
        {"id": world.articles[0]},
    )
    owner_db.commit()
    sh.validated_entry(world, [(0, "200", "6000")])
    sh.validated_entry(world, [(0, "100", "6000")], site_id=world.site2)
    return world


def _register(
    w: World, name: str = "Caisse principale", site: str | None = None, api: Api | None = None
) -> dict[str, Any]:
    response = (api or w.owner).post(
        "/cash/registers", json={"site_id": site or w.site, "name": name}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _open(api: Api, register: dict[str, Any], opening_float: str = "0") -> Any:
    return api.post(
        "/cash/sessions",
        json={"cash_register_id": register["id"], "opening_float": opening_float},
    )


def _opened(api: Api, register: dict[str, Any], opening_float: str = "0") -> dict[str, Any]:
    response = _open(api, register, opening_float)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _sale(
    w: World, amount: int, site: str | None = None, validate: bool = True, **extra: Any
) -> dict[str, Any]:
    created = w.owner.post(
        "/sales",
        json={
            "site_id": site or w.site,
            "lines": [{"article_id": w.articles[0], "quantity": str(amount // 10000)}],
            **extra,
        },
    )
    assert created.status_code == 201, created.text
    sale = dict(created.json())
    if validate:
        validated = w.owner.post(f"/sales/{sale['id']}/validate")
        assert validated.status_code == 200, validated.text
        sale = dict(validated.json())
    return sale


def _pay(api: Api, sale: dict[str, Any], amount: str, method: str = "CASH", **extra: Any) -> Any:
    return api.post(
        f"/sales/{sale['id']}/payments", json={"amount": amount, "method": method, **extra}
    )


def _paid(api: Api, sale: dict[str, Any], amount: str, method: str = "CASH", **extra: Any):
    response = _pay(api, sale, amount, method, **extra)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _move(
    api: Api,
    session: dict[str, Any],
    movement_type: str,
    amount: str,
    category: str = "OTHER",
    reason: str = "Motif de test",
    **extra: Any,
) -> Any:
    return api.post(
        f"/cash/sessions/{session['id']}/movements",
        json={
            "movement_type": movement_type,
            "amount": amount,
            "category": category,
            "reason": reason,
            **extra,
        },
    )


def _session(api: Api, session: dict[str, Any]) -> dict[str, Any]:
    response = api.get(f"/cash/sessions/{session['id']}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _journal(api: Api, session: dict[str, Any], **params: Any) -> list[dict[str, Any]]:
    response = api.get(f"/cash/sessions/{session['id']}/movements", params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def _close(api: Api, session: dict[str, Any], counted: str, **extra: Any) -> Any:
    return api.post(
        f"/cash/sessions/{session['id']}/close", json={"counted_balance": counted, **extra}
    )


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


def _run_concurrently(app: Any, token: str, calls: list[tuple[str, Any]]) -> list[int]:
    """Requêtes simultanées (barrière) ; statuts dans l'ordre des appels."""
    barrier = threading.Barrier(len(calls))
    statuses: list[int] = [0] * len(calls)

    def run(index: int, path: str, body: Any) -> None:
        with TestClient(app) as client:
            api = Api(client, token)
            barrier.wait()
            statuses[index] = api.post(path, json=body).status_code

    threads = [
        threading.Thread(target=run, args=(i, path, body)) for i, (path, body) in enumerate(calls)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return statuses


def _count(owner_db: Session, sql: str) -> int:
    return sh.count(owner_db, sql)


# --- Caisses --------------------------------------------------------------------------------------


def test_register_lifecycle(priced: World) -> None:
    main = _register(priced)
    assert main["code"] == "CAI-001" and main["is_active"] is True
    assert main["site_id"] == priced.site and main["site_name"]
    assert main["current_session"] is None
    depot = _register(priced, "Caisse dépôt", priced.site2)
    assert depot["code"] == "CAI-002"
    updated = priced.owner.patch(
        f"/cash/registers/{main['id']}",
        json={"name": "Caisse 1", "description": "Comptoir", "site_id": priced.site2},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Caisse 1" and updated.json()["site_id"] == priced.site
    listed = priced.owner.get("/cash/registers", params={"site_id": priced.site2}).json()
    assert [r["code"] for r in listed["items"]] == ["CAI-002"]
    assert priced.owner.get("/cash/registers", params={"search": "caisse 1"}).json()["total"] == 1
    # Désactivée : consultable, mais ne peut pas être ouverte.
    off = priced.owner.post(f"/cash/registers/{main['id']}/deactivate")
    assert off.status_code == 200 and off.json()["is_active"] is False
    refused = _open(priced.owner, main)
    assert refused.status_code == 409 and refused.json()["code"] == "cash_register_inactive"
    inactive = priced.owner.get("/cash/registers", params={"status": "inactive"}).json()
    assert [r["code"] for r in inactive["items"]] == ["CAI-001"]
    assert priced.owner.post(f"/cash/registers/{main['id']}/activate").status_code == 200
    # Une caisse dont une session est ouverte ne peut pas être désactivée.
    _opened(priced.owner, main)
    busy = priced.owner.post(f"/cash/registers/{main['id']}/deactivate")
    assert busy.status_code == 409 and busy.json()["code"] == "cash_register_has_open_session"
    # Aucune suppression.
    assert (
        priced.owner.client.delete(
            f"/api/v1/cash/registers/{main['id']}", headers=priced.owner._headers()
        ).status_code
        == 405
    )


def test_open_session_with_opening_float(priced: World) -> None:
    register = _register(priced)
    session = _opened(priced.owner, register, "100000")
    assert session["number"] == "SES-000001" and session["status"] == "OPEN"
    assert session["opening_float"] == "100000.00"
    assert session["theoretical_balance"] == "100000.00" and session["cash_in_total"] == "0.00"
    assert session["opened_by_name"] and session["opened_at"]
    journal = _journal(priced.owner, session)
    assert [(m["movement_type"], m["amount"], m["balance_after"]) for m in journal] == [
        ("OPENING_FLOAT", "100000.00", "100000.00")
    ]
    current = priced.owner.get(f"/cash/registers/{register['id']}").json()["current_session"]
    assert current["id"] == session["id"] and current["theoretical_balance"] == "100000.00"
    # Une seule session ouverte par caisse.
    again = _open(priced.owner, register, "5000")
    assert again.status_code == 409 and again.json()["code"] == "cash_session_already_open"
    assert again.json()["session_number"] == "SES-000001"
    # Fond initial nul : aucune écriture ; négatif ou mal formé : refusé.
    other = _register(priced, "Caisse 2")
    assert _journal(priced.owner, _opened(priced.owner, other, "0")) == []
    third = _register(priced, "Caisse 3")
    for bad in ("-1", "10.001", "abc"):
        assert _open(priced.owner, third, bad).status_code == 422


# --- Encaissements des ventes ---------------------------------------------------------------------


def test_cash_payment_creates_a_linked_cash_movement(priced: World, owner_db: Session) -> None:
    session = _opened(priced.owner, _register(priced), "100000")
    customer = priced.owner.post(
        "/customers", json={"customer_type": "INDIVIDUAL", "name": "Awa Traoré"}
    ).json()
    sale = _sale(priced, 100000, customer_id=customer["id"])
    payment = _paid(priced.owner, sale, "100000")
    journal = _journal(priced.owner, session)
    cash_in = [m for m in journal if m["movement_type"] == "SALE_CASH_IN"]
    assert len(cash_in) == 1
    movement = cash_in[0]
    assert movement["amount"] == "100000.00" and movement["signed_amount"] == "100000.00"
    assert movement["payment_id"] == payment["id"] and movement["reference"] == payment["number"]
    assert movement["source_type"] == "sale" and movement["source_id"] == sale["id"]
    assert movement["source_number"] == sale["number"]
    assert movement["balance_after"] == "200000.00"
    assert _session(priced.owner, session)["theoretical_balance"] == "200000.00"
    # Traçabilité mouvement → paiement → vente → client, sans duplication.
    traced = owner_db.execute(
        text(
            "SELECT s.customer_id FROM cash_movements m JOIN payments p ON p.id = m.payment_id "
            "JOIN sales s ON s.id = p.sale_id WHERE m.id = :id"
        ),
        {"id": movement["id"]},
    ).scalar_one()
    assert str(traced) == customer["id"]


def test_electronic_payments_never_touch_the_cash(priced: World) -> None:
    # Aucune caisse ouverte : les moyens électroniques restent acceptés.
    sale = _sale(priced, 100000)
    for method in ("MOBILE_MONEY", "CARD", "BANK_TRANSFER", "OTHER"):
        _paid(priced.owner, sale, "10000", method)
    session = _opened(priced.owner, _register(priced), "0")
    _paid(priced.owner, sale, "10000", "MOBILE_MONEY")
    assert _journal(priced.owner, session) == []
    assert priced.owner.get("/cash/movements").json()["total"] == 0


def test_cash_payment_requires_an_open_session(priced: World, owner_db: Session) -> None:
    sale = _sale(priced, 100000)
    refused = _pay(priced.owner, sale, "10000")
    assert refused.status_code == 422 and refused.json()["code"] == "cash_session_required"
    # Caisse ouverte sur un AUTRE site seulement : refus (jamais une caisse arbitraire).
    depot = _opened(priced.owner, _register(priced, "Dépôt", priced.site2))
    assert _pay(priced.owner, sale, "10000").json()["code"] == "cash_session_required"
    wrong_site = _pay(priced.owner, sale, "10000", cash_register_id=depot["cash_register_id"])
    assert wrong_site.json()["code"] == "cash_session_required"
    # Session clôturée : refus.
    main = _register(priced)
    session = _opened(priced.owner, main)
    assert _close(priced.owner, session, "0").status_code == 200
    assert _pay(priced.owner, sale, "10000").json()["code"] == "cash_session_required"
    # Rien n'a été créé : ni paiement, ni mouvement.
    assert _count(owner_db, "SELECT count(*) FROM payments") == 0
    assert _count(owner_db, "SELECT count(*) FROM cash_movements") == 0


def test_cash_register_choice(priced: World, client: TestClient) -> None:
    first = _register(priced, "Caisse 1")
    second = _register(priced, "Caisse 2")
    s1 = _opened(priced.owner, first)
    s2 = _opened(priced.owner, second)
    sale = _sale(priced, 100000)
    # Deux sessions ouvertes par le même utilisateur : la caisse doit être choisie.
    ambiguous = _pay(priced.owner, sale, "10000")
    assert ambiguous.status_code == 422 and ambiguous.json()["code"] == "cash_register_required"
    assert set(ambiguous.json()["cash_register_ids"]) == {first["id"], second["id"]}
    _paid(priced.owner, sale, "10000", cash_register_id=second["id"])
    assert len(_journal(priced.owner, s2)) == 1 and _journal(priced.owner, s1) == []
    # Un vendeur qui a ouvert sa propre caisse y encaisse par défaut.
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    assert _close(priced.owner, s1, "0").status_code == 200
    own = _opened(seller, first)
    _paid(seller, sale, "20000")
    assert [m["amount"] for m in _journal(priced.owner, own)] == ["20000.00"]
    assert [m["created_by_name"] for m in _journal(priced.owner, own)] == ["vendeur@example.com"]


def test_immediate_cash_payment_at_validation(priced: World, owner_db: Session) -> None:
    draft = _sale(priced, 50000, validate=False)
    body = {"payments": [{"amount": "50000", "method": "CASH"}]}
    refused = priced.owner.post(f"/sales/{draft['id']}/validate", json=body)
    assert refused.status_code == 422 and refused.json()["code"] == "cash_session_required"
    # Tout ou rien : vente brouillon, stock et paiements inchangés.
    assert priced.owner.get(f"/sales/{draft['id']}").json()["status"] == "DRAFT"
    assert sh.level(owner_db, priced, 0)[0] == "200.000"
    assert _count(owner_db, "SELECT count(*) FROM payments") == 0
    # Paiement électronique à la validation : aucune caisse requise.
    mobile = _sale(priced, 30000, validate=False)
    ok = priced.owner.post(
        f"/sales/{mobile['id']}/validate",
        json={"payments": [{"amount": "30000", "method": "MOBILE_MONEY"}]},
    )
    assert ok.status_code == 200 and ok.json()["payment_status"] == "PAID"
    session = _opened(priced.owner, _register(priced))
    validated = priced.owner.post(f"/sales/{draft['id']}/validate", json=body)
    assert validated.status_code == 200 and validated.json()["payment_status"] == "PAID"
    assert [m["source_number"] for m in _journal(priced.owner, session)] == [draft["number"]]


# --- Entrées, sorties, journal, solde -------------------------------------------------------------


def test_manual_movements_journal_and_balance(priced: World) -> None:
    session = _opened(priced.owner, _register(priced), "100000")
    first = _sale(priced, 50000)
    second = _sale(priced, 80000)
    _paid(priced.owner, first, "50000")
    _paid(priced.owner, second, "75000")
    assert (
        _move(priced.owner, session, "MANUAL_CASH_OUT", "20000", "EXPENSE", "Carburant").status_code
        == 201
    )
    assert (
        _move(
            priced.owner, session, "MANUAL_CASH_OUT", "50000", "BANK_DEPOSIT", "Remise en banque"
        ).status_code
        == 201
    )
    entry = _move(
        priced.owner,
        session,
        "MANUAL_CASH_IN",
        "10000",
        "CASH_ADDITION",
        "Apport de monnaie",
        reference="BON-12",
    )
    assert entry.status_code == 201
    created = entry.json()
    assert created["category"] == "CASH_ADDITION" and created["reference"] == "BON-12"
    assert created["balance_after"] == "165000.00" and created["created_by_name"]
    detail = _session(priced.owner, session)
    # 100 000 + 50 000 + 75 000 − 20 000 − 50 000 + 10 000
    assert detail["theoretical_balance"] == "165000.00"
    assert detail["cash_in_total"] == "135000.00" and detail["cash_out_total"] == "70000.00"
    assert detail["movement_count"] == 6
    journal = _journal(priced.owner, session, sort="occurred_at")
    assert [m["movement_type"] for m in journal] == [
        "OPENING_FLOAT",
        "SALE_CASH_IN",
        "SALE_CASH_IN",
        "MANUAL_CASH_OUT",
        "MANUAL_CASH_OUT",
        "MANUAL_CASH_IN",
    ]
    assert [m["balance_after"] for m in journal] == [
        "100000.00",
        "150000.00",
        "225000.00",
        "205000.00",
        "155000.00",
        "165000.00",
    ]
    assert journal[3]["signed_amount"] == "-20000.00"
    # Filtres : type, recherche (référence, vente, motif), montant, utilisateur.
    outs = _journal(priced.owner, session, movement_type="MANUAL_CASH_OUT")
    assert {m["reason"] for m in outs} == {"Carburant", "Remise en banque"}
    assert [m["reason"] for m in _journal(priced.owner, session, search="banque")] == [
        "Remise en banque"
    ]
    assert [
        m["source_number"] for m in _journal(priced.owner, session, search=first["number"])
    ] == [first["number"]]
    assert len(_journal(priced.owner, session, min_amount="75000")) == 2
    me = priced.owner.get("/me").json()["user"]["id"]
    assert len(_journal(priced.owner, session, created_by=me)) == 6
    # Journal global paginé côté serveur (tous les mouvements visibles).
    page = priced.owner.get("/cash/movements", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 6 and len(page["items"]) == 2
    assert page["items"][0]["cash_register_code"] == "CAI-001"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"movement_type": "MANUAL_CASH_IN", "amount": "0"}, None),
        ({"movement_type": "MANUAL_CASH_IN", "amount": "-5"}, None),
        ({"movement_type": "MANUAL_CASH_IN", "amount": "10", "reason": "x"}, None),
        (
            {"movement_type": "MANUAL_CASH_IN", "category": "EXPENSE"},
            "cash_movement_category_invalid",
        ),
        (
            {"movement_type": "MANUAL_CASH_OUT", "category": "CASH_ADDITION"},
            "cash_movement_category_invalid",
        ),
        ({"movement_type": "SALE_CASH_IN"}, "cash_movement_type_invalid"),
        ({"movement_type": "OPENING_FLOAT"}, "cash_movement_type_invalid"),
    ],
)
def test_manual_movement_validation(priced: World, body: dict[str, Any], code: str | None) -> None:
    session = _opened(priced.owner, _register(priced), "1000")
    payload = {"amount": "10", "category": "OTHER", "reason": "Motif valable", **body}
    response = priced.owner.post(f"/cash/sessions/{session['id']}/movements", json=payload)
    assert response.status_code == 422
    if code:
        assert response.json()["code"] == code


def test_cash_out_never_makes_the_balance_negative_and_idempotency(priced: World) -> None:
    session = _opened(priced.owner, _register(priced), "30000")
    too_much = _move(priced.owner, session, "MANUAL_CASH_OUT", "30000.01", "EXPENSE")
    assert too_much.status_code == 422 and too_much.json()["code"] == "cash_insufficient_balance"
    assert too_much.json()["balance"] == "30000.00"
    # Exactement le solde : accepté (solde 0).
    key = str(uuid.uuid4())
    out = _move(
        priced.owner, session, "MANUAL_CASH_OUT", "30000", "WITHDRAWAL", idempotency_key=key
    )
    assert out.status_code == 201
    replay = _move(
        priced.owner, session, "MANUAL_CASH_OUT", "30000", "WITHDRAWAL", idempotency_key=key
    )
    assert replay.status_code == 200 and replay.json()["id"] == out.json()["id"]
    reused = _move(priced.owner, session, "MANUAL_CASH_IN", "5", "OTHER", idempotency_key=key)
    assert reused.status_code == 409 and reused.json()["code"] == "idempotency_key_reused"
    assert _session(priced.owner, session)["theoretical_balance"] == "0.00"
    assert len(_journal(priced.owner, session)) == 2


# --- Clôture --------------------------------------------------------------------------------------


def test_close_session_with_variance(priced: World, owner_db: Session) -> None:
    register = _register(priced)
    session = _opened(priced.owner, register, "100000")
    _paid(priced.owner, _sale(priced, 50000), "50000")
    _paid(priced.owner, _sale(priced, 80000), "75000")
    _move(priced.owner, session, "MANUAL_CASH_OUT", "20000", "EXPENSE")
    _move(priced.owner, session, "MANUAL_CASH_OUT", "50000", "BANK_DEPOSIT")
    movements = len(_journal(priced.owner, session))
    # Un écart envoyé par le client est ignoré : le serveur le calcule.
    closed = _close(priced.owner, session, "153500", note="Pièce manquante", variance="0")
    assert closed.status_code == 200, closed.text
    body = closed.json()
    assert body["status"] == "CLOSED" and body["closed_at"] and body["closed_by_name"]
    assert body["theoretical_balance"] == "155000.00" and body["counted_balance"] == "153500.00"
    assert body["variance"] == "-1500.00" and body["closing_note"] == "Pièce manquante"
    # Aucun mouvement d'écart créé automatiquement.
    assert len(_journal(priced.owner, session)) == movements
    # Session fermée : consultable, immuable ; plus aucun mouvement, paiement ni clôture.
    assert _session(priced.owner, session)["variance"] == "-1500.00"
    late = _move(priced.owner, session, "MANUAL_CASH_IN", "1000", "OTHER")
    assert late.status_code == 409 and late.json()["code"] == "cash_session_closed"
    assert _pay(priced.owner, _sale(priced, 10000), "10000").json()["code"] == (
        "cash_session_required"
    )
    twice = _close(priced.owner, session, "155000")
    assert twice.status_code == 409 and twice.json()["code"] == "cash_session_closed"
    assert _session(priced.owner, session)["counted_balance"] == "153500.00"
    # Nouvelle session possible ; excédent : écart positif.
    second = _opened(priced.owner, register, "10000")
    assert second["number"] == "SES-000002"
    excess = _close(priced.owner, second, "10500").json()
    assert excess["variance"] == "500.00"
    assert _close(priced.owner, _opened(priced.owner, register), "-1").status_code == 422
    # Instantané cohérent avec les mouvements (écart = compté − théorique, CHECK en base).
    total = owner_db.execute(
        text(
            "SELECT sum(CASE WHEN movement_type IN ('MANUAL_CASH_OUT', 'SALE_CASH_REVERSAL') "
            "THEN -amount ELSE amount END) FROM cash_movements WHERE cash_session_id = :id"
        ),
        {"id": session["id"]},
    ).scalar_one()
    assert total == Decimal("155000.00")


def test_cancel_cash_payment_reverses_in_the_same_open_session(priced: World) -> None:
    session = _opened(priced.owner, _register(priced), "0")
    sale = _sale(priced, 50000)
    payment = _paid(priced.owner, sale, "30000")
    cancelled = priced.owner.post(
        f"/sales/{sale['id']}/payments/{payment['id']}/cancel", json={"reason": "Erreur de caisse"}
    )
    assert cancelled.status_code == 200
    journal = _journal(priced.owner, session, sort="occurred_at")
    assert [(m["movement_type"], m["signed_amount"]) for m in journal] == [
        ("SALE_CASH_IN", "30000.00"),
        ("SALE_CASH_REVERSAL", "-30000.00"),
    ]
    assert journal[1]["payment_id"] == payment["id"] and journal[1]["reason"] == "Erreur de caisse"
    assert _session(priced.owner, session)["theoretical_balance"] == "0.00"
    # Paiement espèces d'une session clôturée : annulation refusée (historique immuable).
    other = _paid(priced.owner, sale, "20000")
    _close(priced.owner, session, "20000")
    refused = priced.owner.post(
        f"/sales/{sale['id']}/payments/{other['id']}/cancel", json={"reason": "Trop tard"}
    )
    assert refused.status_code == 409 and refused.json()["code"] == "cash_session_closed"
    status = priced.owner.get(f"/sales/{sale['id']}/payments/{other['id']}").json()["status"]
    assert status == "COMPLETED"
    # Paiement électronique : annulation sans effet sur la caisse.
    mobile = _paid(priced.owner, sale, "30000", "MOBILE_MONEY")
    assert (
        priced.owner.post(
            f"/sales/{sale['id']}/payments/{mobile['id']}/cancel", json={"reason": "Erreur"}
        ).status_code
        == 200
    )


def test_payment_idempotency_creates_a_single_cash_movement(priced: World, app: Any) -> None:
    session = _opened(priced.owner, _register(priced))
    sale = _sale(priced, 100000)
    key = str(uuid.uuid4())
    first = _pay(priced.owner, sale, "40000", idempotency_key=key)
    replay = _pay(priced.owner, sale, "40000", idempotency_key=key)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.json()["id"] == first.json()["id"]
    other = str(uuid.uuid4())
    path = f"/sales/{sale['id']}/payments"
    body = {"amount": "10000", "method": "CASH", "idempotency_key": other}
    assert sorted(_run_concurrently(app, priced.owner.token, [(path, body), (path, body)])) == [
        200,
        201,
    ]
    journal = _journal(priced.owner, session)
    assert sorted(m["amount"] for m in journal) == ["10000.00", "40000.00"]
    assert len(priced.owner.get(f"/sales/{sale['id']}/payments").json()["items"]) == 2


# --- Concurrence ----------------------------------------------------------------------------------


def test_concurrent_openings_leave_a_single_open_session(
    priced: World, app: Any, owner_db: Session
) -> None:
    register = _register(priced)
    body = {"cash_register_id": register["id"], "opening_float": "1000"}
    statuses = _run_concurrently(
        app, priced.owner.token, [("/cash/sessions", body), ("/cash/sessions", body)]
    )
    assert sorted(statuses) == [201, 409]
    assert _count(owner_db, "SELECT count(*) FROM cash_sessions WHERE status = 'OPEN'") == 1
    assert _count(owner_db, "SELECT count(*) FROM cash_movements") == 1


def test_concurrent_cash_outs_never_overdraw(priced: World, app: Any) -> None:
    session = _opened(priced.owner, _register(priced), "100000")
    path = f"/cash/sessions/{session['id']}/movements"
    body = {
        "movement_type": "MANUAL_CASH_OUT",
        "amount": "60000",
        "category": "WITHDRAWAL",
        "reason": "Retrait",
    }
    assert sorted(_run_concurrently(app, priced.owner.token, [(path, body), (path, body)])) == [
        201,
        422,
    ]
    assert _session(priced.owner, session)["theoretical_balance"] == "40000.00"


def test_cash_sale_and_cash_out_are_serialized(priced: World, app: Any) -> None:
    session = _opened(priced.owner, _register(priced), "50000")
    sale = _sale(priced, 100000)
    out = {
        "movement_type": "MANUAL_CASH_OUT",
        "amount": "120000",
        "category": "BANK_DEPOSIT",
        "reason": "Remise en banque",
    }
    pay_status, out_status = _run_concurrently(
        app,
        priced.owner.token,
        [
            (f"/sales/{sale['id']}/payments", {"amount": "100000", "method": "CASH"}),
            (f"/cash/sessions/{session['id']}/movements", out),
        ],
    )
    assert pay_status == 201
    balance = Decimal(_session(priced.owner, session)["theoretical_balance"])
    if out_status == 201:  # encaissement d'abord : 150 000 − 120 000
        assert balance == Decimal("30000")
    else:  # sortie d'abord : refusée (50 000 < 120 000)
        assert out_status == 422 and balance == Decimal("150000")


def test_cash_in_and_cash_out_concurrently(priced: World, app: Any) -> None:
    session = _opened(priced.owner, _register(priced), "10000")
    path = f"/cash/sessions/{session['id']}/movements"
    cash_in = {
        "movement_type": "MANUAL_CASH_IN",
        "amount": "50000",
        "category": "CASH_ADDITION",
        "reason": "Apport",
    }
    cash_out = {**cash_in, "movement_type": "MANUAL_CASH_OUT", "category": "EXPENSE"}
    in_status, out_status = _run_concurrently(
        app, priced.owner.token, [(path, cash_in), (path, cash_out)]
    )
    assert in_status == 201
    balance = _session(priced.owner, session)["theoretical_balance"]
    assert (out_status, balance) in {(201, "10000.00"), (422, "60000.00")}


def test_cash_payment_and_closing_are_serialized(priced: World, app: Any) -> None:
    session = _opened(priced.owner, _register(priced), "0")
    sale = _sale(priced, 100000)
    pay_status, close_status = _run_concurrently(
        app,
        priced.owner.token,
        [
            (f"/sales/{sale['id']}/payments", {"amount": "40000", "method": "CASH"}),
            (f"/cash/sessions/{session['id']}/close", {"counted_balance": "40000"}),
        ],
    )
    assert close_status == 200
    closed = _session(priced.owner, session)
    journal = _journal(priced.owner, session)
    # Le solde figé contient exactement les mouvements de la session, jamais un mouvement
    # arrivé après la clôture.
    assert Decimal(closed["theoretical_balance"]) == sum(
        (Decimal(m["signed_amount"]) for m in journal), Decimal("0")
    )
    if pay_status == 201:  # paiement d'abord : compté dans la session
        assert closed["theoretical_balance"] == "40000.00" and closed["variance"] == "0.00"
    else:  # clôture d'abord : paiement refusé, rien d'encaissé
        assert pay_status == 422 and journal == []
        assert priced.owner.get(f"/sales/{sale['id']}").json()["paid_amount"] == "0.00"


def test_concurrent_closings(priced: World, app: Any) -> None:
    session = _opened(priced.owner, _register(priced), "5000")
    path = f"/cash/sessions/{session['id']}/close"
    statuses = _run_concurrently(
        app,
        priced.owner.token,
        [(path, {"counted_balance": "5000"}), (path, {"counted_balance": "4000"})],
    )
    assert sorted(statuses) == [200, 409]
    closed = _session(priced.owner, session)
    assert closed["counted_balance"] in {"5000.00", "4000.00"}
    assert Decimal(closed["variance"]) == Decimal(closed["counted_balance"]) - Decimal("5000")


# --- Sécurité : RBAC, sites, abonnement, module ---------------------------------------------------


def test_permissions_by_base_role(priced: World, client: TestClient) -> None:
    register = _register(priced)
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    viewer = sh.member(priced, client, "consultant@example.com", "viewer", all_sites=True)
    # Vendeur : consulte, ouvre, encaisse et clôture ; ni gestion des caisses, ni mouvements.
    assert seller.get("/cash/registers").json()["total"] == 1
    denied = seller.post("/cash/registers", json={"site_id": priced.site, "name": "X"})
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    session = _opened(seller, register, "1000")
    manual = _move(seller, session, "MANUAL_CASH_IN", "10", "OTHER")
    assert manual.status_code == 403 and manual.json()["code"] == "permission_denied"
    _paid(seller, _sale(priced, 10000), "10000")
    # Gestionnaire : gestion opérationnelle complète.
    assert _move(manager, session, "MANUAL_CASH_OUT", "500", "EXPENSE").status_code == 201
    assert _register(priced, "Caisse gestionnaire", api=manager)["code"] == "CAI-002"
    # Consultant : consultation seule.
    assert len(_journal(viewer, session)) == 3
    assert _close(viewer, session, "0").status_code == 403
    assert _open(viewer, _register(priced, "Caisse 3")).status_code == 403
    assert _close(seller, session, "10500").json()["variance"] == "0.00"


def test_custom_role_permissions(priced: World, client: TestClient) -> None:
    register = _register(priced)
    session = _opened(priced.owner, register, "1000")
    treasurer = _custom_member(
        priced,
        client,
        "tresorier@example.com",
        ["cash_register.session.view", "cash_register.movement.create"],
    )
    assert _move(treasurer, session, "MANUAL_CASH_OUT", "100", "BANK_DEPOSIT").status_code == 201
    assert _close(treasurer, session, "900").status_code == 403
    assert treasurer.get("/cash/registers").status_code == 403
    reader = _custom_member(priced, client, "lecteur@example.com", ["sales.sale.view"])
    for path in ("/cash/registers", "/cash/sessions", "/cash/movements"):
        assert reader.get(path).json()["code"] == "permission_denied"


def test_site_scope(priced: World, client: TestClient) -> None:
    depot = _register(priced, "Caisse dépôt", priced.site2)
    depot_session = _opened(priced.owner, depot, "1000")
    main = _register(priced)
    shop = sh.member(priced, client, "boutique@example.com", "manager", site_ids=[priced.site])
    # Un membre de la boutique ne voit ni n'utilise la caisse du dépôt.
    assert [r["code"] for r in shop.get("/cash/registers").json()["items"]] == [main["code"]]
    assert shop.get("/cash/sessions").json()["total"] == 0
    assert shop.get("/cash/movements").json()["total"] == 0
    for response in (
        shop.get(f"/cash/registers/{depot['id']}"),
        _open(shop, depot),
        shop.get(f"/cash/sessions/{depot_session['id']}"),
        shop.get(f"/cash/sessions/{depot_session['id']}/movements"),
        _move(shop, depot_session, "MANUAL_CASH_OUT", "100", "EXPENSE"),
        _close(shop, depot_session, "1000"),
    ):
        assert response.status_code == 404, response.text
    created = shop.post("/cash/registers", json={"site_id": priced.site2, "name": "Intrus"})
    assert created.status_code == 403 and created.json()["code"] == "site_access_denied"
    # Site sélectionné (X-Site-Id) différent : refus explicite.
    priced.owner.site_id = uuid.UUID(priced.site)
    mismatch = _close(priced.owner, depot_session, "1000")
    assert mismatch.status_code == 403 and mismatch.json()["code"] == "site_mismatch"


def test_expired_subscription_keeps_consultation(priced: World, owner_db: Session) -> None:
    register = _register(priced)
    session = _opened(priced.owner, register, "1000")
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert priced.owner.get("/cash/registers").status_code == 200
    assert len(_journal(priced.owner, session)) == 1
    for response in (
        priced.owner.post("/cash/registers", json={"site_id": priced.site, "name": "X"}),
        _move(priced.owner, session, "MANUAL_CASH_IN", "10", "OTHER"),
        _close(priced.owner, session, "1000"),
    ):
        assert response.status_code == 403
        assert response.json()["code"] == "subscription_restricted"


def test_module_deactivation(priced: World, owner_db: Session) -> None:
    # Les ventes dépendent de la caisse : désactivation refusée tant que les ventes sont actives.
    refused = priced.owner.put("/modules/cash_register", json={"enabled": False})
    assert refused.status_code == 409 and refused.json()["code"] == "module_has_dependents"
    owner_db.execute(
        text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'cash_register'")
    )
    owner_db.commit()
    for path in ("/cash/registers", "/cash/sessions", "/cash/movements"):
        denied = priced.owner.get(path)
        assert denied.status_code == 403 and denied.json()["code"] == "module_unavailable"


def test_audit_trail(priced: World) -> None:
    register = _register(priced)
    priced.owner.patch(f"/cash/registers/{register['id']}", json={"name": "Caisse 1"})
    session = _opened(priced.owner, register, "100000")
    _move(priced.owner, session, "MANUAL_CASH_OUT", "20000", "EXPENSE", "Carburant")
    payment = _paid(priced.owner, _sale(priced, 10000), "10000")
    _close(priced.owner, session, "89000", note="Écart constaté")
    logs = priced.owner.get("/audit-logs", params={"action": "cash_", "limit": 50}).json()
    by_action = {log["action"]: log for log in logs["items"]}
    assert set(by_action) == {
        "cash_register.created",
        "cash_register.updated",
        "cash_session.opened",
        "cash_movement.created",
        "cash_session.closed",
    }
    assert by_action["cash_register.updated"]["data"]["name"] == {
        "before": "Caisse principale",
        "after": "Caisse 1",
    }
    opened = by_action["cash_session.opened"]
    assert opened["data"]["opening_float"] == "100000.00" and opened["site_id"] == priced.site
    assert opened["user_id"]
    moved = by_action["cash_movement.created"]["data"]
    assert moved["reason"] == "Carburant" and moved["amount"] == "20000.00"
    assert moved["balance_after"] == "80000.00"
    closed = by_action["cash_session.closed"]["data"]
    assert (closed["theoretical_balance"], closed["counted_balance"], closed["variance"]) == (
        "90000.00",
        "89000.00",
        "-1000.00",
    )
    completed = priced.owner.get(
        "/audit-logs", params={"action": "payment.completed", "limit": 5}
    ).json()["items"][0]
    assert completed["entity_id"] == payment["id"]
    assert completed["data"]["cash_session_id"] == session["id"]
    # La consultation n'écrit rien.
    before = priced.owner.get("/audit-logs", params={"limit": 1}).json()["total"]
    priced.owner.get("/cash/registers")
    _journal(priced.owner, session)
    assert priced.owner.get("/audit-logs", params={"limit": 1}).json()["total"] == before


# --- Multi-tenant ---------------------------------------------------------------------------------


def test_isolation_between_tenants_api(priced: World, provision: Any, api_for: Any) -> None:
    register = _register(priced)
    session = _opened(priced.owner, register, "1000")
    provision("beta", profile="quincaillerie", plan="ENTREPRISE")
    beta: Api = api_for("owner@beta.example.com")
    for path in ("/cash/registers", "/cash/sessions", "/cash/movements"):
        assert beta.get(path).json()["total"] == 0
    for response in (
        beta.get(f"/cash/registers/{register['id']}"),
        _open(beta, register),
        beta.get(f"/cash/sessions/{session['id']}"),
        _move(beta, session, "MANUAL_CASH_OUT", "100", "EXPENSE"),
        _close(beta, session, "0"),
    ):
        assert response.status_code == 404
    assert _session(priced.owner, session)["status"] == "OPEN"


def test_isolation_with_app_role_and_rls(
    priced: World, provision: Any, app_engine: Engine, owner_db: Session
) -> None:
    register = _register(priced)
    session = _opened(priced.owner, register, "1000")
    b = provision("beta")
    tenant_a = owner_db.execute(text("SELECT tenant_id FROM cash_sessions")).scalar_one()
    tables = ("cash_registers", "cash_sessions", "cash_movements")
    with create_session_factory(app_engine)() as db:
        for table in tables:
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        for table in tables:
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        assert (
            db.execute(
                text("UPDATE cash_sessions SET opening_float = 1 WHERE id = :id"),
                {"id": session["id"]},
            ).rowcount
            == 0
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO cash_registers (id, tenant_id, code, site_id, name, is_active) "
                    "VALUES (:id, :tenant, 'CAI-X', :site, 'Intrus', true)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_a, "site": priced.site},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        assert (
            db.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
            is False
        )
        # Mouvements append-only ; caisses et sessions jamais supprimées.
        for sql in (
            "DELETE FROM cash_movements",
            "UPDATE cash_movements SET amount = 1",
            "DELETE FROM cash_sessions",
            "DELETE FROM cash_registers",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(sql))
            db.rollback()
            set_db_context(db, tenant_id=tenant_a)
    # Contraintes en base (session propriétaire) : deux sessions ouvertes sur la même caisse,
    # écart incohérent, mouvement d'un autre site que sa session.
    insert_session = (
        "INSERT INTO cash_sessions (id, tenant_id, number, cash_register_id, site_id, status, "
        "opening_float, opened_at, closed_at, theoretical_balance, counted_balance, variance) "
        "VALUES (:id, :tenant, :number, :register, :site, :status, 0, now(), :closed, :t, :c, :v)"
    )
    for status, closed, t, c, v in (
        ("OPEN", None, None, None, None),
        ("CLOSED", "2026-09-25", 100, 90, 0),
    ):
        with pytest.raises(IntegrityError):
            owner_db.execute(
                text(insert_session),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "number": f"SES-{uuid.uuid4().hex[:6]}",
                    "register": register["id"],
                    "site": priced.site,
                    "status": status,
                    "closed": closed,
                    "t": t,
                    "c": c,
                    "v": v,
                },
            )
        owner_db.rollback()
    with pytest.raises(IntegrityError):
        owner_db.execute(
            text(
                "INSERT INTO cash_movements (id, tenant_id, cash_session_id, cash_register_id, "
                "site_id, movement_type, amount, occurred_at) VALUES (:id, :tenant, :session, "
                ":register, :site, 'OPENING_FLOAT', 1, now())"
            ),
            {
                "id": uuid.uuid4(),
                "tenant": tenant_a,
                "session": session["id"],
                "register": register["id"],
                "site": priced.site2,
            },
        )
    owner_db.rollback()
