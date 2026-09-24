from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Api


def _caps(api: Api) -> dict[str, Any]:
    response = api.get("/me/capabilities")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _module_codes(caps: dict[str, Any]) -> set[str]:
    return {m["code"] for m in caps["modules"]}


def test_profiles_drive_modules_navigation_and_terminology(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant")
    provision("quinc", profile="quincaillerie")
    resto = _caps(api_for("owner@resto.example.com"))
    quinc = _caps(api_for("owner@quinc.example.com"))

    assert {"restaurant.tables", "restaurant.kitchen"} <= _module_codes(resto)
    assert not [m for m in _module_codes(quinc) if m.startswith("restaurant.")]
    assert resto["navigation"][:3] == ["dashboard", "restaurant.tables", "restaurant.orders"]
    assert "restaurant.tables" not in quinc["navigation"]
    assert resto["terminology"]["fr"]["catalog"]["item"] == "Produit"
    assert quinc["terminology"]["fr"]["catalog"]["item"] == "Article"
    # Les modules core sont toujours présents.
    for caps in (resto, quinc):
        assert {"dashboard", "organization", "users", "audit", "subscription"} <= _module_codes(
            caps
        )


def test_plan_filters_modules(provision: Any, api_for: Any, owner_db: Session) -> None:
    std = provision("std", profile="restaurant", plan="STANDARD")
    ent = provision("ent", profile="restaurant", plan="ENTREPRISE")
    # restaurant.qr est optionnel dans le profil : on l'active dans les deux tenants.
    for t in (std, ent):
        owner_db.execute(
            text(
                "INSERT INTO tenant_modules (tenant_id, module_code, enabled) "
                "VALUES (:t, 'restaurant.qr', true) "
                "ON CONFLICT (tenant_id, module_code) DO UPDATE SET enabled = true"
            ),
            {"t": t.tenant_id},
        )
    owner_db.commit()
    assert "restaurant.qr" not in _module_codes(_caps(api_for("owner@std.example.com")))
    assert "restaurant.qr" in _module_codes(_caps(api_for("owner@ent.example.com")))


def test_disabled_dependency_removes_dependents(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("alpha", profile="alimentation")
    owner_db.execute(
        text(
            "UPDATE tenant_modules SET enabled = false "
            "WHERE tenant_id = :t AND module_code = 'sales'"
        ),
        {"t": t.tenant_id},
    )
    owner_db.commit()
    modules = _module_codes(_caps(api_for("owner@alpha.example.com")))
    assert "sales" not in modules
    assert not {"payments", "cash_register", "pos"} & modules  # fermeture sur les dépendances
    assert "stock" in modules


def test_owner_gets_all_permissions_of_active_modules(provision: Any, api_for: Any) -> None:
    provision("alpha")
    caps = _caps(api_for("owner@alpha.example.com"))
    assert caps["is_owner"] is True
    assert "users.member.manage" in caps["permissions"]
    assert caps["restricted_permissions"] == []


def test_role_based_permissions_and_site_scoped_roles(provision: Any, api_for: Any) -> None:
    t = provision("alpha", plan="ENTREPRISE")
    owner = api_for("owner@alpha.example.com")
    second_site = owner.post("/sites", json={"name": "Dépôt", "code": "DEPOT", "kind": "warehouse"})
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    created = owner.post(
        "/members",
        json={
            "email": "caissier@example.com",
            "full_name": "Caissier",
            "password": "Provisoire-123",
            "roles": [
                {"role_id": roles["viewer"]},
                {"role_id": roles["administrator"], "site_id": second_site.json()["id"]},
            ],
            "site_ids": [str(t.site_id), second_site.json()["id"]],
        },
    )
    assert created.status_code == 201, created.text

    member = api_for("caissier@example.com", password="Provisoire-123")
    assert (
        member.post(
            "/me/password",
            json={"current_password": "Provisoire-123", "new_password": "Definitif-456"},
        ).status_code
        == 204
    )

    caps = _caps(member)
    # Consultant : consultation, sans les utilisateurs ni le journal d'audit (exclusions).
    assert "organization.site.view" in caps["permissions"]
    assert "users.member.view" not in caps["permissions"]
    assert "audit.log.view" not in caps["permissions"]
    assert "users.member.manage" not in caps["permissions"]
    assert {s["id"] for s in caps["sites"]} == {str(t.site_id), second_site.json()["id"]}

    member.site_id = second_site.json()["id"]
    assert "users.member.manage" in _caps(member)["permissions"]  # rôle limité à ce site
    member.site_id = t.site_id
    assert "users.member.manage" not in _caps(member)["permissions"]


def test_site_not_assigned_is_refused(provision: Any, api_for: Any) -> None:
    t = provision("alpha")
    owner = api_for("owner@alpha.example.com")
    other = owner.post("/sites", json={"name": "Bobo", "code": "BOBO"}).json()
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    owner.post(
        "/members",
        json={
            "email": "vendeur@example.com",
            "full_name": "Vendeur",
            "password": "Provisoire-123",
            "roles": [{"role_id": roles["viewer"]}],
            "site_ids": [str(t.site_id)],
        },
    )
    member = api_for("vendeur@example.com", password="Provisoire-123")
    member.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": "Definitif-456"}
    )
    member.site_id = other["id"]
    response = member.get("/me/capabilities")
    assert response.status_code == 403
    assert response.json()["code"] == "site_access_denied"


def test_expired_subscription_keeps_read_and_blocks_sensitive_actions(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("alpha")
    owner_db.execute(
        text(
            "UPDATE subscriptions SET current_period_end = now() - interval '60 days' "
            "WHERE tenant_id = :t"
        ),
        {"t": t.tenant_id},
    )
    owner_db.commit()
    api = api_for("owner@alpha.example.com")  # la connexion reste possible
    caps = _caps(api)
    assert caps["subscription"]["status"] == "expired"
    assert set(caps["subscription"]["allowed_access"]) == {"read", "export", "billing"}
    assert "organization.site.view" in caps["permissions"]
    assert "organization.site.manage" in caps["restricted_permissions"]

    assert api.get("/sites").status_code == 200  # consultation
    assert api.get("/subscription").status_code == 200  # renouvellement
    blocked = api.post("/sites", json={"name": "Nouveau", "code": "NEW"})
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "subscription_restricted"
    # Aucune donnée supprimée.
    assert len(api.get("/sites").json()) == 1


def test_trial_expiry_and_past_due_grace(provision: Any, api_for: Any, owner_db: Session) -> None:
    t = provision("alpha", trial_days=14)
    api = api_for("owner@alpha.example.com")
    assert _caps(api)["subscription"]["status"] == "trial"
    owner_db.execute(
        text(
            "UPDATE subscriptions SET status = 'active', "
            "current_period_end = now() - interval '2 days'"
        )
    )
    owner_db.commit()
    caps = _caps(api)
    assert caps["subscription"]["status"] == "past_due"  # grâce de 15 jours (ENTREPRISE)
    assert "organization.site.manage" in caps["permissions"]
    assert t.tenant_id


def test_suspended_tenant_is_refused(provision: Any, api_for: Any, owner_db: Session) -> None:
    provision("alpha")
    api = api_for("owner@alpha.example.com")
    owner_db.execute(text("UPDATE tenants SET status = 'suspended'"))
    owner_db.commit()
    assert api.get("/me/capabilities").json()["code"] == "tenant_suspended"
