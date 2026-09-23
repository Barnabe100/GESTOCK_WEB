"""Isolation multi-tenant vue de l'API : un utilisateur du tenant A ne peut ni voir ni
modifier les données du tenant B, quel que soit l'identifiant qu'il fournit."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.security import AccessClaims, create_access_token
from tests.conftest import Api


@dataclass
class World:
    a: Any
    b: Any
    api_a: Api
    api_b: Api
    b_role_id: str
    b_member_id: str
    b_extra_site_id: str


@pytest.fixture
def world(provision: Any, api_for: Any) -> World:
    a = provision("alpha")
    b = provision("beta")
    api_a = api_for("owner@alpha.example.com")
    api_b = api_for("owner@beta.example.com")
    role = api_b.post("/roles", json={"name": "Secret B", "permissions": ["audit.log.view"]})
    site = api_b.post("/sites", json={"name": "Dépôt B", "code": "DEPOTB"})
    member = api_b.post(
        "/members",
        json={
            "email": "salarie@beta.example.com",
            "full_name": "Salarié B",
            "password": "Provisoire-123",
            "roles": [{"role_id": role.json()["id"]}],
            "all_sites": True,
        },
    )
    assert member.status_code == 201, member.text
    return World(
        a=a,
        b=b,
        api_a=api_a,
        api_b=api_b,
        b_role_id=role.json()["id"],
        b_member_id=member.json()["id"],
        b_extra_site_id=site.json()["id"],
    )


LIST_ENDPOINTS = ["/sites", "/roles", "/members", "/modules"]


@pytest.mark.parametrize("path", LIST_ENDPOINTS)
def test_lists_only_contain_own_tenant_data(world: World, path: str) -> None:
    body_a = world.api_a.get(path).json()
    body_b = world.api_b.get(path).json()
    ids_b = {item.get("id") for item in body_b if item.get("id")}
    ids_a = {item.get("id") for item in body_a if item.get("id")}
    assert ids_a.isdisjoint(ids_b)


def test_member_list_does_not_leak_other_tenant_users(world: World) -> None:
    emails = {m["email"] for m in world.api_a.get("/members").json()}
    assert emails == {"owner@alpha.example.com"}


def test_audit_logs_are_isolated(world: World) -> None:
    items = world.api_a.get("/audit-logs").json()["items"]
    assert items
    assert all(i["action"] != "member.created" for i in items)
    assert not any(i["entity_id"] == world.b_member_id for i in items)


def test_tenant_endpoint_returns_own_tenant(world: World) -> None:
    assert world.api_a.get("/tenant").json()["id"] == str(world.a.tenant_id)


@pytest.mark.parametrize(
    ("method", "path_template", "body"),
    [
        ("get", "/sites/{site}", None),
        ("patch", "/sites/{site}", {"name": "piraté"}),
        ("get", "/roles/{role}", None),
        ("patch", "/roles/{role}", {"name": "piraté"}),
        ("delete", "/roles/{role}", None),
        ("get", "/members/{member}", None),
        ("patch", "/members/{member}", {"status": "suspended"}),
    ],
)
def test_other_tenant_resources_are_not_found(
    world: World, method: str, path_template: str, body: dict[str, Any] | None
) -> None:
    path = path_template.format(
        site=world.b_extra_site_id, role=world.b_role_id, member=world.b_member_id
    )
    kwargs = {"json": body} if body is not None else {}
    response = getattr(world.api_a, method)(path, **kwargs)
    assert response.status_code == 404, response.text
    # Et rien n'a changé côté B.
    assert world.api_b.get("/sites/" + world.b_extra_site_id).json()["name"] == "Dépôt B"
    assert world.api_b.get("/roles/" + world.b_role_id).json()["name"] == "Secret B"
    assert world.api_b.get("/members/" + world.b_member_id).json()["status"] == "active"


def test_cannot_use_other_tenant_site_as_context(world: World) -> None:
    world.api_a.site_id = world.b.site_id
    response = world.api_a.get("/me/capabilities")
    assert response.status_code == 403
    assert response.json()["code"] == "site_access_denied"


def test_cannot_assign_other_tenant_role_or_site(world: World) -> None:
    with_role = world.api_a.post(
        "/members",
        json={
            "email": "x@alpha.example.com",
            "full_name": "X",
            "password": "Provisoire-123",
            "roles": [{"role_id": world.b_role_id}],
        },
    )
    assert with_role.status_code == 422
    assert with_role.json()["code"] == "role_not_found"
    with_site = world.api_a.post(
        "/members",
        json={
            "email": "y@alpha.example.com",
            "full_name": "Y",
            "password": "Provisoire-123",
            "site_ids": [str(world.b.site_id)],
        },
    )
    assert with_site.status_code == 422
    assert with_site.json()["code"] == "site_not_found"


def test_forged_token_for_foreign_tenant_is_refused(
    world: World, client: TestClient, app: Any
) -> None:
    """Même un jeton correctement signé mais lié à un tenant dont l'utilisateur n'est pas
    membre est refusé : l'appartenance est revérifiée à chaque requête."""
    me = world.api_a.get("/me").json()
    session_id = _session_id(world.api_a)
    forged = create_access_token(
        AccessClaims(
            user_id=uuid.UUID(me["user"]["id"]),
            session_id=session_id,
            tenant_id=world.b.tenant_id,
        ),
        app.state.settings,
        datetime.now(UTC),
    )
    response = Api(client, forged).get("/me/capabilities")
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"
    assert Api(client, forged).get("/sites").status_code == 403


def test_refresh_cannot_switch_to_foreign_tenant(world: World, client: TestClient) -> None:
    # Le cookie du client est celui du dernier login (owner B dans la fixture).
    response = client.post("/api/v1/auth/refresh", json={"tenant_id": str(world.a.tenant_id)})
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


def test_suspended_membership_loses_access(world: World, api_for: Any) -> None:
    member = api_for("salarie@beta.example.com", password="Provisoire-123")
    member.post(
        "/me/password",
        json={"current_password": "Provisoire-123", "new_password": "Definitif-456"},
    )
    assert member.get("/me/capabilities").status_code == 200
    world.api_b.patch("/members/" + world.b_member_id, json={"status": "suspended"})
    assert member.get("/me/capabilities").json()["code"] == "tenant_access_denied"


def _session_id(api: Api) -> uuid.UUID:
    import jwt

    payload = jwt.decode(api.token, options={"verify_signature": False})
    return uuid.UUID(payload["sid"])
