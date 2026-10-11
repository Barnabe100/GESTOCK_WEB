"""R4, décision A + C (ADR-0049 D8) — avertissement de cohérence des rôles PERSONNALISÉS.

Un rôle personnalisé qui contient ``restaurant.orders.order.serve`` ou ``.claim`` (pouvant
rendre destinataire de « commande prête ») sans ``organization.notification.view`` est
ENREGISTRÉ (aucun blocage) ; la réponse porte un avertissement ``notification_read_missing`` et
la liste des attributions actuelles (membre, site) où la lecture manque SUR CE SITE (une lecture
accordée seulement sur un autre site ne compte jamais). Règle des destinataires D8, permissions,
modèles de rôles et anti-escalade inchangés.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import PASSWORD, Api, add_site, login
from tests.test_restaurant_orders import MAQUIS, _api, _code, _ok

P = "restaurant.orders.order"
READ = "organization.notification.view"
CODE = "notification_read_missing"


@pytest.fixture
def org(provision: Any, oclient: TestClient) -> SimpleNamespace:
    t = provision("ro-warn", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-warn.example.com")
    other = _ok(
        add_site(owner, "Maquis Nord", "MNORD", "restaurant", business_profile_code=MAQUIS), 201
    )
    return SimpleNamespace(owner=owner, client=oclient, site=str(t.site_id), other_site=other["id"])


def _role(o: SimpleNamespace, name: str, permissions: list[str]) -> dict[str, Any]:
    return dict(_ok(o.owner.post("/roles", json={"name": name, "permissions": permissions}), 201))


def _warning(role: dict[str, Any]) -> dict[str, Any] | None:
    found = [w for w in role["warnings"] if w["code"] == CODE]
    return found[0] if found else None


def _member(o: SimpleNamespace, email: str, roles: list[dict[str, str]], **access: Any) -> str:
    body = {
        "email": email,
        "full_name": email.split("@")[0],
        "password": "Provisoire-123",
        "roles": roles,
        **({"all_sites": True} | access),
    }
    return str(_ok(o.owner.post("/members", json=body), 201)["id"])


def _template(o: SimpleNamespace, code: str) -> str:
    roles = {x["template_code"]: x["id"] for x in o.owner.get("/roles").json()}
    if code not in roles:
        created = o.owner.post("/roles/from-template", json={"template_code": code})
        roles[code] = _ok(created, 201)["id"]
    return roles[code]


def test_incomplete_role_is_saved_with_an_explicit_warning(org: SimpleNamespace) -> None:
    o = org
    role = _role(o, "Service sans avis", [f"{P}.view", f"{P}.serve"])
    warning = _warning(role)
    assert warning == {
        "code": CODE,
        "receiver_permissions": [f"{P}.serve"],
        "read_permission": READ,
        "affected": [],  # aucun membre encore
    }
    # Non bloquant : le rôle est enregistré tel quel.
    stored = _ok(o.owner.get(f"/roles/{role['id']}"))
    assert set(stored["permission_codes"]) == {f"{P}.view", f"{P}.serve"}
    assert _warning(stored) == warning  # consultation : même avertissement
    # ``claim`` seul (responsable) : même avertissement.
    claim = _role(o, "Prise sans avis", [f"{P}.view", f"{P}.claim"])
    assert _warning(claim)["receiver_permissions"] == [f"{P}.claim"]  # type: ignore[index]
    both = _role(o, "Service et prise", [f"{P}.view", f"{P}.serve", f"{P}.claim"])
    assert _warning(both)["receiver_permissions"] == [f"{P}.claim", f"{P}.serve"]  # type: ignore[index]


def test_complete_or_unrelated_roles_have_no_warning(org: SimpleNamespace) -> None:
    o = org
    complete = _role(o, "Service complet", [f"{P}.view", f"{P}.serve", READ])
    assert complete["warnings"] == []
    unrelated = _role(o, "Consultation", [f"{P}.view", "catalog.article.view"])
    assert unrelated["warnings"] == []
    # Rôles standard : jamais d'avertissement (et tous ceux qui rendent destinataire lisent).
    waiter = _ok(o.owner.get(f"/roles/{_template(o, 'waiter')}"))
    assert waiter["warnings"] == []
    # Listes : pas d'avertissement calculé.
    assert all(r["warnings"] == [] for r in o.owner.get("/roles").json())


def test_update_and_duplicate_follow_the_role_permissions(org: SimpleNamespace) -> None:
    o = org
    role = _role(o, "Runner", [f"{P}.view", f"{P}.serve"])
    fixed = _ok(
        o.owner.patch(
            f"/roles/{role['id']}", json={"permissions": [f"{P}.view", f"{P}.serve", READ]}
        )
    )
    assert fixed["warnings"] == []
    broken = _ok(
        o.owner.patch(f"/roles/{role['id']}", json={"permissions": [f"{P}.view", f"{P}.serve"]})
    )
    assert _warning(broken) is not None
    # Modification sans rapport (nom) : l'avertissement reste tant que la lecture manque.
    renamed = _ok(o.owner.patch(f"/roles/{role['id']}", json={"name": "Runner bis"}))
    assert _warning(renamed) is not None
    copy = _ok(o.owner.post(f"/roles/{role['id']}/duplicate", json={"name": "Runner copie"}), 201)
    assert _warning(copy) is not None


def test_affected_assignments_respect_site_scoped_permissions(org: SimpleNamespace) -> None:
    o = org
    role = _role(o, "Service", [f"{P}.view", f"{P}.serve"])
    reader = _role(o, "Lecture des avis", [READ])
    waiter = _template(o, "waiter")  # lecture comprise
    # 1. Rôle limité au site principal ; lecture seulement sur l'AUTRE site : concerné.
    elsewhere = _member(
        o,
        "ailleurs@ro-warn.example.com",
        [{"role_id": role["id"], "site_id": o.site}, {"role_id": waiter, "site_id": o.other_site}],
    )
    # 2. Rôle de tout le tenant, lecture seulement sur l'autre site : concerné sur le site
    #    principal seulement.
    partial = _member(
        o,
        "partiel@ro-warn.example.com",
        [{"role_id": role["id"]}, {"role_id": reader["id"], "site_id": o.other_site}],
    )
    # 3. Lecture sur le MÊME site (rôle limité à ce site) : pas concerné.
    same_site = _member(
        o,
        "memesite@ro-warn.example.com",
        [{"role_id": role["id"], "site_id": o.site}, {"role_id": reader["id"], "site_id": o.site}],
    )
    # 4. Lecture sur tout le tenant : pas concerné.
    covered = _member(
        o, "couvert@ro-warn.example.com", [{"role_id": role["id"]}, {"role_id": reader["id"]}]
    )
    # 5. Membre limité au site principal (accès), rôle de tout le tenant, sans lecture.
    local = _member(
        o,
        "local@ro-warn.example.com",
        [{"role_id": role["id"]}],
        all_sites=False,
        site_ids=[o.site],
    )
    warning = _warning(_ok(o.owner.get(f"/roles/{role['id']}")))
    assert warning is not None
    affected = {(a["membership_id"], a["site_id"]) for a in warning["affected"]}
    assert affected == {(elsewhere, o.site), (partial, o.site), (local, o.site)}
    assert all(a["site_name"] for a in warning["affected"])
    assert same_site not in {m for m, _ in affected}
    assert covered not in {m for m, _ in affected}
    # Le rôle de lecture ajouté au rôle lui-même : plus aucun avertissement.
    fixed = _ok(
        o.owner.patch(
            f"/roles/{role['id']}", json={"permissions": [f"{P}.view", f"{P}.serve", READ]}
        )
    )
    assert fixed["warnings"] == []


def test_member_without_any_other_role_is_affected_on_each_accessible_site(
    org: SimpleNamespace,
) -> None:
    o = org
    role = _role(o, "Service", [f"{P}.view", f"{P}.serve"])
    alone = _member(o, "seul@ro-warn.example.com", [{"role_id": role["id"]}])
    warning = _warning(_ok(o.owner.get(f"/roles/{role['id']}")))
    assert warning is not None
    assert {(a["membership_id"], a["site_id"]) for a in warning["affected"]} == {
        (alone, o.site),
        (alone, o.other_site),
    }
    # Rôle désactivé : n'accorde rien, aucune attribution concernée ; l'avertissement de
    # configuration demeure.
    _ok(o.owner.post(f"/roles/{role['id']}/deactivate", json={"confirm": True}))
    inactive = _warning(_ok(o.owner.get(f"/roles/{role['id']}")))
    assert inactive is not None and inactive["affected"] == []


def test_anti_escalation_and_recipient_rule_are_unchanged(
    org: SimpleNamespace, client: TestClient
) -> None:
    """L'avertissement n'ouvre ni ne ferme aucun droit : un administrateur non propriétaire
    reste soumis à l'anti-escalade (ici, il détient ``serve`` : création permise, avec
    avertissement) ; un membre sans ``users.role.manage`` ne peut toujours pas créer de rôle."""
    o = org
    admin_role = {x["template_code"]: x["id"] for x in o.owner.get("/roles").json()}
    _member(o, "admin@ro-warn.example.com", [{"role_id": admin_role["administrator"]}])
    temporary = _api(o.client, "admin@ro-warn.example.com", "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    admin = _api(o.client, "admin@ro-warn.example.com")
    created = _ok(
        admin.post(
            "/roles", json={"name": "Par admin", "permissions": [f"{P}.view", f"{P}.serve"]}
        ),
        201,
    )
    assert _warning(created) is not None
    _member(o, "vendeur@ro-warn.example.com", [{"role_id": admin_role["seller"]}])
    temporary = _api(o.client, "vendeur@ro-warn.example.com", "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    seller = _api(o.client, "vendeur@ro-warn.example.com")
    refused = seller.post("/roles", json={"name": "Interdit", "permissions": [f"{P}.serve"]})
    assert _code(refused) == (403, "permission_denied")


def test_production_planned_module_still_warns(
    provision: Any, api_for: Any, client: TestClient
) -> None:
    """Module planifié (production) : le propriétaire peut enregistrer ces permissions
    (comportement existant, inerte) ; l'avertissement est déjà donné, en vue de l'activation."""
    provision("ro-warn-prod", profile=MAQUIS)
    owner = api_for("owner@ro-warn-prod.example.com")
    role = owner.post(
        "/roles", json={"name": "Service", "permissions": [f"{P}.view", f"{P}.serve"]}
    )
    assert role.status_code == 201, role.text
    assert _warning(role.json()) is not None
    assert Api(client, login(client, "owner@ro-warn-prod.example.com").json()["access_token"])
