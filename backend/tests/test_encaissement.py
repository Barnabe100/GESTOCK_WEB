"""Lot 1 — encaissement (ADR-0037) : moyens de paiement configurables (entreprise, activation par
site, comportement par TYPE jamais par libellé, instantané sur le paiement), caisse optionnelle
par site (sans caisse : espèces sans session ; désactivation refusée si une session est
ouverte), session = site + poste + utilisateur, montant reçu et monnaie calculés par le serveur
sur la seule partie espèces, paiements mixtes, crédit (client obligatoire, permission,
limite, dépassement autorisé et justifié), numérotation ``VENT-{SITE}-{ANNÉE}-{SÉQUENCE}`` à la
validation (par site et par année, concurrence, anciens numéros conservés, code de site figé),
portée « ses propres ventes », isolation site / tenant, RLS et droits SQL."""

import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.platform.context import get_now
from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

YEAR = datetime.now(UTC).year


@pytest.fixture
def priced(world: World, owner_db: Session) -> World:
    """Article 0 vendu 10 000 ; 200 en stock sur le site principal, 100 au dépôt. Aucune
    caisse activée (caisse optionnelle : désactivée par défaut)."""
    owner_db.execute(
        text("UPDATE catalog_articles SET sale_price = 10000 WHERE id = :id"),
        {"id": world.articles[0]},
    )
    owner_db.commit()
    sh.validated_entry(world, [(0, "200", "6000")])
    sh.validated_entry(world, [(0, "100", "6000")], site_id=world.site2)
    return world


# --- Aides ----------------------------------------------------------------------------------------


def _methods(api: Api, site: str | None = None) -> dict[str, dict[str, Any]]:
    response = api.get("/payment-methods", params={"site_id": site} if site else None)
    assert response.status_code == 200, response.text
    return {m["label"]: m for m in response.json()}


def _method_id(w: World, label: str) -> str:
    return str(_methods(w.owner)[label]["id"])


def _draft(
    w: World, units: int, site: str | None = None, api: Api | None = None, **extra: Any
) -> dict[str, Any]:
    response = (api or w.owner).post(
        "/sales",
        json={
            "site_id": site or w.site,
            "lines": [{"article_id": w.articles[0], "quantity": str(units)}],
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _validate(
    w: World,
    sale: dict[str, Any],
    payments: list[dict[str, Any]],
    api: Api | None = None,
    **kw: Any,
) -> Any:
    return (api or w.owner).post(f"/sales/{sale['id']}/validate", json={"payments": payments, **kw})


def _validated(w: World, sale: dict[str, Any], payments: list[dict[str, Any]], **kw: Any):
    response = _validate(w, sale, payments, **kw)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _payments(api: Api, sale: dict[str, Any]) -> list[dict[str, Any]]:
    response = api.get(f"/sales/{sale['id']}/payments")
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


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


def _site_code(w: World, site: str) -> str:
    return str(next(s["code"] for s in w.owner.get("/sites").json() if s["id"] == site))


def _audit_actions(w: World) -> list[str]:
    items = w.owner.get("/audit-logs", params={"limit": 200}).json()["items"]
    return [str(i["action"]) for i in items]


def _count(owner_db: Session, sql: str, **params: Any) -> int:
    owner_db.expire_all()
    return int(owner_db.execute(text(sql), params).scalar_one())


def _register(api: Api, site: str, name: str = "Poste 1") -> dict[str, Any]:
    response = api.post("/cash/registers", json={"site_id": site, "name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


def _open(api: Api, register: dict[str, Any], opening_float: str = "0") -> dict[str, Any]:
    response = api.post(
        "/cash/sessions",
        json={"cash_register_id": register["id"], "opening_float": opening_float},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- Moyens de paiement configurables -------------------------------------------------------------


def test_default_methods_and_configuration(priced: World) -> None:
    methods = _methods(priced.owner)
    assert {(m["label"], m["kind"]) for m in methods.values()} == {
        ("Espèces", "CASH"),
        ("Mobile Money", "MOBILE_MONEY"),
        ("Carte bancaire", "CARD"),
        ("Virement", "BANK_TRANSFER"),
        ("Autre", "OTHER"),
    }
    assert all(m["integration_mode"] == "MANUAL" and m["is_active"] for m in methods.values())

    created = priced.owner.post(
        "/payment-methods",
        json={"label": "Orange Money", "kind": "MOBILE_MONEY", "reference_required": True},
    )
    assert created.status_code == 201, created.text
    om = created.json()
    assert (om["kind"], om["reference_required"], om["disabled_site_ids"]) == (
        "MOBILE_MONEY",
        True,
        [],
    )
    # Libellé unique (casse ignorée) ; type jamais modifiable ; intégration API pas encore
    # disponible (saisie manuelle seulement, le modèle est prêt).
    duplicate = priced.owner.post(
        "/payment-methods", json={"label": "orange money", "kind": "OTHER"}
    )
    assert duplicate.json()["code"] == "payment_method_label_taken"
    assert (
        priced.owner.patch(f"/payment-methods/{om['id']}", json={"kind": "CASH"}).status_code == 422
    )
    api_mode = priced.owner.post(
        "/payment-methods",
        json={"label": "Wave", "kind": "MOBILE_MONEY", "integration_mode": "API"},
    )
    assert api_mode.json()["code"] == "payment_integration_unavailable"

    renamed = priced.owner.patch(
        f"/payment-methods/{om['id']}", json={"label": "Orange Money BF", "sort_order": 15}
    )
    assert renamed.status_code == 200 and renamed.json()["label"] == "Orange Money BF"
    other = methods["Autre"]
    off = priced.owner.patch(f"/payment-methods/{other['id']}", json={"is_active": False})
    assert off.json()["is_active"] is False
    # Désactivé : jamais supprimé, toujours listé, plus utilisable.
    assert _methods(priced.owner, priced.site)["Autre"]["available"] is False
    sale = _draft(priced, 1, customer_id=sh.credit_customer(priced))
    refused = _validate(priced, sale, [{"amount": "10000", "payment_method_id": other["id"]}])
    assert refused.json()["code"] == "payment_method_unavailable"
    actions = _audit_actions(priced)
    assert {"payment_method.created", "payment_method.updated"} <= set(actions)


def test_activation_per_site(priced: World, owner_db: Session) -> None:
    mobile = _method_id(priced, "Mobile Money")
    off = priced.owner.put(
        f"/payment-methods/{mobile}/sites/{priced.site2}", json={"enabled": False}
    )
    assert off.status_code == 200 and off.json()["disabled_site_ids"] == [priced.site2]
    assert _methods(priced.owner, priced.site2)["Mobile Money"]["available"] is False
    assert _methods(priced.owner, priced.site)["Mobile Money"]["available"] is True

    on_site2 = _draft(priced, 1, site=priced.site2)
    refused = _validate(priced, on_site2, [{"amount": "10000", "payment_method_id": mobile}])
    assert refused.status_code == 422 and refused.json()["code"] == "payment_method_unavailable"
    # Refus : rien n'est écrit (ni stock, ni numéro, ni paiement).
    assert _count(owner_db, "SELECT count(*) FROM payments") == 0
    assert priced.owner.get(f"/sales/{on_site2['id']}").json()["number"] is None

    on_site1 = _draft(priced, 1)
    assert (
        _validated(priced, on_site1, [{"amount": "10000", "payment_method_id": mobile}])[
            "payment_status"
        ]
        == "PAID"
    )
    back = priced.owner.put(
        f"/payment-methods/{mobile}/sites/{priced.site2}", json={"enabled": True}
    )
    assert back.json()["disabled_site_ids"] == []
    assert (
        _validated(priced, on_site2, [{"amount": "10000", "payment_method_id": mobile}])[
            "payment_status"
        ]
        == "PAID"
    )
    actions = _audit_actions(priced)
    assert {"payment_method.site_disabled", "payment_method.site_enabled"} <= set(actions)


def test_behavior_follows_the_type_never_the_label(priced: World) -> None:
    """Un moyen libellé « Espèces bis » mais de type MOBILE_MONEY ne rend jamais la monnaie ;
    un moyen « Caisse Wave » de type CASH est traité comme des espèces."""
    fake = priced.owner.post(
        "/payment-methods", json={"label": "Espèces bis", "kind": "MOBILE_MONEY"}
    ).json()
    cash_like = priced.owner.post(
        "/payment-methods", json={"label": "Caisse Wave", "kind": "CASH"}
    ).json()
    sale = _draft(priced, 2, customer_id=sh.credit_customer(priced))
    no_change = _validate(
        priced, sale, [{"amount_received": "25000", "payment_method_id": fake["id"]}]
    )
    assert no_change.json()["code"] == "change_not_allowed"
    done = _validated(
        priced, sale, [{"amount_received": "25000", "payment_method_id": cash_like["id"]}]
    )
    assert done["payment_status"] == "PAID"
    [payment] = _payments(priced.owner, sale)
    assert (payment["method"], payment["method_label"], payment["amount"]) == (
        "CASH",
        "Caisse Wave",
        "20000.00",
    )
    assert (payment["amount_received"], payment["change_given"]) == ("25000.00", "5000.00")
    # Compatibilité : le type seul n'est accepté que s'il désigne un moyen unique.
    ambiguous = _draft(priced, 1, customer_id=sh.credit_customer(priced))
    response = _validate(priced, ambiguous, [{"amount": "10000", "method": "CASH"}])
    assert response.json()["code"] == "payment_method_required"


def test_payment_snapshot_survives_method_changes(priced: World) -> None:
    mobile = _method_id(priced, "Mobile Money")
    sale = _draft(priced, 1)
    _validated(
        priced, sale, [{"amount": "10000", "payment_method_id": mobile, "reference": "OM-123"}]
    )
    priced.owner.patch(f"/payment-methods/{mobile}", json={"label": "Moov Money"})
    priced.owner.patch(f"/payment-methods/{mobile}", json={"is_active": False})
    [payment] = _payments(priced.owner, sale)
    assert (payment["method_label"], payment["method"], payment["reference"]) == (
        "Mobile Money",
        "MOBILE_MONEY",
        "OM-123",
    )
    assert payment["payment_method_id"] == mobile


def test_reference_required(priced: World) -> None:
    om = priced.owner.post(
        "/payment-methods",
        json={"label": "Orange Money", "kind": "MOBILE_MONEY", "reference_required": True},
    ).json()
    sale = _draft(priced, 1)
    missing = _validate(priced, sale, [{"amount": "10000", "payment_method_id": om["id"]}])
    assert missing.json()["code"] == "payment_reference_required"
    blank = _validate(
        priced, sale, [{"amount": "10000", "payment_method_id": om["id"], "reference": "  "}]
    )
    assert blank.json()["code"] == "payment_reference_required"
    ok = _validated(
        priced, sale, [{"amount": "10000", "payment_method_id": om["id"], "reference": "TX-9"}]
    )
    assert ok["payment_status"] == "PAID"


def test_payment_method_permissions(priced: World, client: TestClient) -> None:
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    # Lecture pour qui encaisse ; configuration réservée à ``sales.payment_method.manage``.
    assert seller.get("/payment-methods").status_code == 200
    for api in (seller, manager):
        assert api.post("/payment-methods", json={"label": "X", "kind": "OTHER"}).status_code == 403
        mobile = _method_id(priced, "Mobile Money")
        assert api.patch(f"/payment-methods/{mobile}", json={"label": "Y"}).status_code == 403
        assert (
            api.put(
                f"/payment-methods/{mobile}/sites/{priced.site}", json={"enabled": False}
            ).status_code
            == 403
        )
    configurator = _custom_member(
        priced, client, "config@example.com", ["sales.payment_method.manage"]
    )
    assert (
        configurator.post(
            "/payment-methods", json={"label": "Wave", "kind": "MOBILE_MONEY"}
        ).status_code
        == 201
    )
    # Administrateur limité au site principal : ne configure pas un autre site.
    local_admin = sh.member(
        priced, client, "admin-boutique@example.com", "administrator", site_ids=[priced.site]
    )
    mobile = _method_id(priced, "Mobile Money")
    assert (
        local_admin.put(
            f"/payment-methods/{mobile}/sites/{priced.site2}", json={"enabled": False}
        ).status_code
        == 404
    )


# --- Caisse optionnelle par site ------------------------------------------------------------------


def test_site_without_cash_sells_cash_without_session(priced: World, owner_db: Session) -> None:
    sites = {s["site_id"]: s for s in priced.owner.get("/cash/sites").json()}
    assert sites[priced.site]["enabled"] is False and sites[priced.site2]["enabled"] is False
    sale = _draft(priced, 2)
    done = _validated(
        priced,
        sale,
        [{"amount_received": "50000", "payment_method_id": _method_id(priced, "Espèces")}],
    )
    assert (done["payment_status"], done["is_credit"]) == ("PAID", False)
    [payment] = _payments(priced.owner, sale)
    assert (payment["amount"], payment["amount_received"], payment["change_given"]) == (
        "20000.00",
        "50000.00",
        "30000.00",
    )
    # Aucune session, aucun fond, aucun mouvement de caisse.
    assert _count(owner_db, "SELECT count(*) FROM cash_movements") == 0
    assert _count(owner_db, "SELECT count(*) FROM cash_sessions") == 0
    # Ouvrir une session sur un site sans caisse : refusé (message explicite).
    register = _register(priced.owner, priced.site)
    refused = priced.owner.post(
        "/cash/sessions", json={"cash_register_id": register["id"], "opening_float": "0"}
    )
    assert refused.status_code == 409 and refused.json()["code"] == "cash_disabled_for_site"


def test_site_without_cash_mobile_money(priced: World, owner_db: Session) -> None:
    sale = _draft(priced, 1, site=priced.site2)
    done = _validated(
        priced,
        sale,
        [{"amount": "10000", "payment_method_id": _method_id(priced, "Mobile Money")}],
    )
    assert done["payment_status"] == "PAID"
    [payment] = _payments(priced.owner, sale)
    assert (payment["amount_received"], payment["change_given"]) == (None, None)
    assert _count(owner_db, "SELECT count(*) FROM cash_movements") == 0


def test_site_with_cash_requires_the_users_own_session(
    priced: World, client: TestClient, owner_db: Session
) -> None:
    sh.enable_cash(priced.owner, priced.site)
    cash = _method_id(priced, "Espèces")
    sale = _draft(priced, 2)
    no_session = _validate(priced, sale, [{"amount_received": "25000", "payment_method_id": cash}])
    assert no_session.json()["code"] == "cash_session_required"
    register = _register(priced.owner, priced.site)
    session = _open(priced.owner, register, "5000")
    done = _validated(priced, sale, [{"amount_received": "25000", "payment_method_id": cash}])
    assert done["payment_status"] == "PAID"
    # Mouvement = montant imputé (la monnaie rendue ne reste pas en caisse).
    owner_db.expire_all()
    amounts = (
        owner_db.execute(
            text(
                "SELECT amount::text FROM cash_movements WHERE cash_session_id = :s "
                "AND movement_type = 'SALE_CASH_IN'"
            ),
            {"s": session["id"]},
        )
        .scalars()
        .all()
    )
    assert amounts == ["20000.00"]
    assert priced.owner.get(f"/cash/sessions/{session['id']}").json()["theoretical_balance"] == (
        "25000.00"
    )
    # Session = site + poste + utilisateur : un vendeur n'encaisse jamais dans la session d'un
    # autre utilisateur ; il ouvre la sienne sur un autre poste (plusieurs postes par site).
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    own = _draft(priced, 1, api=seller)
    refused = _validate(priced, own, [{"amount": "10000", "payment_method_id": cash}], api=seller)
    assert refused.json()["code"] == "cash_session_required"
    second = _register(priced.owner, priced.site, "Poste 2")
    seller_session = _open(seller, second)
    ok = _validate(priced, own, [{"amount": "10000", "payment_method_id": cash}], api=seller)
    assert ok.status_code == 200, ok.text
    owner_db.expire_all()
    assert owner_db.execute(
        text("SELECT cash_session_id FROM cash_movements WHERE source_id = :s"), {"s": own["id"]}
    ).scalar_one() == uuid.UUID(seller_session["id"])
    # Les autres moyens ne passent jamais par la caisse.
    mobile = _draft(priced, 1)
    _validated(
        priced,
        mobile,
        [{"amount": "10000", "payment_method_id": _method_id(priced, "Mobile Money")}],
    )
    assert (
        _count(owner_db, "SELECT count(*) FROM cash_movements WHERE source_id = :s", s=mobile["id"])
        == 0
    )


def test_cash_deactivation_refused_while_a_session_is_open(priced: World) -> None:
    sh.enable_cash(priced.owner, priced.site)
    session = _open(priced.owner, _register(priced.owner, priced.site))
    refused = priced.owner.put(f"/cash/sites/{priced.site}", json={"enabled": False})
    assert refused.status_code == 409
    assert (refused.json()["code"], refused.json()["open_sessions"]) == ("cash_sessions_open", 1)
    assert "clôturez" in refused.json()["detail"]
    closed = priced.owner.post(
        f"/cash/sessions/{session['id']}/close", json={"counted_balance": "0"}
    )
    assert closed.status_code == 200, closed.text
    off = priced.owner.put(f"/cash/sites/{priced.site}", json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    # Désactivée : l'historique reste consultable ; ventes en espèces sans session.
    assert priced.owner.get(f"/cash/sessions/{session['id']}").status_code == 200
    sale = _draft(priced, 1)
    assert (
        _validate(
            priced, sale, [{"amount": "10000", "payment_method_id": _method_id(priced, "Espèces")}]
        ).status_code
        == 200
    )
    # Réactivation possible.
    on = priced.owner.put(f"/cash/sites/{priced.site}", json={"enabled": True})
    assert on.status_code == 200 and on.json()["enabled"] is True
    actions = _audit_actions(priced)
    assert actions.count("cash_site.enabled") == 2 and actions.count("cash_site.disabled") == 1


def test_cash_toggle_and_opening_are_serialized(priced: World, app: Any, owner_db: Session) -> None:
    """Désactivation et ouverture simultanées : jamais de session ouverte sur un site dont la
    caisse est désactivée."""
    sh.enable_cash(priced.owner, priced.site)
    register = _register(priced.owner, priced.site)
    barrier = threading.Barrier(2)
    statuses: dict[str, int] = {}

    def run(name: str, method: str, path: str, body: Any) -> None:
        with TestClient(app) as c:
            api = Api(c, priced.owner.token)
            barrier.wait()
            statuses[name] = getattr(api, method)(path, json=body).status_code

    threads = [
        threading.Thread(
            target=run,
            args=(
                "open",
                "post",
                "/cash/sessions",
                {"cash_register_id": register["id"], "opening_float": "0"},
            ),
        ),
        threading.Thread(
            target=run, args=("off", "put", f"/cash/sites/{priced.site}", {"enabled": False})
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(statuses.values()) in ([200, 409], [200, 422], [201, 409])
    open_sessions = _count(owner_db, "SELECT count(*) FROM cash_sessions WHERE status = 'OPEN'")
    enabled = owner_db.execute(
        text("SELECT enabled FROM cash_site_settings WHERE site_id = :s"), {"s": priced.site}
    ).scalar_one()
    assert not (open_sessions and not enabled)


def test_cash_toggle_permissions(priced: World, client: TestClient) -> None:
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    for api in (seller, manager):
        assert api.put(f"/cash/sites/{priced.site}", json={"enabled": True}).status_code == 403
    # Permission de configuration existante (organization.site.manage), jamais un nom de rôle.
    site_admin = _custom_member(
        priced,
        client,
        "sites@example.com",
        ["organization.site.manage", "cash_register.register.view"],
    )
    assert site_admin.put(f"/cash/sites/{priced.site}", json={"enabled": True}).status_code == 200
    local_admin = sh.member(
        priced, client, "admin-boutique@example.com", "administrator", site_ids=[priced.site]
    )
    assert local_admin.put(f"/cash/sites/{priced.site}", json={"enabled": False}).status_code == 200
    assert local_admin.put(f"/cash/sites/{priced.site2}", json={"enabled": True}).status_code == 404
    assert {s["site_id"] for s in local_admin.get("/cash/sites").json()} == {priced.site}


# --- Montant reçu, monnaie, paiements mixtes ------------------------------------------------------


@pytest.mark.parametrize(
    ("payment", "code"),
    [
        ({"amount": "20000", "amount_received": "15000"}, "cash_received_insufficient"),
        ({"amount_received": "25000", "kind": "Mobile Money"}, "change_not_allowed"),
        ({"amount": "30000"}, "payment_exceeds_balance"),
    ],
)
def test_cash_input_rules(priced: World, payment: dict[str, Any], code: str) -> None:
    label = payment.pop("kind", "Espèces")
    sale = _draft(priced, 2, customer_id=sh.credit_customer(priced))
    response = _validate(
        priced, sale, [{**payment, "payment_method_id": _method_id(priced, label)}]
    )
    assert response.status_code == 422 and response.json()["code"] == code


def test_mixed_payment_change_only_on_the_cash_part(priced: World) -> None:
    sale = _draft(priced, 3)  # 30 000
    done = _validated(
        priced,
        sale,
        [
            # Espèces en premier dans la saisie : imputées après les autres moyens.
            {"amount_received": "20000", "payment_method_id": _method_id(priced, "Espèces")},
            {"amount": "15000", "payment_method_id": _method_id(priced, "Mobile Money")},
        ],
    )
    assert (done["payment_status"], done["paid_amount"], done["is_credit"]) == (
        "PAID",
        "30000.00",
        False,
    )
    by_kind = {p["method"]: p for p in _payments(priced.owner, sale)}
    assert by_kind["MOBILE_MONEY"]["amount"] == "15000.00"
    assert by_kind["MOBILE_MONEY"]["change_given"] is None
    assert (
        by_kind["CASH"]["amount"],
        by_kind["CASH"]["amount_received"],
        by_kind["CASH"]["change_given"],
    ) == ("15000.00", "20000.00", "5000.00")


def test_partial_and_multiple_payments_after_validation(priced: World) -> None:
    sale = _draft(priced, 3, customer_id=sh.credit_customer(priced))
    validated = _validated(
        priced,
        sale,
        [{"amount": "10000", "payment_method_id": _method_id(priced, "Carte bancaire")}],
    )
    assert (validated["is_credit"], validated["credit_status"]) == (True, "PARTIAL")
    cash = _method_id(priced, "Espèces")
    first = priced.owner.post(
        f"/sales/{sale['id']}/payments", json={"amount_received": "5000", "payment_method_id": cash}
    )
    assert first.status_code == 201 and first.json()["change_given"] == "0.00"
    # Espèces sans montant : min(reçu, reste dû), monnaie calculée par le serveur.
    last = priced.owner.post(
        f"/sales/{sale['id']}/payments",
        json={"amount_received": "20000", "payment_method_id": cash},
    )
    assert (last.json()["amount"], last.json()["change_given"]) == ("15000.00", "5000.00")
    after = priced.owner.get(f"/sales/{sale['id']}").json()
    assert (after["payment_status"], after["credit_status"]) == ("PAID", "PAID")


# --- Crédit ---------------------------------------------------------------------------------------


def test_credit_requires_an_identified_customer(priced: World, owner_db: Session) -> None:
    sale = _draft(priced, 2)
    refused = _validate(
        priced, sale, [{"amount": "5000", "payment_method_id": _method_id(priced, "Espèces")}]
    )
    assert refused.status_code == 422
    assert (refused.json()["code"], refused.json()["remaining"]) == (
        "credit_customer_required",
        "15000.00",
    )
    # Tout ou rien : ni stock, ni paiement, ni numéro.
    assert priced.owner.get(f"/sales/{sale['id']}").json()["status"] == "DRAFT"
    assert _count(owner_db, "SELECT count(*) FROM payments") == 0
    assert sh.level(owner_db, priced, 0)[0] == "200.000"
    # Vente ordinaire (sans client) entièrement payée : acceptée.
    ok = _validated(
        priced, sale, [{"amount": "20000", "payment_method_id": _method_id(priced, "Espèces")}]
    )
    assert (ok["customer_id"], ok["is_credit"], ok["credit_status"]) == (None, False, None)


def test_credit_with_customer_and_computed_statuses(priced: World) -> None:
    customer = sh.credit_customer(priced)
    sale = _draft(priced, 2, customer_id=customer)
    validated = _validated(priced, sale, [])
    assert (validated["is_credit"], validated["credit_status"], validated["remaining_amount"]) == (
        True,
        "OPEN",
        "20000.00",
    )
    mobile = _method_id(priced, "Mobile Money")
    paid = priced.owner.post(
        f"/sales/{sale['id']}/payments", json={"amount": "20000", "payment_method_id": mobile}
    ).json()
    assert priced.owner.get(f"/sales/{sale['id']}").json()["credit_status"] == "PAID"
    priced.owner.post(
        f"/sales/{sale['id']}/payments/{paid['id']}/cancel", json={"reason": "Erreur de saisie"}
    )
    assert priced.owner.get(f"/sales/{sale['id']}").json()["credit_status"] == "OPEN"
    cancelled = priced.owner.post(
        f"/sales/{sale['id']}/cancel", json={"reason": "Retour du client"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["credit_status"] == "CANCELLED"
    receivables = priced.owner.get("/receivables").json()
    assert receivables["total"] == 0


def test_credit_permission(priced: World, client: TestClient) -> None:
    customer = sh.credit_customer(priced)
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    sale = _draft(priced, 1, api=seller, customer_id=customer)
    refused = _validate(priced, sale, [], api=seller)
    assert refused.status_code == 403 and refused.json()["code"] == "credit_not_allowed"
    # Comptant : toujours permis au vendeur.
    ok = _validate(
        priced,
        sale,
        [{"amount": "10000", "payment_method_id": _method_id(priced, "Espèces")}],
        api=seller,
    )
    assert ok.status_code == 200, ok.text
    creditor = _custom_member(
        priced,
        client,
        "credit@example.com",
        ["sales.sale.view", "sales.sale.create", "sales.sale.validate", "sales.sale.credit_create"],
    )
    on_credit = _draft(priced, 1, api=creditor, customer_id=customer)
    assert _validate(priced, on_credit, [], api=creditor).json()["is_credit"] is True
    # POS : même règle (même service).
    checkout = seller.post(
        "/pos/checkout",
        json={
            "site_id": priced.site,
            "customer_id": customer,
            "lines": [{"article_id": priced.articles[0], "quantity": "1"}],
            "payments": [],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert checkout.json()["code"] == "credit_not_allowed"


def test_credit_limit_and_admin_override(
    priced: World, client: TestClient, owner_db: Session
) -> None:
    customer = sh.credit_customer(priced)
    assert (
        priced.owner.patch(f"/customers/{customer}", json={"credit_limit": "15000"}).status_code
        == 200
    )
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    sale = _draft(priced, 2, api=manager, customer_id=customer)  # 20 000 > 15 000
    exceeded = _validate(priced, sale, [], api=manager)
    assert exceeded.status_code == 422
    body = exceeded.json()
    assert (body["code"], body["override_allowed"]) == ("credit_limit_exceeded", False)
    forced = _validate(priced, sale, [], api=manager, credit_override={"reason": "Client fidèle"})
    assert forced.status_code == 403 and forced.json()["code"] == "credit_override_not_allowed"
    # L'administrateur autorise le dépassement : justification obligatoire.
    assert _validate(priced, sale, []).json()["override_allowed"] is True
    short = _validate(priced, sale, [], credit_override={"reason": "ok"})
    assert short.status_code == 422
    done = _validated(priced, sale, [], credit_override={"reason": "Client fidèle depuis 2019"})
    assert (done["credit_override_reason"], done["credit_override_amount"]) == (
        "Client fidèle depuis 2019",
        "5000.00",
    )
    assert done["credit_override_by_name"] and done["credit_override_at"]
    owner_db.expire_all()
    [entry] = (
        owner_db.execute(
            text("SELECT data FROM audit_logs WHERE action = 'sale.credit_limit_overridden'")
        )
        .scalars()
        .all()
    )
    assert (entry["excess"], entry["credit_limit"], entry["reason"]) == (
        "5000.00",
        "15000.00",
        "Client fidèle depuis 2019",
    )
    # Sans limite (NULL = non configurée) : jamais bloqué ; aucun dépassement enregistré, même
    # si une justification est fournie.
    priced.owner.patch(f"/customers/{customer}", json={"credit_limit": None})
    unlimited = _validated(
        priced,
        _draft(priced, 5, customer_id=customer),
        [],
        credit_override={"reason": "Justification inutile"},
    )
    assert unlimited["is_credit"] is True and unlimited["credit_override_at"] is None


def test_concurrent_credit_validations_respect_the_limit(
    priced: World, app: Any, owner_db: Session
) -> None:
    customer = sh.credit_customer(priced)
    priced.owner.patch(f"/customers/{customer}", json={"credit_limit": "30000"})
    sales = [_draft(priced, 2, customer_id=customer) for _ in range(2)]  # 2 × 20 000
    barrier = threading.Barrier(2)
    statuses: list[int] = [0, 0]

    def run(index: int) -> None:
        with TestClient(app) as c:
            api = Api(c, priced.owner.token)
            barrier.wait()
            statuses[index] = api.post(
                f"/sales/{sales[index]['id']}/validate", json={"payments": []}
            ).status_code

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(statuses) == [200, 422]
    assert _count(owner_db, "SELECT count(*) FROM sales WHERE status = 'VALIDATED'") == 1


# --- Numérotation ---------------------------------------------------------------------------------


def test_number_assigned_at_validation_per_site(priced: World, owner_db: Session) -> None:
    main, depot = _site_code(priced, priced.site), _site_code(priced, priced.site2)
    cash = _method_id(priced, "Espèces")
    draft = _draft(priced, 1)
    assert draft["number"] is None
    # Brouillon annulé : jamais numéroté ; refus (stock insuffisant) : aucun numéro consommé.
    cancelled = _draft(priced, 1)
    priced.owner.post(f"/sales/{cancelled['id']}/cancel", json={"reason": "Panier abandonné"})
    too_big = _draft(priced, 500)
    assert (
        _validate(priced, too_big, [{"amount": "5000000", "payment_method_id": cash}]).status_code
        == 422
    )
    first = _validated(priced, draft, [{"amount": "10000", "payment_method_id": cash}])
    assert first["number"] == f"VENT-{main.upper()}-{YEAR}-000001"
    other_site = _validated(
        priced,
        _draft(priced, 1, site=priced.site2),
        [{"amount": "10000", "payment_method_id": cash}],
    )
    assert other_site["number"] == f"VENT-{depot.upper()}-{YEAR}-000001"
    second = _validated(priced, _draft(priced, 1), [{"amount": "10000", "payment_method_id": cash}])
    assert second["number"] == f"VENT-{main.upper()}-{YEAR}-000002"
    assert priced.owner.get(f"/sales/{cancelled['id']}").json()["number"] is None
    # Le rembourrage n'est qu'une présentation : 999 999 → 1 000 000.
    owner_db.execute(
        text("UPDATE document_sequences SET next_value = 999998 WHERE sequence_key = :k"),
        {"k": f"{priced.site}:sale:{YEAR}"},
    )
    owner_db.commit()
    numbers = [
        _validated(priced, _draft(priced, 1), [{"amount": "10000", "payment_method_id": cash}])[
            "number"
        ]
        for _ in range(2)
    ]
    assert numbers == [
        f"VENT-{main.upper()}-{YEAR}-999999",
        f"VENT-{main.upper()}-{YEAR}-1000000",
    ]
    # Recherche par numéro ; paiement rattaché au numéro définitif.
    found = priced.owner.get("/sales", params={"search": numbers[1]}).json()
    assert found["total"] == 1
    assert _payments(priced.owner, first)[0]["sale_number"] == first["number"]


def test_numbering_restarts_each_year(priced: World, app: Any, owner_db: Session) -> None:
    cash = _method_id(priced, "Espèces")
    code = _site_code(priced, priced.site).upper()
    assert (
        _validated(priced, _draft(priced, 1), [{"amount": "10000", "payment_method_id": cash}])[
            "number"
        ]
        == f"VENT-{code}-{YEAR}-000001"
    )
    # Horloge avancée à l'année suivante : abonnement et session de connexion prolongés.
    for sql in (
        "UPDATE subscriptions SET current_period_end = current_period_end + interval '3 years'",
        "UPDATE auth_sessions SET expires_at = expires_at + interval '3 years'",
    ):
        owner_db.execute(text(sql))
    owner_db.commit()
    next_year = datetime(YEAR + 1, 1, 2, 12, 0, tzinfo=UTC)
    app.dependency_overrides[get_now] = lambda: next_year
    try:
        sale = _draft(priced, 1)
        numbered = _validated(priced, sale, [{"amount": "10000", "payment_method_id": cash}])
    finally:
        app.dependency_overrides.pop(get_now, None)
    assert numbered["number"] == f"VENT-{code}-{YEAR + 1}-000001"


def test_concurrent_validations_get_distinct_consecutive_numbers(priced: World, app: Any) -> None:
    cash = _method_id(priced, "Espèces")
    drafts = [_draft(priced, 1) for _ in range(6)]
    barrier = threading.Barrier(len(drafts))
    numbers: list[str | None] = [None] * len(drafts)

    def run(index: int) -> None:
        with TestClient(app) as c:
            api = Api(c, priced.owner.token)
            barrier.wait()
            response = api.post(
                f"/sales/{drafts[index]['id']}/validate",
                json={"payments": [{"amount": "10000", "payment_method_id": cash}]},
            )
            numbers[index] = response.json().get("number")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(drafts))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    code = _site_code(priced, priced.site).upper()
    assert sorted(n or "" for n in numbers) == [
        f"VENT-{code}-{YEAR}-{i:06d}" for i in range(1, len(drafts) + 1)
    ]


def test_draft_number_is_never_kept_and_site_code_is_locked(
    priced: World, owner_db: Session
) -> None:
    """Lot 2 : un brouillon n'a jamais de numéro conservé (aucune compatibilité avec un ancien
    format, application non déployée) : la validation attribue toujours ``VENT-…`` ; le code
    d'un site devient stable dès qu'il a émis un numéro."""
    cash = _method_id(priced, "Espèces")
    draft = _draft(priced, 1)
    owner_db.execute(
        text("UPDATE sales SET number = 'ANCIEN-000042' WHERE id = :id"), {"id": draft["id"]}
    )
    owner_db.commit()
    kept = _validated(priced, draft, [{"amount": "10000", "payment_method_id": cash}])
    code = _site_code(priced, priced.site).upper()
    assert kept["number"] == f"VENT-{code}-{YEAR}-000001"
    renamed = priced.owner.patch(f"/sites/{priced.site2}", json={"code": "DEP2"})
    assert renamed.status_code == 200, renamed.text
    _validated(
        priced,
        _draft(priced, 1, site=priced.site2),
        [{"amount": "10000", "payment_method_id": cash}],
    )
    locked = priced.owner.patch(f"/sites/{priced.site2}", json={"code": "DEP3"})
    assert locked.status_code == 409 and locked.json()["code"] == "site_code_locked"
    # Le nom reste modifiable.
    assert (
        priced.owner.patch(f"/sites/{priced.site2}", json={"name": "Dépôt nord"}).status_code == 200
    )
    # Numéro jamais modifié en base par un ordre de l'application (vente validée immuable).
    edit = priced.owner.put(
        f"/sales/{kept['id']}",
        json={"lines": [{"article_id": priced.articles[0], "quantity": "2"}]},
    )
    assert edit.status_code == 409
    # En base : le numéro d'une vente validée est définitif (déclencheur), même pour le
    # propriétaire du schéma.
    with pytest.raises(IntegrityError, match="définitif"):
        owner_db.execute(
            text("UPDATE sales SET number = 'AUTRE-999999' WHERE id = :id"), {"id": kept["id"]}
        )
    owner_db.rollback()


def test_checkout_idempotency_keeps_a_single_number(priced: World, owner_db: Session) -> None:
    body = {
        "site_id": priced.site,
        "lines": [{"article_id": priced.articles[0], "quantity": "1"}],
        "payments": [
            {"amount_received": "10000", "payment_method_id": _method_id(priced, "Espèces")}
        ],
        "idempotency_key": str(uuid.uuid4()),
    }
    first = priced.owner.post("/pos/checkout", json=body)
    replay = priced.owner.post("/pos/checkout", json=body)
    assert first.status_code == 201, first.text
    assert replay.json()["replayed"] is True
    assert replay.json()["sale"]["number"] == first.json()["sale"]["number"]
    assert _count(owner_db, "SELECT count(*) FROM sales") == 1
    assert (
        _count(
            owner_db,
            "SELECT sum(next_value) FROM document_sequences WHERE sequence_key LIKE '%:sale:%'",
        )
        == 1
    )


# --- Portée des ventes ----------------------------------------------------------------------------


def test_own_sales_scope_and_view_all(priced: World, client: TestClient) -> None:
    cash = _method_id(priced, "Espèces")
    seller = sh.member(priced, client, "vendeur@example.com", "seller", all_sites=True)
    other = sh.member(priced, client, "vendeur2@example.com", "seller", all_sites=True)
    mine = _draft(priced, 1, api=seller)
    _validate(priced, mine, [{"amount": "10000", "payment_method_id": cash}], api=seller)
    theirs = _draft(priced, 1, api=other)
    owners = _draft(priced, 1)
    assert {s["id"] for s in seller.get("/sales").json()["items"]} == {mine["id"]}
    for sale in (theirs, owners):
        assert seller.get(f"/sales/{sale['id']}").status_code == 404
        assert seller.get(f"/sales/{sale['id']}/payments").status_code == 404
    manager = sh.member(priced, client, "gestion@example.com", "manager", all_sites=True)
    viewer = sh.member(priced, client, "consultant@example.com", "viewer", all_sites=True)
    for api in (manager, viewer):
        assert {s["id"] for s in api.get("/sales").json()["items"]} == {
            mine["id"],
            theirs["id"],
            owners["id"],
        }
    auditor = _custom_member(
        priced, client, "tout@example.com", ["sales.sale.view", "sales.sale.view_all"]
    )
    assert auditor.get(f"/sales/{theirs['id']}").status_code == 200


# --- Isolation : Tenant A / Site A ≠ Tenant A / Site B ≠ Tenant B ---------------------------------


def test_isolation_between_sites_and_tenants(
    priced: World, client: TestClient, provision: Any, api_for: Any
) -> None:
    cash = _method_id(priced, "Espèces")
    on_depot = _validated(
        priced,
        _draft(priced, 1, site=priced.site2),
        [{"amount": "10000", "payment_method_id": cash}],
    )
    shop = sh.member(priced, client, "boutique@example.com", "manager", site_ids=[priced.site])
    assert shop.get(f"/sales/{on_depot['id']}").status_code == 404
    assert on_depot["id"] not in {s["id"] for s in shop.get("/sales").json()["items"]}
    assert shop.get("/payment-methods", params={"site_id": priced.site2}).status_code == 404
    assert shop.put(f"/cash/sites/{priced.site2}", json={"enabled": True}).status_code == 403
    denied = shop.post(
        "/sales",
        json={
            "site_id": priced.site2,
            "lines": [{"article_id": priced.articles[0], "quantity": "1"}],
        },
    )
    assert denied.status_code in (403, 404)

    provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    beta: Api = api_for("owner@beta.example.com")
    alpha_methods = {m["id"] for m in _methods(priced.owner).values()}
    beta_methods = {m["id"] for m in _methods(beta).values()}
    assert alpha_methods.isdisjoint(beta_methods) and len(beta_methods) == 5
    assert beta.patch(f"/payment-methods/{cash}", json={"label": "Piraté"}).status_code == 404
    assert (
        beta.put(
            f"/payment-methods/{cash}/sites/{priced.site}", json={"enabled": False}
        ).status_code
        == 404
    )
    assert beta.put(f"/cash/sites/{priced.site}", json={"enabled": True}).status_code in (403, 404)
    assert beta.get(f"/sales/{on_depot['id']}").status_code == 404
    # Le moyen d'une autre entreprise n'est jamais utilisable.
    category = beta.post("/catalog/categories", json={"name": "Divers"}).json()["id"]
    article = beta.post(
        "/catalog/articles",
        json={
            "reference": "B-1",
            "designation": "Article B",
            "category_id": category,
            "unit": "u",
            "purchase_price": "1",
            "sale_price": "1",
        },
    )
    assert article.status_code == 201, article.text
    beta_sale = beta.post(
        "/sales",
        json={
            "site_id": beta.get("/sites").json()[0]["id"],
            "lines": [{"article_id": article.json()["id"], "quantity": "1"}],
        },
    )
    assert beta_sale.status_code == 201, beta_sale.text
    beta_sale = beta_sale.json()
    foreign = beta.post(
        f"/sales/{beta_sale['id']}/validate",
        json={"payments": [{"amount": "1", "payment_method_id": cash}]},
    )
    assert foreign.json()["code"] == "payment_method_not_found"


def test_rls_and_grants_on_new_tables(
    priced: World, provision: Any, app_engine: Engine, owner_db: Session
) -> None:
    sh.enable_cash(priced.owner, priced.site)
    mobile = _method_id(priced, "Mobile Money")
    priced.owner.put(f"/payment-methods/{mobile}/sites/{priced.site2}", json={"enabled": False})
    b = provision("beta")
    tenant_a = owner_db.execute(
        text("SELECT tenant_id FROM payment_methods WHERE id = :id"), {"id": mobile}
    ).scalar_one()
    tables = ("payment_methods", "payment_method_sites", "cash_site_settings")
    with create_session_factory(app_engine)() as db:
        for table in tables:
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.execute(text("SELECT count(*) FROM payment_method_sites")).scalar_one() == 0
        assert db.execute(text("SELECT count(*) FROM cash_site_settings")).scalar_one() == 0
        assert (
            db.execute(
                text("SELECT count(*) FROM payment_methods WHERE id = :id"), {"id": mobile}
            ).scalar_one()
            == 0
        )
        assert (
            db.execute(
                text("UPDATE payment_methods SET label = 'X' WHERE id = :id"), {"id": mobile}
            ).rowcount
            == 0
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO payment_methods (id, tenant_id, label, kind, integration_mode, "
                    "reference_required, is_active, sort_order) VALUES (:id, :t, 'Intrus', "
                    "'CASH', 'MANUAL', false, true, 0)"
                ),
                {"id": uuid.uuid4(), "t": tenant_a},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=tenant_a)
        # Jamais supprimés ; le type d'un moyen n'est jamais modifiable ; paiements immuables.
        for sql in (
            "DELETE FROM payment_methods",
            "DELETE FROM payment_method_sites",
            "DELETE FROM cash_site_settings",
            "UPDATE payment_methods SET kind = 'CARD'",
            "UPDATE payments SET method_label = 'X'",
            "UPDATE payments SET change_given = 0",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(sql))
            db.rollback()
            set_db_context(db, tenant_id=tenant_a)
