"""Phase 3.2-D — Administration des utilisateurs (ADR-0029) : l'administrateur d'un tenant gère
l'**appartenance** (rôles, sites, statut), jamais l'identité globale (nom, e-mail, mot de
passe). Liste paginée et filtrée, ajout (compte global réutilisé), activation / désactivation
limitée au tenant, audit, anti-escalade, propriétaire protégé, isolation API et RLS."""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from tests.conftest import PASSWORD, Api, login

TEMPORARY = "Provisoire-Admin-1"


@dataclass
class Tenant:
    id: uuid.UUID
    owner: Api
    site_a: str
    site_b: str
    roles: dict[str, str]


def _tenant(provision: Any, api_for: Any, slug: str) -> Tenant:
    t = provision(slug)
    owner: Api = api_for(f"owner@{slug}.example.com")
    site_b = owner.post("/sites", json={"name": "Dépôt", "code": "DEP", "kind": "warehouse"})
    assert site_b.status_code == 201, site_b.text
    roles = {r["template_code"] or r["name"]: r["id"] for r in owner.get("/roles").json()}
    return Tenant(t.tenant_id, owner, str(t.site_id), site_b.json()["id"], roles)


@pytest.fixture
def alpha(provision: Any, api_for: Any) -> Tenant:
    return _tenant(provision, api_for, "alpha")


def _add(t: Tenant, email: str, name: str | None = None, **access: Any) -> dict[str, Any]:
    response = t.owner.post(
        "/members",
        json={"email": email, "full_name": name or email, "password": TEMPORARY, **access},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _login(client: TestClient, email: str, tenant_id: uuid.UUID | None = None) -> Api:
    """Première connexion : mot de passe provisoire remplacé, puis session sur le tenant."""
    first = login(client, email, TEMPORARY)
    if first.status_code == 200:
        Api(client, first.json()["access_token"]).post(
            "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
        )
    response = login(client, email, PASSWORD, tenant_id)
    assert response.status_code == 200, response.text
    return Api(client, response.json()["access_token"])


def _audit(owner_db: Session, action: str, membership_id: str) -> list[dict[str, Any]]:
    return list(
        owner_db.execute(
            text(
                "SELECT data FROM audit_logs WHERE action = :a AND entity_id = :e "
                "ORDER BY occurred_at"
            ),
            {"a": action, "e": membership_id},
        ).scalars()
    )


# --- Liste ----------------------------------------------------------------------------------


def test_list_is_paginated_searchable_and_filtered(alpha: Tenant) -> None:
    seller = _add(
        alpha,
        "paul@alpha.example.com",
        "Paul Kaboré",
        roles=[{"role_id": alpha.roles["seller"]}],
        site_ids=[alpha.site_a],
    )
    _add(
        alpha,
        "awa@alpha.example.com",
        "Awa Ouédraogo",
        roles=[{"role_id": alpha.roles["manager"], "site_id": alpha.site_b}],
        site_ids=[alpha.site_b],
    )
    alpha.owner.post(f"/members/{seller['id']}/deactivate")

    page = alpha.owner.get("/members", params={"limit": 2}).json()
    assert (page["total"], page["limit"], len(page["items"])) == (3, 2, 2)
    names = [m["full_name"] for m in alpha.owner.get("/members").json()["items"]]
    assert names == ["Awa Ouédraogo", "Owner alpha", "Paul Kaboré"]  # tri par nom

    def emails(**params: Any) -> list[str]:
        return [m["email"] for m in alpha.owner.get("/members", params=params).json()["items"]]

    assert emails(search="kabor") == ["paul@alpha.example.com"]
    assert emails(search="AWA@ALPHA") == ["awa@alpha.example.com"]
    assert emails(status="inactive") == ["paul@alpha.example.com"]
    assert "paul@alpha.example.com" not in emails(status="active")
    assert emails(role_id=alpha.roles["seller"]) == ["paul@alpha.example.com"]
    # Accès à un site : tous les sites (propriétaire), site attribué ou rôle limité au site.
    assert emails(site_id=alpha.site_b) == ["awa@alpha.example.com", "owner@alpha.example.com"]
    assert emails(sort="-email")[0] == "paul@alpha.example.com"
    assert alpha.owner.get("/members", params={"sort": "password_hash"}).json()["code"] == (
        "invalid_sort"
    )
    item = alpha.owner.get("/members", params={"search": "paul"}).json()["items"][0]
    assert set(item) >= {"full_name", "email", "status", "roles", "site_ids", "created_at"}
    # Jamais de secret : seul l'indicateur de changement imposé est exposé.
    assert [k for k in item if "password" in k] == ["must_change_password"]


def test_member_list_permissions(alpha: Tenant, client: TestClient) -> None:
    _add(alpha, "vendeur@alpha.example.com", roles=[{"role_id": alpha.roles["seller"]}])
    _add(alpha, "lecteur@alpha.example.com", roles=[{"role_id": alpha.roles["viewer"]}])
    seller = _login(client, "vendeur@alpha.example.com")
    viewer = _login(client, "lecteur@alpha.example.com")
    # Le Consultant n'a pas accès aux utilisateurs (modèle de rôle : users.* exclu).
    for api in (seller, viewer):
        assert api.get("/members").json()["code"] == "permission_denied"
        assert api.post("/members/x/deactivate").status_code in (403, 422)


# --- Ajout : compte global créé ou réutilisé ----------------------------------------------------


def test_new_user_gets_a_hashed_temporary_password(alpha: Tenant, owner_db: Session) -> None:
    member = _add(alpha, "nouveau@alpha.example.com", roles=[{"role_id": alpha.roles["seller"]}])
    assert member["must_change_password"] is True
    stored = owner_db.execute(
        text("SELECT password_hash FROM users WHERE email = 'nouveau@alpha.example.com'")
    ).scalar_one()
    assert stored != TEMPORARY and stored.startswith("$argon2")
    created = _audit(owner_db, "member.created", member["id"])[0]
    assert created["user_created"] is True and TEMPORARY not in str(created)


def test_existing_global_user_is_reused_never_modified(
    provision: Any, api_for: Any, client: TestClient, owner_db: Session
) -> None:
    alpha = _tenant(provision, api_for, "alpha")
    beta = _tenant(provision, api_for, "beta")
    _add(alpha, "jean@example.com", "Jean Dupont", roles=[{"role_id": alpha.roles["seller"]}])
    _login(client, "jean@example.com")
    before = owner_db.execute(
        text("SELECT id, full_name, password_hash FROM users WHERE email = 'jean@example.com'")
    ).one()
    users = owner_db.execute(text("SELECT count(*) FROM users")).scalar_one()

    # Nom et mot de passe fournis par l'autre entreprise : ignorés, jamais appliqués.
    response = beta.owner.post(
        "/members",
        json={
            "email": "Jean@Example.com",
            "full_name": "Nom imposé",
            "password": "Autre-mot-de-passe-9",
            "roles": [{"role_id": beta.roles["manager"]}],
            "all_sites": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["user_id"] == str(before.id)
    assert response.json()["full_name"] == "Jean Dupont"
    after = owner_db.execute(
        text("SELECT id, full_name, password_hash FROM users WHERE email = 'jean@example.com'")
    ).one()
    assert after == before
    assert owner_db.execute(text("SELECT count(*) FROM users")).scalar_one() == users
    assert _audit(owner_db, "member.created", response.json()["id"])[0]["user_created"] is False
    # Déjà membre : refus (une seule appartenance par tenant).
    again = beta.owner.post(
        "/members", json={"email": "jean@example.com", "full_name": "Jean", "all_sites": True}
    )
    assert (again.status_code, again.json()["code"]) == (409, "member_exists")


# --- Accès : rôles, sites, statut ; identité globale intouchable --------------------------------


def test_access_changes_are_audited(alpha: Tenant, owner_db: Session) -> None:
    member = _add(
        alpha,
        "paul@alpha.example.com",
        roles=[{"role_id": alpha.roles["seller"]}],
        site_ids=[alpha.site_a],
    )
    response = alpha.owner.patch(
        f"/members/{member['id']}",
        json={"roles": [{"role_id": alpha.roles["manager"]}], "site_ids": [alpha.site_b]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["site_ids"] == [alpha.site_b]
    assert [r["role_id"] for r in response.json()["roles"]] == [alpha.roles["manager"]]

    def roles_of(action: str) -> list[str]:
        return [d["role_id"] for d in _audit(owner_db, action, member["id"])]

    def sites_of(action: str) -> list[str]:
        return [d["site_id"] for d in _audit(owner_db, action, member["id"])]

    assert roles_of("member.role_removed") == [alpha.roles["seller"]]
    assert alpha.roles["manager"] in roles_of("member.role_assigned")
    assert sites_of("member.site_removed") == [alpha.site_a]
    assert sites_of("member.site_assigned") == [alpha.site_a, alpha.site_b]
    updated = _audit(owner_db, "member.updated", member["id"])[-1]
    assert updated["before"]["site_ids"] == [alpha.site_a]
    assert updated["after"]["site_ids"] == [alpha.site_b]
    actor = owner_db.execute(
        text(
            "SELECT u.email FROM audit_logs a JOIN users u ON u.id = a.user_id "
            "WHERE a.action = 'member.updated' AND a.entity_id = :e"
        ),
        {"e": member["id"]},
    ).scalar_one()
    assert actor == "owner@alpha.example.com"


@pytest.mark.parametrize(
    "body",
    [
        {"full_name": "Autre nom"},
        {"email": "autre@example.com"},
        {"password": "Nouveau-mot-de-passe-1"},
        {"must_change_password": True},
        {"is_owner": True},
        {"user_id": str(uuid.uuid4())},
    ],
)
def test_global_identity_cannot_be_changed_by_the_tenant(
    alpha: Tenant, owner_db: Session, body: dict[str, Any]
) -> None:
    member = _add(alpha, "paul@alpha.example.com", "Paul", roles=[])
    before = owner_db.execute(
        text("SELECT full_name, email, password_hash FROM users WHERE email = :e"),
        {"e": "paul@alpha.example.com"},
    ).one()
    response = alpha.owner.patch(f"/members/{member['id']}", json=body)
    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    after = owner_db.execute(
        text("SELECT full_name, email, password_hash FROM users WHERE id = :u"),
        {"u": member["user_id"]},
    ).one()
    assert after == before
    # Aucun point d'entrée d'administration de l'identité globale.
    for response in (
        alpha.owner.put(f"/users/{member['user_id']}", json={}),
        alpha.owner.patch(f"/users/{member['user_id']}", json={}),
        alpha.owner.delete(f"/users/{member['user_id']}"),
        alpha.owner.delete(f"/members/{member['id']}"),
    ):
        assert response.status_code in (404, 405)


def test_deactivation_is_limited_to_the_tenant_membership(
    provision: Any, api_for: Any, client: TestClient, owner_db: Session
) -> None:
    alpha = _tenant(provision, api_for, "alpha")
    beta = _tenant(provision, api_for, "beta")
    jean_a = _add(
        alpha,
        "jean@example.com",
        "Jean",
        roles=[{"role_id": alpha.roles["seller"]}],
        site_ids=[alpha.site_a],
    )
    beta.owner.post(
        "/members",
        json={
            "email": "jean@example.com",
            "full_name": "Jean",
            "roles": [{"role_id": beta.roles["manager"]}],
            "all_sites": True,
        },
    )
    jean_in_a = _login(client, "jean@example.com", alpha.id)
    assert jean_in_a.get("/me/capabilities").status_code == 200

    response = alpha.owner.post(f"/members/{jean_a['id']}/deactivate")
    assert response.status_code == 200 and response.json()["status"] == "suspended"
    # Rôles et sites conservés (réactivation à l'identique), historique intact.
    assert response.json()["roles"] == jean_a["roles"]
    assert response.json()["site_ids"] == [alpha.site_a]

    # Tenant A : accès refusé immédiatement (jeton existant et nouvelle connexion).
    assert jean_in_a.get("/me/capabilities").json()["code"] == "tenant_access_denied"
    assert login(client, "jean@example.com", PASSWORD, alpha.id).status_code != 200
    # Tenant B : toujours actif ; le compte global reste actif.
    jean_in_b = _login(client, "jean@example.com", beta.id)
    assert jean_in_b.get("/me/capabilities").status_code == 200
    statuses = dict(
        owner_db.execute(
            text(
                "SELECT m.tenant_id, m.status FROM tenant_memberships m JOIN users u "
                "ON u.id = m.user_id WHERE u.email = 'jean@example.com'"
            )
        ).all()
    )
    assert statuses == {alpha.id: "suspended", beta.id: "active"}
    assert owner_db.execute(
        text("SELECT is_active FROM users WHERE email = 'jean@example.com'")
    ).scalar_one()

    audit = _audit(owner_db, "member.deactivated", jean_a["id"])
    assert audit == [{"user_id": jean_a["user_id"], "before": "active", "after": "suspended"}]
    reactivated = alpha.owner.post(f"/members/{jean_a['id']}/activate").json()
    assert reactivated["status"] == "active"
    assert len(_audit(owner_db, "member.activated", jean_a["id"])) == 1
    assert _login(client, "jean@example.com", alpha.id).get("/me/capabilities").status_code == 200


def test_reactivation_respects_the_plan_user_limit(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("petit", plan="STANDARD")
    owner = api_for("owner@petit.example.com")
    owner_db.execute(
        text(
            "UPDATE plans SET limits = jsonb_set(limits, '{max_users}', '2') "
            "WHERE code = 'STANDARD'"
        )
    )
    owner_db.commit()
    try:
        first = owner.post(
            "/members",
            json={"email": "a@petit.example.com", "full_name": "A", "password": TEMPORARY},
        ).json()
        owner.post(f"/members/{first['id']}/deactivate")
        second = owner.post(
            "/members",
            json={"email": "b@petit.example.com", "full_name": "B", "password": TEMPORARY},
        )
        assert second.status_code == 201, second.text
        refused = owner.post(f"/members/{first['id']}/activate")
        assert (refused.status_code, refused.json()["code"]) == (422, "plan_limit_reached")
    finally:
        owner_db.execute(
            text(
                "UPDATE plans SET limits = jsonb_set(limits, '{max_users}', '5') "
                "WHERE code = 'STANDARD'"
            )
        )
        owner_db.commit()
    assert t.tenant_id


# --- Propriétaire, soi-même, anti-escalade ----------------------------------------------------


def test_owner_and_self_are_protected(alpha: Tenant, client: TestClient) -> None:
    owner_membership = [m for m in alpha.owner.get("/members").json()["items"] if m["is_owner"]][0]
    for action in ("deactivate", "activate"):
        response = alpha.owner.post(f"/members/{owner_membership['id']}/{action}")
        assert (response.status_code, response.json()["code"]) == (403, "owner_protected")

    admin = _add(
        alpha,
        "admin2@alpha.example.com",
        roles=[{"role_id": alpha.roles["administrator"]}],
        all_sites=True,
    )
    second_admin = _login(client, "admin2@alpha.example.com")
    # Un autre administrateur ne touche pas au propriétaire, ni à ses propres accès.
    refused = second_admin.post(f"/members/{owner_membership['id']}/deactivate")
    assert refused.json()["code"] == "owner_protected"
    assert second_admin.post(f"/members/{admin['id']}/deactivate").json()["code"] == (
        "self_modification"
    )
    assert (
        second_admin.patch(f"/members/{owner_membership['id']}", json={"roles": []}).json()["code"]
        == "owner_protected"
    )


def test_delegation_is_limited_to_the_actor_scope(alpha: Tenant, client: TestClient) -> None:
    hr_role = alpha.owner.post(
        "/roles",
        json={
            "name": "RH boutique",
            "permissions": ["users.member.view", "users.member.manage", "users.role.view"],
        },
    ).json()
    _add(
        alpha,
        "rh@alpha.example.com",
        roles=[{"role_id": hr_role["id"]}],
        site_ids=[alpha.site_a],
    )
    hr = _login(client, "rh@alpha.example.com")
    target = _add(alpha, "cible@alpha.example.com", roles=[], site_ids=[alpha.site_a])

    # Cas 1 : rôle non délégable (permissions que l'acteur ne détient pas).
    for role in ("administrator", "seller"):
        response = hr.patch(
            f"/members/{target['id']}", json={"roles": [{"role_id": alpha.roles[role]}]}
        )
        assert (response.status_code, response.json()["code"]) == (403, "permission_escalation")
    # Cas 2 : site hors de son périmètre ; tous les sites.
    for body in ({"site_ids": [alpha.site_b]}, {"all_sites": True}):
        response = hr.patch(f"/members/{target['id']}", json=body)
        assert (response.status_code, response.json()["code"]) == (403, "site_escalation")
    # Un membre dont l'accès dépasse le périmètre de l'acteur n'est pas désactivable par lui.
    wide = _add(alpha, "large@alpha.example.com", roles=[], all_sites=True)
    assert hr.post(f"/members/{wide['id']}/deactivate").json()["code"] == "site_escalation"
    # Dans son périmètre : autorisé.
    assert hr.post(f"/members/{target['id']}/deactivate").status_code == 200
    # Nouvel utilisateur : mêmes contrôles à l'ajout.
    created = hr.post(
        "/members",
        json={
            "email": "x@alpha.example.com",
            "full_name": "X",
            "password": TEMPORARY,
            "roles": [{"role_id": alpha.roles["manager"]}],
            "site_ids": [alpha.site_a],
        },
    )
    assert created.json()["code"] == "permission_escalation"


# --- Isolation -------------------------------------------------------------------------------


def test_other_tenant_memberships_are_unreachable(
    provision: Any, api_for: Any, app_engine: Engine, owner_db: Session
) -> None:
    alpha = _tenant(provision, api_for, "alpha")
    beta = _tenant(provision, api_for, "beta")
    target = _add(beta, "salarie@beta.example.com", roles=[{"role_id": beta.roles["seller"]}])
    assert "salarie@beta.example.com" not in str(alpha.owner.get("/members").json())
    for call in (
        alpha.owner.get(f"/members/{target['id']}"),
        alpha.owner.patch(f"/members/{target['id']}", json={"roles": []}),
        alpha.owner.post(f"/members/{target['id']}/deactivate"),
        alpha.owner.post(f"/members/{target['id']}/activate"),
    ):
        assert (call.status_code, call.json()["code"]) == (404, "member_not_found")
    # Filtres par rôle ou site d'un autre tenant : rien.
    assert (
        alpha.owner.get("/members", params={"role_id": beta.roles["seller"]}).json()["total"] == 0
    )
    assert alpha.owner.get("/members", params={"site_id": beta.site_b}).json()["total"] == 1

    # RLS (rôle applicatif sans BYPASSRLS) : appartenances, rôles et sites de B intouchables.
    with create_session_factory(app_engine)() as db:
        set_db_context(db, tenant_id=alpha.id)
        for table in ("tenant_memberships", "membership_roles", "membership_sites"):
            rows = db.execute(text(f"SELECT DISTINCT tenant_id FROM {table}")).scalars().all()
            assert beta.id not in rows
        updated = db.execute(
            text("UPDATE tenant_memberships SET status = 'suspended' WHERE id = :m"),
            {"m": target["id"]},
        )
        assert updated.rowcount == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            db.execute(
                text(
                    "INSERT INTO membership_sites (tenant_id, membership_id, site_id) "
                    "VALUES (:t, :m, :s)"
                ),
                {"t": beta.id, "m": target["id"], "s": beta.site_b},
            )
    status = owner_db.execute(
        text("SELECT status FROM tenant_memberships WHERE id = :m"), {"m": target["id"]}
    ).scalar_one()
    assert status == "active"
