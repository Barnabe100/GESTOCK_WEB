"""Module Clients (Phase 2.3) : CRUD, validation, référence CLI, recherche, désactivation,
audit, RBAC, plan et abonnement, isolation API et SQL (rôle applicatif réel, RLS)."""

import threading
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.modules.customers.api import get_customer_ref
from tests.conftest import PASSWORD, Api, login

TEMPORARY = "Provisoire-123"


@pytest.fixture
def owner(provision: Any, api_for: Any) -> Api:
    provision("alpha", profile="retail.quincaillerie", plan="ENTREPRISE")
    api: Api = api_for("owner@alpha.example.com")
    return api


def _create(api: Api, **fields: Any) -> dict[str, Any]:
    body = {"customer_type": "INDIVIDUAL", "name": "Awa Traoré", **fields}
    response = api.post("/customers", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


def _member(owner: Api, client: Any, email: str, template: str) -> Api:
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    created = owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": TEMPORARY,
            "roles": [{"role_id": roles[template]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, TEMPORARY).json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


# --- Création et validation --------------------------------------------------------------------


def test_create_customer(owner: Api) -> None:
    first = _create(
        owner,
        name="  Awa Traoré ",
        phone="+226 70-11-22-33",
        phone2="70.11.22.34",
        email="Awa@Example.com",
        city=" Ouagadougou ",
        notes="",
    )
    assert first["code"] == "CLI-000001"
    assert first["is_active"] is True and first["customer_type"] == "INDIVIDUAL"
    assert first["name"] == "Awa Traoré" and first["city"] == "Ouagadougou"
    assert (first["phone"], first["phone2"]) == ("+22670112233", "70112234")
    assert first["email"] == "Awa@example.com"  # domaine normalisé
    assert first["notes"] is None and first["credit_limit"] is None

    business = _create(
        owner,
        customer_type="BUSINESS",
        name="Quincaillerie du Centre",
        legal_name="QDC SARL",
        tax_id="00012345A",
        credit_limit="150000",
    )
    assert business["code"] == "CLI-000002"
    assert business["credit_limit"] == "150000.00"  # chaîne décimale (jamais de float)
    assert owner.get(f"/customers/{business['id']}").json()["legal_name"] == "QDC SARL"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"customer_type": "INDIVIDUAL"}, "name"),
        ({"customer_type": "INDIVIDUAL", "name": "   "}, "name"),
        ({"name": "Sans type"}, "customer_type"),
        ({"customer_type": "VIP", "name": "X"}, "customer_type"),
        ({"customer_type": "INDIVIDUAL", "name": "X" * 151}, "name"),
        ({"customer_type": "INDIVIDUAL", "name": "X", "email": "pas-un-email"}, "email"),
        ({"customer_type": "INDIVIDUAL", "name": "X", "phone": "appelez-moi"}, "phone"),
        ({"customer_type": "INDIVIDUAL", "name": "X", "notes": "n" * 1001}, "notes"),
        ({"customer_type": "BUSINESS", "name": "X", "legal_name": "L" * 201}, "legal_name"),
        ({"customer_type": "INDIVIDUAL", "name": "X", "credit_limit": "-1"}, "credit_limit"),
        ({"customer_type": "INDIVIDUAL", "name": "X", "credit_limit": "1.234"}, "credit_limit"),
    ],
)
def test_create_validation(owner: Api, body: dict[str, Any], field: str) -> None:
    response = owner.post("/customers", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert field in str(response.json())
    assert owner.get("/customers").json()["total"] == 0


def test_code_is_server_side_and_unique(owner: Api, owner_db: Session) -> None:
    created = _create(owner, code="HACK-1")  # champ ignoré : le code est attribué
    assert created["code"] == "CLI-000001"
    patched = owner.patch(f"/customers/{created['id']}", json={"code": "CLI-999999"})
    assert patched.json()["code"] == "CLI-000001"  # immuable
    # Contrainte en base : (tenant_id, code) unique.
    tenant_id = owner_db.execute(text("SELECT tenant_id FROM customers")).scalar_one()
    with pytest.raises(IntegrityError):
        owner_db.execute(
            text(
                "INSERT INTO customers (id, tenant_id, code, customer_type, name, is_active) "
                "VALUES (:id, :tenant, 'CLI-000001', 'INDIVIDUAL', 'Doublon', true)"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_id},
        )
    owner_db.rollback()


def test_phone_and_email_are_not_unique(owner: Api) -> None:
    """Plusieurs clients (ex. membres d'une même entreprise) peuvent partager un contact."""
    _create(owner, name="Awa", phone="70112233", email="contact@qdc.bf")
    second = owner.post(
        "/customers",
        json={
            "customer_type": "INDIVIDUAL",
            "name": "Moussa",
            "phone": "70 11 22 33",
            "email": "contact@qdc.bf",
        },
    )
    assert second.status_code == 201


def test_concurrent_creations_get_distinct_codes(owner: Api, app: Any) -> None:
    barrier = threading.Barrier(4)
    codes: list[str] = []

    def create(index: int) -> None:
        with TestClient(app) as client:
            api = Api(client, owner.token)
            barrier.wait()
            response = api.post(
                "/customers", json={"customer_type": "INDIVIDUAL", "name": f"Client {index}"}
            )
            codes.append(response.json()["code"])

    threads = [threading.Thread(target=create, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(codes) == [f"CLI-00000{i}" for i in range(1, 5)]


# --- Lecture, recherche, filtres ---------------------------------------------------------------


def test_list_search_filters_and_sort(owner: Api) -> None:
    awa = _create(owner, name="Awa Traoré", phone="70112233", email="awa@example.com")
    _create(
        owner,
        customer_type="BUSINESS",
        name="Quincaillerie du Centre",
        legal_name="Société Burkinabè de Matériaux",
        phone="25300000",
    )
    moussa = _create(owner, name="Moussa Ouédraogo", phone2="76000000")
    owner.post(f"/customers/{moussa['id']}/deactivate")

    def names(**params: str) -> list[str]:
        response = owner.get("/customers", params=params)
        assert response.status_code == 200, response.text
        return [c["name"] for c in response.json()["items"]]

    assert names() == ["Awa Traoré", "Moussa Ouédraogo", "Quincaillerie du Centre"]
    assert names(search="AWA") == ["Awa Traoré"]  # casse ignorée
    assert names(search="cli-000002") == ["Quincaillerie du Centre"]  # code
    assert names(search="burkinabè") == ["Quincaillerie du Centre"]  # raison sociale
    assert names(search="70 11 22") == ["Awa Traoré"]  # téléphone saisi avec espaces
    assert names(search="76000") == ["Moussa Ouédraogo"]  # téléphone secondaire
    assert names(search="example.com") == ["Awa Traoré"]  # email
    assert names(search="%") == []  # caractères spéciaux échappés
    assert names(type="BUSINESS") == ["Quincaillerie du Centre"]
    assert names(status="inactive") == ["Moussa Ouédraogo"]
    assert names(status="active", type="INDIVIDUAL") == ["Awa Traoré"]
    assert names(sort="-code") == ["Moussa Ouédraogo", "Quincaillerie du Centre", "Awa Traoré"]
    page = owner.get("/customers", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3 and [c["name"] for c in page["items"]] == ["Moussa Ouédraogo"]
    assert owner.get("/customers", params={"sort": "phone"}).json()["code"] == "invalid_sort"
    assert owner.get("/customers", params={"type": "VIP"}).status_code == 422
    assert owner.get(f"/customers/{awa['id']}").json()["code"] == "CLI-000001"
    missing = owner.get(f"/customers/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["code"] == "customer_not_found"


# --- Modification, désactivation, audit --------------------------------------------------------


def test_update_deactivate_reactivate_and_audit(owner: Api) -> None:
    customer = _create(owner, name="Awa", phone="70112233", city="Bobo")
    updated = owner.patch(
        f"/customers/{customer['id']}",
        json={"name": "Awa Traoré", "city": "", "customer_type": "BUSINESS"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["name"], body["city"], body["customer_type"]) == ("Awa Traoré", None, "BUSINESS")
    assert body["phone"] == "70112233"  # champ absent : inchangé
    invalid = owner.patch(f"/customers/{customer['id']}", json={"name": None})
    assert invalid.status_code == 400 and invalid.json()["code"] == "validation_error"

    deactivated = owner.post(f"/customers/{customer['id']}/deactivate").json()
    assert deactivated["is_active"] is False
    # Toujours consultable (historique), et exclu des clients sélectionnables.
    assert owner.get(f"/customers/{customer['id']}").status_code == 200
    assert owner.get("/customers", params={"status": "active"}).json()["total"] == 0
    assert owner.post(f"/customers/{customer['id']}/deactivate").json()["is_active"] is False
    assert owner.post(f"/customers/{customer['id']}/activate").json()["is_active"] is True

    items = owner.get("/audit-logs", params={"action": "customer."}).json()["items"]
    assert [i["action"] for i in items] == [
        "customer.activated",
        "customer.deactivated",  # une seule fois : la 2e désactivation ne change rien
        "customer.updated",
        "customer.created",
    ]
    change = items[2]["data"]
    assert change["code"] == "CLI-000001"
    assert change["name"] == {"before": "Awa", "after": "Awa Traoré"}
    assert change["city"] == {"before": "Bobo", "after": None}
    assert change["customer_type"] == {"before": "INDIVIDUAL", "after": "BUSINESS"}
    assert items[3]["data"] == {
        "code": "CLI-000001",
        "name": "Awa",
        "customer_type": "INDIVIDUAL",
    }
    assert all(i["entity_type"] == "customer" for i in items)


# --- RBAC, plan, abonnement --------------------------------------------------------------------


def test_permissions_by_base_role(owner: Api, client: Any) -> None:
    customer = _create(owner)
    admin = _member(owner, client, "admin@example.com", "administrator")
    manager = _member(owner, client, "gestion@example.com", "manager")
    seller = _member(owner, client, "vendeur@example.com", "seller")
    viewer = _member(owner, client, "consultant@example.com", "viewer")
    new = {"customer_type": "INDIVIDUAL", "name": "Nouveau"}
    url = f"/customers/{customer['id']}"

    for api in (admin, manager):
        assert api.post("/customers", json=new).status_code == 201
        assert api.patch(url, json={"city": "Ouaga"}).status_code == 200
        assert api.post(f"{url}/deactivate").status_code == 200
        assert api.post(f"{url}/activate").status_code == 200

    # Vendeur et Consultant : consultation (et sélection future) seulement.
    for api in (seller, viewer):
        assert api.get("/customers").status_code == 200
        assert api.get(url).status_code == 200
        for response in (
            api.post("/customers", json=new),
            api.patch(url, json={"city": "X"}),
            api.post(f"{url}/deactivate"),
            api.post(f"{url}/activate"),
        ):
            assert response.status_code == 403
            assert response.json()["code"] == "permission_denied"

    custom = owner.post(
        "/roles", json={"name": "Stock seul", "permissions": ["stock.level.view"]}
    ).json()
    owner.post(
        "/members",
        json={
            "email": "stock@example.com",
            "full_name": "Stock",
            "password": TEMPORARY,
            "roles": [{"role_id": custom["id"]}],
        },
    )
    token = login(client, "stock@example.com", TEMPORARY).json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )
    stock_only = Api(client, login(client, "stock@example.com").json()["access_token"])
    assert stock_only.get("/customers").json()["code"] == "permission_denied"
    caps = stock_only.get("/me/capabilities").json()
    assert not any(p.startswith("customers.") for p in caps["permissions"])


def test_base_role_templates_include_customers(owner: Api) -> None:
    roles = {r["template_code"]: r for r in owner.get("/roles").json() if r["template_code"]}
    customer_perms = {
        "customers.customer.view",
        "customers.customer.create",
        "customers.customer.update",
        "customers.customer.status",
    }
    assert customer_perms <= set(roles["administrator"]["permission_codes"])
    assert customer_perms <= set(roles["manager"]["permission_codes"])
    seller = {p for p in roles["seller"]["permission_codes"] if p.startswith("customers.")}
    viewer = {p for p in roles["viewer"]["permission_codes"] if p.startswith("customers.")}
    assert seller == viewer == {"customers.customer.view"}


def test_module_and_subscription_policies(owner: Api, owner_db: Session) -> None:
    _create(owner)
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    assert owner.get("/customers").status_code == 200  # consultation conservée
    blocked = owner.post("/customers", json={"customer_type": "INDIVIDUAL", "name": "X"})
    assert blocked.status_code == 403 and blocked.json()["code"] == "subscription_restricted"

    owner_db.execute(
        text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'customers'")
    )
    owner_db.commit()
    unavailable = owner.get("/customers")
    assert unavailable.status_code == 403 and unavailable.json()["code"] == "module_unavailable"


# --- Multi-tenant -------------------------------------------------------------------------------


def test_isolation_between_tenants_api(owner: Api, provision: Any, api_for: Any) -> None:
    a_customer = _create(owner, name="Client de A", phone="70112233")
    provision("beta")
    beta: Api = api_for("owner@beta.example.com")
    b_customer = _create(beta, name="Client de B")
    # Chaque tenant a sa propre numérotation.
    assert b_customer["code"] == "CLI-000001"
    assert [c["name"] for c in beta.get("/customers").json()["items"]] == ["Client de B"]
    assert [c["name"] for c in owner.get("/customers").json()["items"]] == ["Client de A"]
    assert beta.get("/customers", params={"search": "70112233"}).json()["total"] == 0
    url = f"/customers/{a_customer['id']}"
    for response in (
        beta.get(url),
        beta.patch(url, json={"name": "Piraté"}),
        beta.post(f"{url}/deactivate"),
        beta.post(f"{url}/activate"),
    ):
        assert response.status_code == 404
        assert response.json()["code"] == "customer_not_found"
    after = owner.get(url).json()
    assert after["name"] == "Client de A" and after["is_active"] is True


def test_isolation_with_app_role_and_rls(
    owner: Api, provision: Any, api_for: Any, app_engine: Engine
) -> None:
    a_customer = _create(owner)
    b = provision("beta")
    tenant_a = owner.get("/me/capabilities").json()["tenant"]["id"]

    with create_session_factory(app_engine)() as db:
        # Sans contexte : aucune ligne.
        assert db.execute(text("SELECT count(*) FROM customers")).scalar_one() == 0
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=b.tenant_id)
        assert db.execute(text("SELECT count(*) FROM customers")).scalar_one() == 0
        updated = db.execute(
            text("UPDATE customers SET name = 'Piraté' WHERE id = :id"), {"id": a_customer["id"]}
        )
        assert updated.rowcount == 0
        # API publique : un client d'un autre tenant n'existe pas.
        assert get_customer_ref(db, uuid.UUID(a_customer["id"])) is None
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO customers (id, tenant_id, code, customer_type, name, is_active) "
                    "VALUES (:id, :tenant, 'CLI-X', 'INDIVIDUAL', 'Intrus', true)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_a},
            )
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=uuid.UUID(tenant_a))
        ref = get_customer_ref(db, uuid.UUID(a_customer["id"]))
        assert ref is not None and ref.code == "CLI-000001" and ref.is_active is True
        # Jamais de suppression physique (droit DELETE non accordé).
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM customers"))
