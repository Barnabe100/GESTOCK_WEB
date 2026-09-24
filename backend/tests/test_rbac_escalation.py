"""Failles d'escalade identifiées lors de l'analyse RBAC (2026-09-24) : tests de non-régression.

1. Un administrateur limité à un site ne doit pas pouvoir, en sélectionnant ce site, accorder
   des permissions valables sur tout le tenant.
2. Un gestionnaire des membres limité à certains sites ne doit pas pouvoir accorder l'accès à
   d'autres sites (ni à tous les sites).
"""

from dataclasses import dataclass
from typing import Any

import pytest

from tests.conftest import PASSWORD, Api, login

TEMPORARY = "Provisoire-123"


@dataclass
class Tenant:
    owner: Api
    site_a: str
    site_b: str
    roles: dict[str, str]


@pytest.fixture
def tenant(provision: Any, api_for: Any) -> Tenant:
    t = provision("alpha", plan="ENTREPRISE")
    owner: Api = api_for("owner@alpha.example.com")
    site_b = owner.post("/sites", json={"name": "Dépôt", "code": "DEP", "kind": "warehouse"})
    assert site_b.status_code == 201, site_b.text
    roles = {r["template_code"] or r["name"]: r["id"] for r in owner.get("/roles").json()}
    return Tenant(owner, str(t.site_id), site_b.json()["id"], roles)


def _member(t: Tenant, client: Any, email: str, **access: Any) -> Api:
    response = t.owner.post(
        "/members",
        json={"email": email, "full_name": email, "password": TEMPORARY, **access},
    )
    assert response.status_code == 201, response.text
    token = login(client, email, TEMPORARY).json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def _new_member_body(email: str, **access: Any) -> dict[str, Any]:
    return {"email": email, "full_name": email, "password": TEMPORARY, **access}


# --- Faille 1 : rôle limité à un site utilisé pour accorder des droits sur tout le tenant --------


def test_site_scoped_admin_cannot_grant_tenant_wide_permissions(
    tenant: Tenant, client: Any
) -> None:
    site_admin = _member(
        tenant,
        client,
        "admin-boutique@example.com",
        roles=[{"role_id": tenant.roles["administrator"], "site_id": tenant.site_a}],
        site_ids=[tenant.site_a],
    )
    site_admin.site_id = tenant.site_a  # type: ignore[assignment]

    # Un rôle n'a pas de portée : le créer revient à accorder ses permissions partout.
    super_role = site_admin.post(
        "/roles", json={"name": "Super", "permissions": ["organization.site.manage"]}
    )
    assert super_role.status_code == 403, super_role.text
    assert super_role.json()["code"] == "permission_escalation"

    # Attribuer l'Administrateur sur tout le tenant à un complice : refusé.
    accomplice = site_admin.post(
        "/members",
        json=_new_member_body(
            "complice@example.com",
            roles=[{"role_id": tenant.roles["administrator"]}],
            site_ids=[tenant.site_a],
        ),
    )
    assert accomplice.status_code == 403, accomplice.text
    assert accomplice.json()["code"] == "permission_escalation"

    # Sur son propre site, il peut attribuer ce qu'il détient sur ce site.
    local = site_admin.post(
        "/members",
        json=_new_member_body(
            "vendeur-boutique@example.com",
            roles=[{"role_id": tenant.roles["viewer"], "site_id": tenant.site_a}],
            site_ids=[tenant.site_a],
        ),
    )
    assert local.status_code == 201, local.text
    # Mais pas pour un autre site.
    other_site = site_admin.post(
        "/members",
        json=_new_member_body(
            "vendeur-depot@example.com",
            roles=[{"role_id": tenant.roles["viewer"], "site_id": tenant.site_b}],
            site_ids=[tenant.site_b],
        ),
    )
    assert other_site.status_code == 403, other_site.text


# --- Faille 2 : accès à des sites que l'acteur ne possède pas -----------------------------------


def test_member_manager_cannot_grant_sites_beyond_own(tenant: Tenant, client: Any) -> None:
    hr_role = tenant.owner.post(
        "/roles",
        json={
            "name": "RH boutique",
            "permissions": [
                "users.member.view",
                "users.member.manage",
                "users.role.view",
                "catalog.article.view",
            ],
        },
    ).json()
    hr = _member(
        tenant,
        client,
        "rh@example.com",
        roles=[{"role_id": hr_role["id"]}],
        site_ids=[tenant.site_a],
    )
    viewer_like = tenant.owner.post(
        "/roles", json={"name": "Lecture catalogue", "permissions": ["catalog.article.view"]}
    ).json()["id"]

    all_sites = hr.post(
        "/members",
        json=_new_member_body(
            "tous-sites@example.com", roles=[{"role_id": viewer_like}], all_sites=True
        ),
    )
    assert all_sites.status_code == 403, all_sites.text
    assert all_sites.json()["code"] == "site_escalation"

    foreign_site = hr.post(
        "/members",
        json=_new_member_body(
            "depot@example.com", roles=[{"role_id": viewer_like}], site_ids=[tenant.site_b]
        ),
    )
    assert foreign_site.status_code == 403, foreign_site.text
    assert foreign_site.json()["code"] == "site_escalation"

    own_site = hr.post(
        "/members",
        json=_new_member_body(
            "boutique@example.com", roles=[{"role_id": viewer_like}], site_ids=[tenant.site_a]
        ),
    )
    assert own_site.status_code == 201, own_site.text

    # Un membre ayant accès à d'autres sites échappe à son périmètre : pas de modification.
    wide = tenant.owner.post(
        "/members",
        json=_new_member_body(
            "large@example.com", roles=[{"role_id": viewer_like}], all_sites=True
        ),
    ).json()
    edit = hr.patch(f"/members/{wide['id']}", json={"status": "suspended"})
    assert edit.status_code == 403, edit.text
    assert edit.json()["code"] == "site_escalation"
