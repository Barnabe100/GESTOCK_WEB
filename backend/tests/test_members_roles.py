from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, login


def _roles(api: Api) -> dict[str, str]:
    return {r["template_code"] or r["name"]: r["id"] for r in api.get("/roles").json()}


def _activate(client: Any, email: str, temporary: str = "Provisoire-123") -> None:
    token = login(client, email, temporary).json()["access_token"]
    response = Api(client, token).post(
        "/me/password", json={"current_password": temporary, "new_password": PASSWORD}
    )
    assert response.status_code == 204


def test_new_member_gets_temporary_password(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    response = owner.post(
        "/members",
        json={"email": "New@Example.com", "full_name": "Nouveau", "password": "Provisoire-123"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["must_change_password"] is True
    assert "Provisoire-123" not in response.text and "password_hash" not in response.text
    stored = owner_db.execute(
        text("SELECT password_hash FROM users WHERE email = 'new@example.com'")
    ).scalar_one()
    assert stored.startswith("$argon2id$")


def test_new_member_requires_password(provision: Any, api_for: Any) -> None:
    provision("alpha")
    response = api_for("owner@alpha.example.com").post(
        "/members", json={"email": "sans@example.com", "full_name": "Sans"}
    )
    assert response.json()["code"] == "password_required"


def test_existing_user_joins_without_password_change(
    provision: Any, api_for: Any, client: Any
) -> None:
    provision("alpha")
    provision("beta")
    owner_b = api_for("owner@beta.example.com")
    response = owner_b.post(
        "/members",
        json={
            "email": "owner@alpha.example.com",
            "full_name": "Ignoré",
            "password": "Tentative-de-reset",
        },
    )
    assert response.status_code == 201
    assert response.json()["full_name"] == "Owner alpha"  # identité inchangée
    # Le mot de passe n'a pas été modifié par l'autre tenant.
    assert login(client, "owner@alpha.example.com", "Tentative-de-reset").status_code == 401
    memberships = login(client, "owner@alpha.example.com").json()["memberships"]
    assert len(memberships) == 2
    duplicate = owner_b.post(
        "/members", json={"email": "owner@alpha.example.com", "full_name": "X"}
    )
    assert duplicate.json()["code"] == "member_exists"


def test_plan_limits(provision: Any, api_for: Any) -> None:
    provision("alpha", plan="STANDARD")  # provisoire : 1 site, 5 utilisateurs
    owner = api_for("owner@alpha.example.com")
    site = owner.post("/sites", json={"name": "Second", "code": "SECOND"})
    assert site.status_code == 422
    assert site.json()["code"] == "plan_limit_reached"
    for i in range(4):
        created = owner.post(
            "/members",
            json={"email": f"u{i}@example.com", "full_name": f"U{i}", "password": "Provisoire-123"},
        )
        assert created.status_code == 201
    over = owner.post(
        "/members",
        json={"email": "u9@example.com", "full_name": "U9", "password": "Provisoire-123"},
    )
    assert over.json()["code"] == "plan_limit_reached"


def test_owner_and_self_are_protected(provision: Any, api_for: Any, client: Any) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    roles = _roles(owner)
    owner_membership = owner.get("/members").json()["items"][0]
    admin = owner.post(
        "/members",
        json={
            "email": "admin@example.com",
            "full_name": "Admin",
            "password": "Provisoire-123",
            "roles": [{"role_id": roles["administrator"]}],
            "all_sites": True,
        },
    ).json()
    _activate(client, "admin@example.com")
    admin_api = api_for("admin@example.com")
    blocked = admin_api.patch(f"/members/{owner_membership['id']}", json={"status": "suspended"})
    assert blocked.json()["code"] == "owner_protected"
    self_edit = admin_api.patch(f"/members/{admin['id']}", json={"roles": []})
    assert self_edit.json()["code"] == "self_modification"


def test_no_privilege_escalation(provision: Any, api_for: Any, client: Any) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    limited = owner.post(
        "/roles",
        json={
            "name": "Gestion membres",
            "permissions": [
                "users.member.view",
                "users.member.manage",
                "users.role.view",
                "users.role.manage",
            ],
        },
    ).json()
    owner.post(
        "/members",
        json={
            "email": "rh@example.com",
            "full_name": "RH",
            "password": "Provisoire-123",
            "roles": [{"role_id": limited["id"]}],
        },
    )
    _activate(client, "rh@example.com")
    rh = api_for("rh@example.com")
    # Ne peut pas créer un rôle plus puissant que le sien…
    escalate = rh.post(
        "/roles", json={"name": "Super", "permissions": ["organization.site.manage"]}
    )
    assert escalate.status_code == 403
    assert escalate.json()["code"] == "permission_escalation"
    # … ni attribuer le rôle Administrateur.
    admin_role = _roles(rh)["administrator"]
    grant = rh.post(
        "/members",
        json={
            "email": "complice@example.com",
            "full_name": "Complice",
            "password": "Provisoire-123",
            "roles": [{"role_id": admin_role}],
        },
    )
    assert grant.json()["code"] == "permission_escalation"
    # Mais peut accorder ce qu'il détient.
    ok = rh.post("/roles", json={"name": "Lecture membres", "permissions": ["users.member.view"]})
    assert ok.status_code == 201


def test_role_rules(provision: Any, api_for: Any) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    roles = _roles(owner)
    system = owner.patch(f"/roles/{roles['administrator']}", json={"name": "Autre"})
    assert system.json()["code"] == "system_role"
    unknown = owner.post("/roles", json={"name": "X", "permissions": ["stock.magic.do"]})
    assert unknown.json()["code"] == "unknown_permission"
    role = owner.post("/roles", json={"name": "Temp", "permissions": ["audit.log.view"]}).json()
    duplicate = owner.post("/roles", json={"name": "Temp"})
    assert duplicate.json()["code"] == "role_name_taken"
    owner.post(
        "/members",
        json={
            "email": "t@example.com",
            "full_name": "T",
            "password": "Provisoire-123",
            "roles": [{"role_id": role["id"]}],
        },
    )
    # Aucun rôle n'est supprimé (ADR-0015) : désactivation, avec confirmation s'il est attribué.
    assert owner.delete(f"/roles/{role['id']}").status_code == 405
    assert owner.post(f"/roles/{role['id']}/deactivate").json()["code"] == "role_in_use"
    perms = owner.get("/permissions").json()
    assert {
        "code": "audit.log.view",
        "module": "audit",
        "access": "read",
        "resource": "log",
        "action": "view",
    } in perms


def test_member_update_changes_roles(provision: Any, api_for: Any) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    roles = _roles(owner)
    member = owner.post(
        "/members",
        json={
            "email": "m@example.com",
            "full_name": "M",
            "password": "Provisoire-123",
            "roles": [{"role_id": roles["viewer"]}],
        },
    ).json()
    updated = owner.patch(
        f"/members/{member['id']}", json={"roles": [{"role_id": roles["administrator"]}]}
    ).json()
    assert [r["role_id"] for r in updated["roles"]] == [roles["administrator"]]
    actions = [i["action"] for i in owner.get("/audit-logs").json()["items"]]
    assert "member.updated" in actions and "member.created" in actions


def test_tenant_update_validates_timezone(provision: Any, api_for: Any) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    assert owner.patch("/tenant", json={"timezone": "Mars/Olympus"}).status_code == 422
    ok = owner.patch("/tenant", json={"name": "Alpha SARL", "timezone": "Africa/Abidjan"})
    assert ok.status_code == 200
    assert ok.json()["timezone"] == "Africa/Abidjan"
    actions = [i["action"] for i in owner.get("/audit-logs").json()["items"]]
    assert "tenant.updated" in actions
