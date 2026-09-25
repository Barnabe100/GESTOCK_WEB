"""RBAC (ADR-0015) : rôles de base, rôles personnalisés, activation, duplication, membres,
audit, plan et abonnement, isolation."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, login

TEMPORARY = "Provisoire-123"


@pytest.fixture
def owner(provision: Any, api_for: Any) -> Api:
    provision("alpha", profile="quincaillerie", plan="ENTREPRISE")
    api: Api = api_for("owner@alpha.example.com")
    return api


def _roles(api: Api, **params: str) -> dict[str, dict[str, Any]]:
    response = api.get("/roles", params=params)
    assert response.status_code == 200, response.text
    return {r["template_code"] or r["name"]: r for r in response.json()}


def _member(owner: Api, client: Any, email: str, role_ids: list[str], **access: Any) -> Any:
    body = {
        "email": email,
        "full_name": email,
        "password": TEMPORARY,
        "roles": [{"role_id": r} for r in role_ids],
        "all_sites": True,
        **access,
    }
    created = owner.post("/members", json=body)
    assert created.status_code == 201, created.text
    token = login(client, email, TEMPORARY).json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": TEMPORARY, "new_password": PASSWORD}
    )
    return created.json(), Api(client, login(client, email).json()["access_token"])


def _permissions(api: Api) -> set[str]:
    return set(api.get("/me/capabilities").json()["permissions"])


# --- Rôles de base ------------------------------------------------------------------------------


def test_base_roles(owner: Api) -> None:
    roles = _roles(owner)
    assert {c for c, r in roles.items() if r["is_system"]} == {
        "administrator",
        "manager",
        "seller",
        "viewer",
    }
    names = {c: r["name"] for c, r in roles.items()}
    assert names == {
        "administrator": "Administrateur",
        "manager": "Gestionnaire",
        "seller": "Vendeur",
        "viewer": "Consultant",
    }
    assert roles["administrator"]["protected"] is True
    assert [c for c, r in roles.items() if r["protected"]] == ["administrator"]

    manager = set(roles["manager"]["permission_codes"])
    assert {"catalog.article.create", "stock.entry.validate", "alerts.stock.view"} <= manager
    # Gestionnaire : ni annulation, ni gestion des motifs, ni administration.
    assert not manager & {"stock.entry.cancel", "stock.exit.cancel", "stock.reason.manage"}
    assert not any(p.startswith(("users.", "organization.")) for p in manager)

    assert set(roles["seller"]["permission_codes"]) == {
        "catalog.article.view",
        "catalog.category.view",
        "customers.customer.view",
        "sales.sale.view",
        "sales.sale.create",
        "sales.sale.update",
        "sales.sale.validate",
        "stock.level.view",
        "alerts.stock.view",
        "inventory_count.inventory.view",  # inventaires : consultation seule (Phase 2.6)
        "sales.payment.view",  # encaissement des ventes, sans annulation (Phase 2.7)
        "sales.payment.create",
        "receivables.receivable.view",  # créances : consultation (Phase 2.8)
        "cash_register.register.view",  # caisse : ouverture et clôture (Phase 2.9)
        "cash_register.session.view",
        "cash_register.session.open",
        "cash_register.session.close",
        "pos.terminal.use",  # point de vente (Phase 3.0)
    }
    viewer = set(roles["viewer"]["permission_codes"])
    assert "stock.movement.view" in viewer and "organization.site.view" in viewer
    assert not any(p.startswith(("users.", "audit.", "organization.module.")) for p in viewer)
    assert all(p.endswith(".view") for p in viewer)

    assert set(_roles(owner, kind="system")) == {"administrator", "manager", "seller", "viewer"}


def test_base_roles_are_read_only(owner: Api) -> None:
    roles = _roles(owner)
    for code in ("administrator", "manager", "seller", "viewer"):
        edit = owner.patch(f"/roles/{roles[code]['id']}", json={"description": "x"})
        assert edit.status_code == 403 and edit.json()["code"] == "system_role"
    protected = owner.post(
        f"/roles/{roles['administrator']['id']}/deactivate", json={"confirm": True}
    )
    assert protected.status_code == 403 and protected.json()["code"] == "role_protected"
    # Un rôle de base non protégé peut être désactivé (non attribué : sans confirmation).
    seller = owner.post(f"/roles/{roles['seller']['id']}/deactivate")
    assert seller.status_code == 200 and seller.json()["is_active"] is False
    assert owner.post(f"/roles/{roles['seller']['id']}/activate").json()["is_active"] is True


# --- Rôles personnalisés -----------------------------------------------------------------------


def test_custom_role_lifecycle(owner: Api) -> None:
    created = owner.post(
        "/roles",
        json={
            "name": "  Magasinier ",
            "description": "Réception",
            "permissions": ["stock.entry.view", "stock.entry.create"],
        },
    )
    assert created.status_code == 201, created.text
    role = created.json()
    assert role["name"] == "Magasinier" and role["is_system"] is False
    assert role["is_active"] is True and role["member_count"] == 0

    # Nom unique sans distinction de casse ; noms des rôles de base réservés.
    assert owner.post("/roles", json={"name": "MAGASINIER"}).json()["code"] == "role_name_taken"
    for reserved in ("vendeur", "Administrateur", " gestionnaire "):
        response = owner.post("/roles", json={"name": reserved})
        assert response.status_code == 409 and response.json()["code"] == "role_name_reserved"

    updated = owner.patch(
        f"/roles/{role['id']}",
        json={
            "name": "Magasinier dépôt",
            "permissions": ["stock.entry.view", "stock.entry.validate"],
        },
    ).json()
    assert updated["name"] == "Magasinier dépôt"
    assert updated["permission_codes"] == ["stock.entry.validate", "stock.entry.view"]
    assert updated["description"] == "Réception"
    renamed_reserved = owner.patch(f"/roles/{role['id']}", json={"name": "Consultant"})
    assert renamed_reserved.json()["code"] == "role_name_reserved"
    unknown = owner.patch(f"/roles/{role['id']}", json={"permissions": ["stock.magic.do"]})
    assert unknown.json()["code"] == "unknown_permission"

    audit = owner.get("/audit-logs", params={"action": "role.updated"}).json()["items"][0]
    assert audit["data"]["permissions_added"] == ["stock.entry.validate"]
    assert audit["data"]["permissions_removed"] == ["stock.entry.create"]
    assert audit["data"]["before"]["name"] == "Magasinier"
    assert audit["data"]["after"]["name"] == "Magasinier dépôt"
    assert set(_roles(owner, kind="custom")) == {"Magasinier dépôt"}


def test_same_role_name_in_two_tenants(provision: Any, api_for: Any, owner: Api) -> None:
    owner.post("/roles", json={"name": "Caissier", "permissions": ["catalog.article.view"]})
    provision("beta")
    other = api_for("owner@beta.example.com")
    created = other.post("/roles", json={"name": "caissier", "permissions": ["stock.level.view"]})
    assert created.status_code == 201, created.text
    assert _roles(owner)["Caissier"]["permission_codes"] == ["catalog.article.view"]
    assert _roles(other)["caissier"]["permission_codes"] == ["stock.level.view"]


def test_duplicate_role(owner: Api) -> None:
    roles = _roles(owner)
    copy = owner.post(
        f"/roles/{roles['manager']['id']}/duplicate",
        json={"name": "Responsable magasin", "description": "Gestionnaire + annulation"},
    )
    assert copy.status_code == 201, copy.text
    duplicated = copy.json()
    assert duplicated["is_system"] is False and duplicated["template_code"] is None
    assert duplicated["permission_codes"] == roles["manager"]["permission_codes"]
    # La copie est un rôle personnalisé : modifiable.
    extended = owner.patch(
        f"/roles/{duplicated['id']}",
        json={"permissions": [*duplicated["permission_codes"], "stock.entry.cancel"]},
    )
    assert "stock.entry.cancel" in extended.json()["permission_codes"]
    again = owner.post(f"/roles/{duplicated['id']}/duplicate", json={"name": "Vendeur"})
    assert again.json()["code"] == "role_name_reserved"
    audit = owner.get("/audit-logs", params={"action": "role.created"}).json()["items"][0]
    assert audit["data"]["copied_from"]["id"] == roles["manager"]["id"]


# --- Activation / désactivation ----------------------------------------------------------------


def test_deactivation_keeps_assignments_and_reactivation_restores(
    owner: Api, client: Any, owner_db: Session
) -> None:
    role = owner.post(
        "/roles", json={"name": "Caissier", "permissions": ["catalog.article.view"]}
    ).json()
    member, api = _member(owner, client, "caisse@example.com", [role["id"]])
    assert "catalog.article.view" in _permissions(api)
    assert _roles(owner)["Caissier"]["member_count"] == 1
    members = owner.get(f"/roles/{role['id']}/members").json()
    assert [(m["membership_id"], m["site_id"]) for m in members] == [(member["id"], None)]

    refused = owner.post(f"/roles/{role['id']}/deactivate")
    assert refused.status_code == 409 and refused.json()["code"] == "role_in_use"
    assert refused.json()["count"] == 1
    assert refused.json()["members"][0]["membership_id"] == member["id"]
    assert _roles(owner)["Caissier"]["is_active"] is True

    done = owner.post(f"/roles/{role['id']}/deactivate", json={"confirm": True})
    assert done.status_code == 200 and done.json()["is_active"] is False
    assert "catalog.article.view" not in _permissions(api)  # plus aucun droit
    assert api.get("/catalog/articles").status_code == 403
    # Attributions conservées (historique d'autorisation intact).
    assert owner.get(f"/members/{member['id']}").json()["roles"] == [
        {"role_id": role["id"], "site_id": None}
    ]
    assert owner_db.execute(text("SELECT count(*) FROM membership_roles")).scalar_one() == 1
    assert set(_roles(owner, status="inactive")) == {"Caissier"}

    # Rôle inactif : aucune nouvelle attribution…
    new = owner.post(
        "/members",
        json={
            "email": "autre@example.com",
            "full_name": "Autre",
            "password": TEMPORARY,
            "roles": [{"role_id": role["id"]}],
        },
    )
    assert new.status_code == 422 and new.json()["code"] == "role_inactive"
    # … mais l'attribution existante n'empêche pas de modifier le membre.
    kept = owner.patch(f"/members/{member['id']}", json={"all_sites": False, "site_ids": []})
    assert kept.status_code == 200, kept.text

    assert owner.post(f"/roles/{role['id']}/activate").json()["is_active"] is True
    assert "catalog.article.view" in _permissions(api)  # droits rétablis

    actions = [
        i["action"] for i in owner.get("/audit-logs", params={"action": "role."}).json()["items"]
    ]
    assert actions[:2] == ["role.activated", "role.deactivated"]
    deactivated = owner.get("/audit-logs", params={"action": "role.deactivated"}).json()
    assert deactivated["items"][0]["data"]["members"] == [member["id"]]
    assert deactivated["items"][0]["data"]["confirmed"] is True


def test_role_members_require_member_view(owner: Api, client: Any) -> None:
    role = owner.post(
        "/roles", json={"name": "Lecture rôles", "permissions": ["users.role.view"]}
    ).json()
    _, api = _member(owner, client, "roles@example.com", [role["id"]])
    assert api.get("/roles").status_code == 200
    denied = api.get(f"/roles/{role['id']}/members")
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"


# --- Attribution et audit des membres -----------------------------------------------------------


def test_role_assignment_audit(owner: Api) -> None:
    roles = _roles(owner)
    member = owner.post(
        "/members",
        json={
            "email": "m@example.com",
            "full_name": "M",
            "password": TEMPORARY,
            "roles": [{"role_id": roles["seller"]["id"]}],
            "all_sites": True,
        },
    ).json()
    owner.patch(f"/members/{member['id']}", json={"roles": [{"role_id": roles["viewer"]["id"]}]})
    items = owner.get("/audit-logs", params={"action": "member."}).json()["items"]
    by_action: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_action.setdefault(item["action"], []).append(item["data"])
    assigned = {d["role_name"] for d in by_action["member.role_assigned"]}
    assert assigned == {"Vendeur", "Consultant"}
    assert [d["role_name"] for d in by_action["member.role_removed"]] == ["Vendeur"]
    update = by_action["member.updated"][0]
    assert update["before"]["roles"] == [{"role_id": roles["seller"]["id"], "site_id": None}]
    assert update["after"]["roles"] == [{"role_id": roles["viewer"]["id"], "site_id": None}]


# --- Plan, modules, abonnement -----------------------------------------------------------------


def test_custom_role_never_bypasses_plan_or_subscription(
    owner: Api, client: Any, owner_db: Session
) -> None:
    role = owner.post(
        "/roles",
        json={
            "name": "Stock complet",
            "permissions": ["stock.entry.view", "stock.entry.create", "catalog.article.view"],
        },
    ).json()
    _, api = _member(owner, client, "stock@example.com", [role["id"]])
    assert {"stock.entry.view", "stock.entry.create"} <= _permissions(api)

    # Module désactivé : les permissions enregistrées du rôle ne donnent plus rien.
    owner_db.execute(
        text("UPDATE tenant_modules SET enabled = false WHERE module_code IN ('stock', 'alerts')")
    )
    owner_db.commit()
    assert not {p for p in _permissions(api) if p.startswith("stock.")}
    blocked = api.get("/stock/entries")
    assert blocked.status_code == 403 and blocked.json()["code"] == "module_unavailable"
    # Et le rôle ne peut plus recevoir de permission d'un module hors offre.
    extra = owner.patch(f"/roles/{role['id']}", json={"permissions": ["stock.exit.create"]})
    assert extra.json()["code"] == "unknown_permission"

    # Abonnement expiré : l'écriture reste bloquée malgré le rôle.
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    caps = api.get("/me/capabilities").json()
    assert "catalog.article.view" in caps["permissions"]
    create = api.post("/catalog/categories", json={"name": "X"})
    assert create.status_code == 403


# --- Multi-site ---------------------------------------------------------------------------------


def test_site_scoped_role_only_applies_on_its_site(
    provision: Any, api_for: Any, client: Any
) -> None:
    t = provision("gamma", profile="quincaillerie", plan="ENTREPRISE")
    owner: Api = api_for("owner@gamma.example.com")
    site_b = owner.post("/sites", json={"name": "Dépôt", "code": "DEP", "kind": "warehouse"})
    site_b_id = site_b.json()["id"]
    boss = owner.post(
        "/roles",
        json={"name": "Responsable boutique", "permissions": ["stock.entry.view"]},
    ).json()
    _, api = _member(
        owner,
        client,
        "resp@example.com",
        [],
        all_sites=False,
        site_ids=[str(t.site_id)],
    )
    member_id = [m for m in owner.get("/members").json() if m["email"] == "resp@example.com"][0][
        "id"
    ]
    owner.patch(
        f"/members/{member_id}",
        json={"roles": [{"role_id": boss["id"], "site_id": str(t.site_id)}]},
    )
    api.site_id = t.site_id  # type: ignore[assignment]
    assert "stock.entry.view" in _permissions(api)
    api.site_id = None
    assert "stock.entry.view" not in _permissions(api)  # sans site : rôle limité inopérant
    api.site_id = site_b_id  # type: ignore[assignment]
    assert api.get("/me/capabilities").json()["code"] == "site_access_denied"
