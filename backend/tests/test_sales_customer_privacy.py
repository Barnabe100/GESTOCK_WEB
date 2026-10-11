"""Confidentialité du client désactivé dans les ventes (correction ciblée après R2-F).

Le refus ``customer_inactive`` ne joint le code du client (``customer_code``) qu'à un membre
détenant ``customers.customer.view`` SUR LE SITE de la vente — règle commune au module Clients
(``customers.api.require_active_customer``), déjà appliquée aux commandes de restauration.
Code d'erreur, message, permissions d'opération et règles des ventes inchangés.
"""

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests import stock_helpers as sh
from tests.conftest import PASSWORD, Api, login
from tests.stock_helpers import World

SELLING = [
    "sales.sale.view",
    "sales.sale.create",
    "sales.sale.update",
    "sales.sale.validate",
    "sales.payment.create",
    "pos.terminal.use",
]
INACTIVE_DETAIL = "Ce client est désactivé"


def _member(w: World, client: TestClient, email: str, view_customers_on: str | None) -> Api:
    """Membre vendant sur tous les sites ; consultation des clients limitée au site
    ``view_customers_on`` (aucune si ``None``) — rôles personnalisés, jamais un nom de rôle."""
    selling = w.owner.post("/roles", json={"name": f"Vente {email}", "permissions": SELLING})
    assert selling.status_code == 201, selling.text
    roles = [{"role_id": selling.json()["id"]}]
    if view_customers_on is not None:
        viewing = w.owner.post(
            "/roles",
            json={"name": f"Clients {email}", "permissions": ["customers.customer.view"]},
        )
        assert viewing.status_code == 201, viewing.text
        roles.append({"role_id": viewing.json()["id"], "site_id": view_customers_on})
    created = w.owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": roles,
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    temporary = Api(client, login(client, email, "Provisoire-123").json()["access_token"])
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _inactive_customer(w: World) -> dict[str, Any]:
    customer = w.owner.post("/customers", json={"customer_type": "INDIVIDUAL", "name": "Parti"})
    assert customer.status_code == 201, customer.text
    assert w.owner.post(f"/customers/{customer.json()['id']}/deactivate").status_code == 200
    return dict(customer.json())


def _draft(api: Api, w: World, site: str, customer_id: str) -> Any:
    return api.post(
        "/sales",
        json={
            "site_id": site,
            "customer_id": customer_id,
            "lines": [{"article_id": w.articles[0], "quantity": "1"}],
        },
    )


def _assert_inactive(response: Any, code: str | None) -> None:
    """Refus inchangé (statut, code, message) ; ``customer_code`` présent seulement si
    attendu."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert (body["code"], body["detail"]) == ("customer_inactive", INACTIVE_DETAIL)
    if code is None:
        assert "customer_code" not in body
        assert "CLI-" not in response.text
    else:
        assert body["customer_code"] == code


def test_draft_refusal_reveals_the_code_only_with_customer_view_on_the_site(
    world: World, client: TestClient
) -> None:
    inactive = _inactive_customer(world)
    blind = _member(world, client, "aveugle@alpha.example.com", None)
    depot_only = _member(world, client, "depot@alpha.example.com", world.site2)
    # Sans consultation des clients : jamais de code, sur aucun site.
    _assert_inactive(_draft(blind, world, world.site, inactive["id"]), None)
    _assert_inactive(_draft(blind, world, world.site2, inactive["id"]), None)
    # Consultation limitée au dépôt : code au dépôt seulement.
    _assert_inactive(_draft(depot_only, world, world.site, inactive["id"]), None)
    _assert_inactive(_draft(depot_only, world, world.site2, inactive["id"]), inactive["code"])
    # Comportement autorisé existant conservé (consultation partout).
    _assert_inactive(_draft(world.owner, world, world.site, inactive["id"]), inactive["code"])
    # Rien n'a été créé.
    assert world.owner.get("/sales").json()["total"] == 0


def test_validation_and_pos_refusals_follow_the_same_rule(world: World, client: TestClient) -> None:
    sh.validated_entry(world, [(0, "10", "100")])
    blind = _member(world, client, "caisse@alpha.example.com", None)
    customer = world.owner.post(
        "/customers", json={"customer_type": "INDIVIDUAL", "name": "Bientôt parti"}
    ).json()
    draft = _draft(blind, world, world.site, customer["id"])
    assert draft.status_code == 201, draft.text
    world.owner.post(f"/customers/{customer['id']}/deactivate")
    # Validation : client désactivé entre-temps.
    _assert_inactive(blind.post(f"/sales/{draft.json()['id']}/validate"), None)
    _assert_inactive(world.owner.post(f"/sales/{draft.json()['id']}/validate"), customer["code"])
    # Point de vente : même règle (création et validation en une transaction).
    checkout = {
        "site_id": world.site,
        "customer_id": customer["id"],
        "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        "payments": [{"amount": "150", "method": "CASH"}],
        "idempotency_key": str(uuid.uuid4()),
    }
    _assert_inactive(blind.post("/pos/checkout", json=checkout), None)
    checkout["idempotency_key"] = str(uuid.uuid4())
    _assert_inactive(world.owner.post("/pos/checkout", json=checkout), customer["code"])


def test_isolation_and_operation_permissions_are_unchanged(
    world: World, client: TestClient, provision: Any, api_for: Any
) -> None:
    inactive = _inactive_customer(world)
    # Autre entreprise : le client n'existe pas pour elle (aucune donnée révélée).
    provision("beta", profile="retail.quincaillerie", plan="ENTREPRISE")
    stranger = api_for("owner@beta.example.com")
    stranger_site = stranger.get("/sites").json()[0]["id"]
    refused = stranger.post(
        "/sales",
        json={
            "site_id": stranger_site,
            "customer_id": inactive["id"],
            "lines": [{"article_id": world.articles[0], "quantity": "1"}],
        },
    )
    assert refused.status_code == 422
    assert refused.json()["code"] in ("customer_not_found", "article_not_found")
    assert inactive["code"] not in refused.text
    # Permissions d'opération inchangées : sans ``sales.sale.create``, refus avant tout
    # contrôle du client.
    viewer = sh.member(world, client, "consultant@alpha.example.com", "viewer", all_sites=True)
    forbidden = _draft(viewer, world, world.site, inactive["id"])
    assert forbidden.status_code == 403
    assert inactive["code"] not in forbidden.text
    # Membre limité à la boutique : le dépôt reste hors de sa portée (refus de site).
    limited = sh.member(
        world, client, "boutique@alpha.example.com", "seller", site_ids=[world.site]
    )
    out_of_scope = _draft(limited, world, world.site2, inactive["id"])
    assert out_of_scope.status_code in (403, 404)
    assert inactive["code"] not in out_of_scope.text
