"""Phase 3.2-E — Délégation RBAC (ADR-0030) : ce qu'un utilisateur peut déléguer est calculé
par le serveur (``/permissions/delegable``, ``/roles/delegable``, ``RoleOut.delegable``) sur la
même base que les contrôles anti-escalade ; comparaison limitée aux permissions qu'un rôle
accorde réellement dans l'offre du tenant ; permissions hors offre conservées ; portée tenant /
site ; isolation (API et RLS)."""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.platform.subscriptions.service import change_plan
from tests.conftest import PASSWORD, Api, login

TEMPORARY = "Provisoire-Deleg-1"


@dataclass
class Tenant:
    id: uuid.UUID
    owner: Api
    site_a: str
    site_b: str
    roles: dict[str, str]


def _tenant(provision: Any, api_for: Any, slug: str, plan: str = "ENTREPRISE") -> Tenant:
    t = provision(slug, plan=plan)
    owner: Api = api_for(f"owner@{slug}.example.com")
    site_b = str(t.site_id)
    if plan == "ENTREPRISE":
        created = owner.post("/sites", json={"name": "Dépôt", "code": "DEP", "kind": "warehouse"})
        assert created.status_code == 201, created.text
        site_b = created.json()["id"]
    roles = {r["template_code"] or r["name"]: r["id"] for r in owner.get("/roles").json()}
    return Tenant(t.tenant_id, owner, str(t.site_id), site_b, roles)


def _member(client: TestClient, t: Tenant, email: str, **access: Any) -> Api:
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


def _codes(api: Api, path: str, **params: Any) -> set[str]:
    response = api.get(path, params=params)
    assert response.status_code == 200, response.text
    return {p["code"] for p in response.json()}


def _role_ids(api: Api, **params: Any) -> set[str]:
    response = api.get("/roles/delegable", params=params)
    assert response.status_code == 200, response.text
    return {r["id"] for r in response.json()}


# --- Correctif : l'offre du tenant borne la comparaison ----------------------------------------


def test_admin_can_delegate_base_roles_on_a_plan_without_optional_features(
    provision: Any, api_for: Any, client: TestClient, owner_db: Session
) -> None:
    """Régression : sur STANDARD (sans ``stock.transfers``), un Administrateur non
    propriétaire était refusé (``permission_escalation``) en attribuant Gestionnaire ou
    Administrateur, à cause de permissions que personne ne peut détenir dans ce tenant."""
    t = _tenant(provision, api_for, "petit", plan="STANDARD")
    admin = _member(
        client,
        t,
        "admin2@petit.example.com",
        roles=[{"role_id": t.roles["administrator"]}],
        all_sites=True,
    )
    # Limite STANDARD : 5 utilisateurs (propriétaire, admin2 et trois ajouts).
    for template in ("administrator", "manager", "seller"):
        response = admin.post(
            "/members",
            json={
                "email": f"{template}@petit.example.com",
                "full_name": template,
                "password": TEMPORARY,
                "roles": [{"role_id": t.roles[template]}],
                "all_sites": True,
            },
        )
        assert response.status_code == 201, (template, response.text)
    # Le rôle attribué n'accorde toujours rien hors de l'offre.
    manager_email = "manager@petit.example.com"
    token = login(client, manager_email, TEMPORARY).json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )
    caps = (
        Api(client, login(client, manager_email).json()["access_token"])
        .get("/me/capabilities")
        .json()
    )
    gated = {"stock.transfer.create", "stock.transfer.update", "stock.transfer.validate"}
    assert not gated & set(caps["permissions"])


# --- Projections de délégation ------------------------------------------------------------------


def test_delegable_permissions_follow_the_actor(
    provision: Any, api_for: Any, client: TestClient
) -> None:
    t = _tenant(provision, api_for, "alpha")
    offer = _codes(t.owner, "/permissions")
    assert _codes(t.owner, "/permissions/delegable") == offer
    assert _role_ids(t.owner) == set(t.roles.values())

    admin = _member(
        client,
        t,
        "admin2@alpha.example.com",
        roles=[{"role_id": t.roles["administrator"]}],
        all_sites=True,
    )
    assert _codes(admin, "/permissions/delegable") == offer

    hr_perms = [
        "users.member.view",
        "users.member.manage",
        "users.role.view",
        "catalog.article.view",
    ]
    hr_role = t.owner.post("/roles", json={"name": "RH", "permissions": hr_perms}).json()
    hr = _member(
        client, t, "rh@alpha.example.com", roles=[{"role_id": hr_role["id"]}], all_sites=True
    )
    assert _codes(hr, "/permissions/delegable") == set(hr_perms)
    # Rôles attribuables : ceux dont toutes les permissions sont délégables (ici : le sien).
    assert _role_ids(hr) == {hr_role["id"]}
    flags = {r["id"]: r["delegable"] for r in hr.get("/roles").json()}
    assert flags[hr_role["id"]] is True
    assert flags[t.roles["seller"]] is False and flags[t.roles["administrator"]] is False

    # Sans gestion des rôles ni des membres : pas de projection.
    seller = _member(
        client,
        t,
        "vendeur@alpha.example.com",
        roles=[{"role_id": t.roles["seller"]}],
        all_sites=True,
    )
    for path in ("/permissions/delegable", "/roles/delegable"):
        assert seller.get(path).json()["code"] == "permission_denied"


def test_projection_matches_enforcement(provision: Any, api_for: Any, client: TestClient) -> None:
    """Chaque permission annoncée délégable est acceptée ; toute autre est refusée."""
    t = _tenant(provision, api_for, "alpha")
    hr_perms = [
        "users.role.view",
        "users.role.manage",
        "catalog.article.view",
        "catalog.category.view",
        "sales.sale.view",
    ]
    hr_role = t.owner.post("/roles", json={"name": "Composeur", "permissions": hr_perms}).json()
    hr = _member(
        client, t, "rh@alpha.example.com", roles=[{"role_id": hr_role["id"]}], all_sites=True
    )
    delegable = _codes(hr, "/permissions/delegable")
    for index, code in enumerate(sorted(delegable)):
        response = hr.post("/roles", json={"name": f"R{index}", "permissions": [code]})
        assert response.status_code == 201, (code, response.text)
    for code in sorted(_codes(t.owner, "/permissions") - delegable)[:10]:
        response = hr.post("/roles", json={"name": f"X-{code}", "permissions": [code]})
        assert (response.status_code, response.json()["code"]) == (403, "permission_escalation")


def test_site_scope_of_delegation(provision: Any, api_for: Any, client: TestClient) -> None:
    t = _tenant(provision, api_for, "alpha")
    site_admin = _member(
        client,
        t,
        "admin-boutique@alpha.example.com",
        roles=[{"role_id": t.roles["administrator"], "site_id": t.site_a}],
        site_ids=[t.site_a],
    )
    # Ses droits de gestion ne valent que dans le contexte de son site.
    assert site_admin.get("/permissions/delegable").json()["code"] == "permission_denied"
    site_admin.site_id = t.site_a  # type: ignore[assignment]
    offer = _codes(t.owner, "/permissions")
    # Ses droits ne valent que sur son site : rien à déléguer sur tout le tenant.
    assert _codes(site_admin, "/permissions/delegable") == set()
    assert _role_ids(site_admin) == set()
    assert _codes(site_admin, "/permissions/delegable", site_id=t.site_a) == offer
    assert t.roles["seller"] in _role_ids(site_admin, site_id=t.site_a)
    assert _codes(site_admin, "/permissions/delegable", site_id=t.site_b) == set()
    assert _role_ids(site_admin, site_id=t.site_b) == set()
    # Site inconnu ou d'une autre entreprise.
    other = _tenant(provision, api_for, "beta")
    for site in (str(uuid.uuid4()), other.site_a):
        response = site_admin.get("/permissions/delegable", params={"site_id": site})
        assert (response.status_code, response.json()["code"]) == (404, "site_not_found")

    # L'application suit la projection : attribution limitée à son site acceptée, ailleurs non.
    ok = site_admin.post(
        "/members",
        json={
            "email": "vendeur@alpha.example.com",
            "full_name": "V",
            "password": TEMPORARY,
            "roles": [{"role_id": t.roles["seller"], "site_id": t.site_a}],
            "site_ids": [t.site_a],
        },
    )
    assert ok.status_code == 201, ok.text
    refused = site_admin.post(
        "/members",
        json={
            "email": "v2@alpha.example.com",
            "full_name": "V2",
            "password": TEMPORARY,
            "roles": [{"role_id": t.roles["seller"]}],
            "site_ids": [t.site_a],
        },
    )
    assert refused.json()["code"] == "permission_escalation"


def test_inactive_roles_are_not_delegable(provision: Any, api_for: Any) -> None:
    t = _tenant(provision, api_for, "alpha")
    role = t.owner.post("/roles", json={"name": "Saison", "permissions": ["sales.sale.view"]})
    role_id = role.json()["id"]
    assert role_id in _role_ids(t.owner)
    t.owner.post(f"/roles/{role_id}/deactivate")
    assert role_id not in _role_ids(t.owner)
    # Jamais supprimé : toujours listé, avec son historique.
    assert role_id in {r["id"] for r in t.owner.get("/roles").json()}
    assert t.owner.delete(f"/roles/{role_id}").status_code == 405


# --- Permissions hors offre : conservées, jamais ajoutées ---------------------------------------


def test_out_of_offer_permissions_are_kept_when_editing(
    provision: Any, api_for: Any, app_engine: Engine, owner_db: Session
) -> None:
    t = _tenant(provision, api_for, "alpha")
    role = t.owner.post(
        "/roles",
        json={"name": "Logistique", "permissions": ["stock.transfer.create", "stock.level.view"]},
    ).json()
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=t.id)
        change_plan(db, t.id, "STANDARD", actor="test")
        db.commit()
    # Fonctionnalité « transferts » absente de STANDARD : création hors offre.
    assert "stock.transfer.create" not in _codes(t.owner, "/permissions/delegable")

    current = t.owner.get(f"/roles/{role['id']}").json()["permission_codes"]
    assert "stock.transfer.create" in current  # conservée malgré le plan réduit
    updated = t.owner.patch(
        f"/roles/{role['id']}", json={"permissions": [*current, "catalog.article.view"]}
    )
    assert updated.status_code == 200, updated.text
    assert set(updated.json()["permission_codes"]) == {
        "stock.transfer.create",
        "stock.level.view",
        "catalog.article.view",
    }
    # Une permission hors offre ne peut pas être ajoutée.
    added = t.owner.patch(
        f"/roles/{role['id']}", json={"permissions": [*current, "stock.transfer.update"]}
    )
    assert (added.status_code, added.json()["code"]) == (422, "unknown_permission")
    # Retirée explicitement : supprimée.
    removed = t.owner.patch(f"/roles/{role['id']}", json={"permissions": ["stock.level.view"]})
    assert removed.json()["permission_codes"] == ["stock.level.view"]
    audit = owner_db.execute(
        text(
            "SELECT data FROM audit_logs WHERE action = 'role.updated' AND entity_id = :r "
            "ORDER BY occurred_at DESC LIMIT 1"
        ),
        {"r": role["id"]},
    ).scalar_one()
    assert set(audit["permissions_removed"]) == {"stock.transfer.create", "catalog.article.view"}


# --- Isolation -------------------------------------------------------------------------------


def test_roles_are_isolated(provision: Any, api_for: Any, app_engine: Engine) -> None:
    a = _tenant(provision, api_for, "alpha")
    b = _tenant(provision, api_for, "beta")
    role_b = b.owner.post("/roles", json={"name": "Secret", "permissions": ["sales.sale.view"]})
    role_b_id = role_b.json()["id"]
    assert role_b_id not in {r["id"] for r in a.owner.get("/roles").json()}
    assert role_b_id not in _role_ids(a.owner)
    for call in (
        a.owner.get(f"/roles/{role_b_id}"),
        a.owner.patch(f"/roles/{role_b_id}", json={"name": "Pirate"}),
        a.owner.post(f"/roles/{role_b_id}/deactivate"),
    ):
        assert (call.status_code, call.json()["code"]) == (404, "role_not_found")
    # Attribuer le rôle d'une autre entreprise : inconnu.
    response = a.owner.post(
        "/members",
        json={
            "email": "x@alpha.example.com",
            "full_name": "X",
            "password": TEMPORARY,
            "roles": [{"role_id": role_b_id}],
            "all_sites": True,
        },
    )
    assert response.json()["code"] == "role_not_found"

    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=a.id)
        for table in ("roles", "role_permissions"):
            tenants = db.execute(text(f"SELECT DISTINCT tenant_id FROM {table}")).scalars().all()
            assert b.id not in tenants
        assert (
            db.execute(
                text("UPDATE roles SET name = 'Pirate' WHERE id = :r"), {"r": role_b_id}
            ).rowcount
            == 0
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO role_permissions (tenant_id, role_id, permission_code) "
                    "VALUES (:t, :r, 'users.role.manage')"
                ),
                {"t": b.id, "r": role_b_id},
            )
