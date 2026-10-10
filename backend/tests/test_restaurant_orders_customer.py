"""Palier R2-E (ADR-0049 Z1) — association tardive d'un client à une commande.

- ``PUT /restaurant/orders/{id}/customer`` : commande OUVERTE et NON réglée, même permission que
  le choix du client à la création (``order.create`` sur le site), mêmes contrôles du client ;
  remplacement motivé ; même client : rien ne change ; évènement ``CUSTOMER_SET`` et audit.
- Aucun droit au crédit n'en découle : le règlement applique les règles existantes des ventes.
- Sérialisé avec le règlement par le verrou de la commande : la vente porte toujours le client
  de la commande.
- Migration 0044 : droit ``UPDATE (customer_id)``, type d'évènement ; descente refusée dès
  qu'une association existe.

Registre de test (``restaurant.orders`` disponible, N1) ; appels directs à l'API.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests.conftest import add_site
from tests.test_restaurant_orders import MAQUIS, _api, _code, _line, _menu, _ok, _order
from tests.test_restaurant_orders_claims import _audit, _join, _roles
from tests.test_restaurant_orders_settlement import (
    _cash,
    _customer,
    _events,
    _race,
    _receive,
    _serve_all,
    _settle,
)
from tests.test_restaurant_sales_migration import _alembic, at_head  # noqa: F401


@pytest.fixture
def resto(provision: Any, oclient: TestClient) -> SimpleNamespace:
    t = provision("ro-cust", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-cust.example.com")
    site = str(t.site_id)
    supplier = _ok(owner.post("/suppliers", json={"name": "Brasserie"}), 201)["id"]
    r = SimpleNamespace(
        tenant=t.tenant_id,
        site=site,
        owner=owner,
        client=oclient,
        menu=_menu(owner, site, "C"),
        supplier=supplier,
    )
    _receive(r, r.menu.article, "100")
    return r


def _associate(api: Any, order_id: str, customer_id: str, reason: str | None = None) -> Any:
    body: dict[str, Any] = {"customer_id": customer_id}
    if reason is not None:
        body["reason"] = reason
    return api.put(f"/restaurant/orders/{order_id}/customer", json=body)


def test_late_customer_makes_a_served_order_settleable_on_credit(resto: SimpleNamespace) -> None:
    """Z1 : servie, non réglée, client parti — le client nommé ensuite rend le crédit possible
    selon les règles EXISTANTES des ventes ; la commande se clôt."""
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit, "3")]), 201)
    _serve_all(r.owner, order["id"])
    assert _code(_settle(r.owner, order["id"])) == (422, "credit_customer_required")
    customer = _customer(r)
    updated = _ok(_associate(r.owner, order["id"], customer))
    assert updated["customer_id"] == customer and updated["customer_name"].startswith("Client")
    assert updated["version"] > order["version"]
    settled = _ok(_settle(r.owner, order["id"]), 201)
    assert settled["order"]["status"] == "CLOSED"
    sale = _ok(r.owner.get(f"/sales/{settled['sale_id']}"))
    assert (sale["customer_id"], sale["is_credit"], sale["total"]) == (customer, True, "2100.00")
    events = [e for e in _ok(r.owner.get(f"/restaurant/orders/{order['id']}/events"))]
    [associated] = [e for e in events if e["event_type"] == "CUSTOMER_SET"]
    assert associated["data"]["customer_id"] == customer
    assert associated["data"]["previous_customer_id"] is None
    assert associated["reason"] is None
    [audit] = _audit(r.owner, "restaurant_order.customer_set")
    assert audit["entity_id"] == order["id"] and audit["data"]["customer_id"] == customer


def test_association_rules(resto: SimpleNamespace) -> None:
    r = resto
    first, second = _customer(r), _customer(r)
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)], customer_id=first), 201)
    # Même client : rien ne change, aucun évènement.
    same = _ok(_associate(r.owner, order["id"], first))
    assert same["version"] == order["version"]
    assert "CUSTOMER_SET" not in _events(r.owner, order["id"])
    # Remplacement : motif obligatoire.
    assert _code(_associate(r.owner, order["id"], second)) == (
        422,
        "customer_change_reason_required",
    )
    assert _code(_associate(r.owner, order["id"], second, "   ")) == (
        422,
        "customer_change_reason_required",
    )
    replaced = _ok(_associate(r.owner, order["id"], second, "Erreur de client"))
    assert replaced["customer_id"] == second
    [event] = [
        e
        for e in _ok(r.owner.get(f"/restaurant/orders/{order['id']}/events"))
        if e["event_type"] == "CUSTOMER_SET"
    ]
    assert (event["data"]["previous_customer_id"], event["reason"]) == (first, "Erreur de client")
    # Client inconnu ou désactivé : refusé, commande inchangée.
    unknown = "00000000-0000-7000-8000-000000000000"
    assert _code(_associate(r.owner, order["id"], unknown, "x")) == (422, "customer_not_found")
    inactive = _customer(r)
    _ok(r.owner.post(f"/customers/{inactive}/deactivate"))
    assert _code(_associate(r.owner, order["id"], inactive, "x")) == (422, "customer_inactive")
    assert _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))["customer_id"] == second


def test_association_needs_an_open_unsettled_order(resto: SimpleNamespace) -> None:
    r = resto
    customer = _customer(r)
    settled = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(_settle(r.owner, settled["id"], payments=_cash("700")), 201)
    assert _code(_associate(r.owner, settled["id"], customer)) == (409, "order_settled")
    cancelled = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(r.owner.post(f"/restaurant/orders/{cancelled['id']}/cancel", json={"reason": "Parti"}))
    assert _code(_associate(r.owner, cancelled["id"], customer)) == (409, "order_cancelled")
    closed = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(_settle(r.owner, closed["id"], payments=_cash("700")), 201)
    assert _serve_all(r.owner, closed["id"])["status"] == "CLOSED"
    assert _code(_associate(r.owner, closed["id"], customer)) == (409, "order_closed")


def test_association_after_sale_cancellation_z3(resto: SimpleNamespace) -> None:
    """Vente annulée (Z3) : la commande redevient « à régler », un autre client peut être
    associé (motif) et la nouvelle vente le porte."""
    r = resto
    first, second = _customer(r), _customer(r)
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)], customer_id=first), 201)
    settled = _ok(_settle(r.owner, order["id"]), 201)
    _ok(r.owner.post(f"/sales/{settled['sale_id']}/cancel", json={"reason": "Mauvais client"}))
    _ok(_associate(r.owner, order["id"], second, "Mauvais client"))
    again = _ok(_settle(r.owner, order["id"]), 201)
    assert _ok(r.owner.get(f"/sales/{again['sale_id']}"))["customer_id"] == second


def test_association_grants_no_credit_and_follows_permissions(resto: SimpleNamespace) -> None:
    r = resto
    roles = _roles(r)
    waiter_role = _ok(r.owner.post("/roles/from-template", json={"template_code": "waiter"}), 201)
    preparer_role = _ok(
        r.owner.post("/roles/from-template", json={"template_code": "preparer"}), 201
    )
    waiter = _join(r, "serveur@ro-cust.example.com", [waiter_role["id"]])
    preparer = _join(r, "prepa@ro-cust.example.com", [preparer_role["id"]])
    seller = _join(r, "vendeur@ro-cust.example.com", [roles["seller"]])
    customer = _customer(r)
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    refused = _associate(preparer.api, order["id"], customer)
    assert _code(refused) == (403, "permission_denied")
    # Le Serveur saisit le client (comme à la création) mais n'encaisse jamais.
    _ok(_associate(waiter.api, order["id"], customer))
    assert _code(_settle(waiter.api, order["id"]))[0] == 403
    # Le Vendeur ne vend jamais à crédit, client associé ou non.
    assert _code(_settle(seller.api, order["id"])) == (403, "credit_not_allowed")
    assert _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))["settlement_status"] == (
        "UNSETTLED"
    )


def test_association_is_isolated_by_company_and_site(
    resto: SimpleNamespace, provision: Any, oclient: TestClient, app_engine: Engine
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    provision("ro-cust-2", profile=MAQUIS, plan="ENTREPRISE")
    stranger = _api(oclient, "owner@ro-cust-2.example.com")
    foreign = str(
        _ok(
            stranger.post("/customers", json={"customer_type": "INDIVIDUAL", "name": "Ailleurs"}),
            201,
        )["id"]
    )
    # Commande d'une autre entreprise : introuvable ; client d'une autre entreprise : introuvable.
    assert _code(_associate(stranger, order["id"], foreign)) == (404, "order_not_found")
    assert _code(_associate(r.owner, order["id"], foreign)) == (422, "customer_not_found")
    # Membre sans accès au site de la commande : refusé.
    roles = _roles(r)
    other_site = _ok(
        add_site(r.owner, "Maquis 2", "MAQ2", "restaurant", business_profile_code=MAQUIS), 201
    )
    outsider = _join(
        r,
        "annexe@ro-cust.example.com",
        [roles["seller"]],
        all_sites=False,
        site_ids=[other_site["id"]],
    )
    assert _code(_associate(outsider.api, order["id"], _customer(r))) == (404, "order_not_found")
    assert _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))["customer_id"] is None
    # RLS : l'autre entreprise ne voit aucun évènement d'association, même en SQL.
    _ok(_associate(r.owner, order["id"], _customer(r)))
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=r.tenant)
        query = text(
            "SELECT count(*) FROM restaurant_order_events WHERE event_type = 'CUSTOMER_SET'"
        )
        assert db.execute(query).scalar_one() == 1
        db.rollback()


def test_association_and_settlement_are_serialized(resto: SimpleNamespace) -> None:
    """Association concurrente du règlement : soit le client est associé AVANT et la vente le
    porte, soit la commande est déjà réglée et l'association est refusée (``order_settled``) ;
    jamais une vente sans le client de la commande."""
    r = resto
    outcomes = set()
    for _ in range(4):
        customer = _customer(r)
        order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
        associate, settle = _race(
            [
                lambda o=order, c=customer: _associate(r.owner, o["id"], c),
                lambda o=order: _settle(r.owner, o["id"], payments=_cash("700")),
            ]
        )
        assert settle.status_code == 201, settle.text
        sale = _ok(r.owner.get(f"/sales/{settle.json()['sale_id']}"))
        state = _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))
        assert sale["customer_id"] == state["customer_id"]
        if associate.status_code == 200:
            assert sale["customer_id"] == customer
            outcomes.add("before")
        else:
            assert _code(associate) == (409, "order_settled")
            assert sale["customer_id"] is None
            outcomes.add("after")
    assert outcomes


def test_application_role_may_only_update_the_customer_of_an_open_order(
    resto: SimpleNamespace, app_engine: Engine
) -> None:
    """Droit SQL limité à ``customer_id`` (migration 0044) ; commande close : déclencheur."""
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    closed = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(_settle(r.owner, closed["id"], payments=_cash("700")), 201)
    _serve_all(r.owner, closed["id"])
    customer = _customer(r)
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=r.tenant)
        db.execute(
            text("UPDATE restaurant_orders SET customer_id = :c WHERE id = :o"),
            {"c": customer, "o": order["id"]},
        )
        db.rollback()
        with pytest.raises(Exception, match="permission denied"):
            db.execute(
                text("UPDATE restaurant_orders SET daily_number = 999 WHERE id = :o"),
                {"o": order["id"]},
            )
        db.rollback()
        with pytest.raises(Exception, match="restaurant_final_state"):
            db.execute(
                text("UPDATE restaurant_orders SET customer_id = :c WHERE id = :o"),
                {"c": customer, "o": closed["id"]},
            )
        db.rollback()


def test_migration_0044_downgrade_refuses_to_lose_an_association(
    resto: SimpleNamespace,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(_associate(r.owner, order["id"], _customer(r)))
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="1 association"):
        command.downgrade(_alembic(), "0043")
    with owner_engine.begin() as conn:
        check = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                "'ck_restaurant_order_events_restaurant_order_event_type'"
            )
        ).scalar_one()
        assert "CUSTOMER_SET" in check


# --- Référence d'un client sans consultation des clients (risque résiduel R2-E) ----------------

CUSTOMER_VIEW = "customers.customer.view"


def test_reference_without_customer_view_matches_creation_and_reveals_nothing_more(
    resto: SimpleNamespace,
) -> None:
    """Le Serveur (``order.create`` sans ``customers.customer.view``) peut, comme à la création
    (R2-B) et comme une vente (``sales.sale.create`` seul), désigner un client par son
    identifiant ; il ne consulte jamais le référentiel et n'obtient que ce que montre déjà
    une commande : identifiant et nom."""
    r = resto
    waiter_role = _ok(r.owner.post("/roles/from-template", json={"template_code": "waiter"}), 201)
    waiter = _join(r, "serveur2@ro-cust.example.com", [waiter_role["id"]])
    caps = _ok(waiter.api.get("/me/capabilities"))
    assert "restaurant.orders.order.create" in caps["permissions"]
    assert CUSTOMER_VIEW not in caps["permissions"]
    customer = _customer(r)
    # Référentiel des clients : jamais consultable (liste, fiche).
    assert waiter.api.get("/customers").status_code == 403
    assert waiter.api.get(f"/customers/{customer}").status_code == 403
    # Création et association : même règle (permission de l'opération, client actif).
    created = _ok(_order(waiter.api, r.site, [_line(r.menu.unit)], customer_id=customer), 201)
    other = _ok(_order(waiter.api, r.site, [_line(r.menu.unit)]), 201)
    associated = _ok(_associate(waiter.api, other["id"], customer))
    for order in (created, associated):
        exposed = {k for k in order if "customer" in k}
        assert exposed == {"customer_id", "customer_name"}
    [event] = [
        e
        for e in _ok(waiter.api.get(f"/restaurant/orders/{other['id']}/events"))
        if e["event_type"] == "CUSTOMER_SET"
    ]
    assert set(event["data"]) == {
        "customer_id",
        "customer_name",
        "previous_customer_id",
        "previous_customer_name",
    }
    # Journal d'audit (données complètes de l'opération) : jamais sans ``audit.log.view``.
    assert waiter.api.get("/audit-logs").status_code == 403


def test_refusals_never_leak_customer_data_without_customer_view(resto: SimpleNamespace) -> None:
    """Client désactivé : même refus pour tous ; son code n'est joint qu'à qui peut consulter
    les clients (création comme association)."""
    r = resto
    waiter_role = _ok(r.owner.post("/roles/from-template", json={"template_code": "waiter"}), 201)
    waiter = _join(r, "serveur3@ro-cust.example.com", [waiter_role["id"]])
    inactive = _customer(r)
    _ok(r.owner.post(f"/customers/{inactive}/deactivate"))
    order = _ok(_order(waiter.api, r.site, [_line(r.menu.unit)]), 201)
    for refused in (
        _associate(waiter.api, order["id"], inactive),
        _order(waiter.api, r.site, [_line(r.menu.unit)], customer_id=inactive),
    ):
        assert _code(refused) == (422, "customer_inactive")
        assert "customer_code" not in refused.json()
    owner_refusal = _associate(r.owner, order["id"], inactive)
    assert _code(owner_refusal) == (422, "customer_inactive")
    assert owner_refusal.json()["customer_code"].startswith("CLI-")
