"""Palier R2-C (ADR-0049 D7, D12, D14 Q5) — prise en charge, protection, délai entre prises,
réattribution motivée, modèles Serveur et Préparateur.

Tous les contrôles sont exercés par l'API (appels directs, sans interface) avec le registre de
test où ``restaurant.orders`` est disponible (N1) ; la production le garde planifié.
"""

import threading
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.registry import ModuleStatus, get_registry
from tests.conftest import PASSWORD, Api, add_site, set_site_module
from tests.test_restaurant_orders import MAQUIS, ORDERS, _api, _code, _line, _menu, _ok, _order

P = "restaurant.orders.order"


def _join(r: SimpleNamespace, email: str, role_ids: list[str], **access: Any) -> SimpleNamespace:
    """Membre ajouté avec des rôles donnés ; renvoie son client, son utilisateur et son
    appartenance."""
    body = {
        "email": email,
        "full_name": email.split("@")[0],
        "password": "Provisoire-123",
        "roles": [{"role_id": role_id} for role_id in role_ids],
        **({"all_sites": True} | access),
    }
    member = _ok(r.owner.post("/members", json=body), 201)
    temporary = _api(r.client, email, "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return SimpleNamespace(
        api=_api(r.client, email), user_id=member["user_id"], membership_id=member["id"]
    )


def _roles(r: SimpleNamespace) -> dict[str, str]:
    return {x["template_code"] or x["name"]: x["id"] for x in r.owner.get("/roles").json()}


def _settings(r: SimpleNamespace, protection: int = 5, cooldown: int = 0) -> None:
    _ok(
        r.owner.put(
            f"/restaurant/settings/{r.site}",
            json={
                "payment_timing": "AT_END",
                "claim_protection_minutes": protection,
                "claim_cooldown_minutes": cooldown,
            },
        )
    )


def _events(api: Api, order_id: str, kind: str) -> list[dict[str, Any]]:
    events = _ok(api.get(f"/restaurant/orders/{order_id}/events"))
    return [e for e in events if e["event_type"] == kind]


def _audit(api: Api, action: str) -> list[dict[str, Any]]:
    return _ok(api.get("/audit-logs", params={"action": action, "limit": 50}))["items"]


@pytest.fixture
def resto(provision: Any, oclient: TestClient) -> SimpleNamespace:
    t = provision("ro-claim", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(oclient, "owner@ro-claim.example.com")
    site = str(t.site_id)
    return SimpleNamespace(
        tenant=t.tenant_id, site=site, owner=owner, client=oclient, menu=_menu(owner, site, "C")
    )


# --- Prise en charge et protection --------------------------------------------------------------


def test_claim_protection_and_takeover(resto: SimpleNamespace, owner_db: Session) -> None:
    r = resto
    roles = _roles(r)
    alice = _join(r, "alice@ro-claim.example.com", [roles["seller"]])
    bruno = _join(r, "bruno@ro-claim.example.com", [roles["seller"]])
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    assert order["assigned_user_id"] is None and order["assigned_at"] is None
    base = f"/restaurant/orders/{order['id']}"

    taken = _ok(alice.api.post(f"{base}/claim"))
    assert taken["assigned_user_id"] == alice.user_id
    assert taken["assigned_name"] == "alice" and taken["assigned_at"] is not None
    assert taken["version"] == order["version"] + 1
    # Protection (5 min par défaut) : un autre employé est refusé, le responsable est nommé.
    refused = bruno.api.post(f"{base}/claim")
    assert _code(refused) == (409, "order_claim_protected")
    assert refused.json()["assigned_name"] == "alice" and refused.json()["protected_until"]
    # Reprise par le responsable : rien ne change (aucun évènement, même version).
    again = _ok(alice.api.post(f"{base}/claim"))
    assert again["version"] == taken["version"]
    assert len(_events(r.owner, order["id"], "CLAIMED")) == 1
    # Protection écoulée : la prise passe à un autre employé.
    owner_db.execute(
        text(
            "UPDATE restaurant_orders SET assigned_at = assigned_at - interval '6 minutes' "
            "WHERE id = :i"
        ),
        {"i": order["id"]},
    )
    owner_db.commit()
    moved = _ok(bruno.api.post(f"{base}/claim"))
    assert moved["assigned_user_id"] == bruno.user_id
    claims = _events(r.owner, order["id"], "CLAIMED")
    assert [e["actor_user_id"] for e in claims] == [alice.user_id, bruno.user_id]
    assert claims[1]["data"]["previous_user_id"] == alice.user_id
    audits = _audit(r.owner, "restaurant_order.claimed")
    assert {a["data"]["previous_name"] for a in audits} == {None, "alice"}
    # Protection désactivée (0) : prise immédiate par un autre employé.
    _settings(r, protection=0)
    assert _ok(alice.api.post(f"{base}/claim"))["assigned_user_id"] == alice.user_id


def test_claim_rules_by_order_state_and_permission(resto: SimpleNamespace) -> None:
    r = resto
    roles = _roles(r)
    viewer = _join(r, "lecteur@ro-claim.example.com", [roles["viewer"]])
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    # Appel direct sans la permission : refus serveur (route et service).
    assert _code(viewer.api.post(f"{base}/claim")) == (403, "permission_denied")
    # Commande servie mais non réglée : toujours ouverte, donc « prenable ».
    for action in ("start", "ready", "serve"):
        _ok(r.owner.post(f"{base}/{action}"))
    assert _ok(r.owner.post(f"{base}/claim"))["assigned_user_id"] is not None
    cancelled = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    _ok(r.owner.post(f"/restaurant/orders/{cancelled['id']}/cancel", json={"reason": "Erreur"}))
    assert _code(r.owner.post(f"/restaurant/orders/{cancelled['id']}/claim")) == (
        409,
        "order_cancelled",
    )
    unknown = r.owner.post(f"/restaurant/orders/{uuid.uuid4()}/claim")
    assert _code(unknown) == (404, "order_not_found")


def test_claim_scope_follows_sites_and_companies(
    resto: SimpleNamespace, provision: Any, oclient: TestClient
) -> None:
    r = resto
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    other = _ok(
        add_site(r.owner, "Maquis 2", "MAQ2", "restaurant", business_profile_code=MAQUIS), 201
    )
    limited = _join(
        r,
        "site2@ro-claim.example.com",
        [_roles(r)["seller"]],
        all_sites=False,
        site_ids=[other["id"]],
    )
    assert _code(limited.api.post(f"{base}/claim")) == (404, "order_not_found")
    selected = Api(r.owner.client, r.owner.token, uuid.UUID(other["id"]))
    assert _code(selected.post(f"{base}/claim")) == (403, "site_mismatch")
    t2 = provision("ro-claim-2", profile=MAQUIS, plan="ENTREPRISE")
    stranger = _api(oclient, "owner@ro-claim-2.example.com")
    assert _code(stranger.post(f"{base}/claim")) == (404, "order_not_found")
    assert t2.tenant_id != r.tenant
    # Aucun responsable posé par ces refus.
    assert _ok(r.owner.get(base))["assigned_user_id"] is None


# --- Délai entre deux prises --------------------------------------------------------------------


def test_cooldown_counts_only_explicit_claims_per_site(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    roles = _roles(r)
    manager = _join(r, "gerant@ro-claim.example.com", [roles["manager"]])
    alice = _join(r, "alice@ro-claim.example.com", [roles["seller"]])
    _settings(r, cooldown=10)
    first, second, third = (
        _ok(_order(alice.api, r.site, [_line(r.menu.unit)]), 201) for _ in range(3)
    )
    # La création ne compte pas : la première prise passe.
    _ok(alice.api.post(f"/restaurant/orders/{first['id']}/claim"))
    refused = alice.api.post(f"/restaurant/orders/{second['id']}/claim")
    assert _code(refused) == (409, "claim_cooldown_active")
    assert refused.json()["available_at"]
    # Une réattribution vers l'employé ne compte pas non plus (ni prise, ni remise à zéro).
    _ok(
        manager.api.post(
            f"/restaurant/orders/{second['id']}/reassign",
            json={"assignee_user_id": alice.user_id, "reason": "Renfort"},
        )
    )
    assert _code(alice.api.post(f"/restaurant/orders/{third['id']}/claim"))[1] == (
        "claim_cooldown_active"
    )
    # Un autre employé n'est pas concerné par le délai d'Alice.
    _ok(manager.api.post(f"/restaurant/orders/{third['id']}/claim"))
    # Délai écoulé (dernière prise lue dans l'historique) : nouvelle prise permise.
    owner_db.execute(
        text(
            "UPDATE restaurant_order_events SET occurred_at = occurred_at - interval '11 minutes' "
            "WHERE event_type = 'CLAIMED' AND actor_user_id = :u"
        ),
        {"u": alice.user_id},
    )
    owner_db.commit()
    fourth = _ok(_order(alice.api, r.site, [_line(r.menu.unit)]), 201)
    _ok(alice.api.post(f"/restaurant/orders/{fourth['id']}/claim"))
    # Le délai est propre au site : une prise sur un autre site n'est pas freinée.
    other = _ok(
        add_site(r.owner, "Maquis 3", "MAQ3", "restaurant", business_profile_code=MAQUIS), 201
    )
    other_menu = _menu(r.owner, other["id"], "C3")
    remote = _ok(_order(alice.api, other["id"], [_line(other_menu.unit)]), 201)
    _ok(alice.api.post(f"/restaurant/orders/{remote['id']}/claim"))


# --- Concurrence (API) --------------------------------------------------------------------------


def _race(calls: list[Any]) -> list[Any]:
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def run(index: int) -> None:
        barrier.wait()
        results[index] = calls[index]()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_simultaneous_claims_exactly_one_succeeds(resto: SimpleNamespace) -> None:
    r = resto
    seller = _roles(r)["seller"]
    members = [_join(r, f"serveur{i}@ro-claim.example.com", [seller]) for i in range(10)]
    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}/claim"
    results = _race([lambda m=m: m.api.post(base) for m in members])
    winners = [res for res in results if res.status_code == 200]
    assert len(winners) == 1
    assert (
        sorted(_code(res) for res in results if res.status_code != 200)
        == [(409, "order_claim_protected")] * 9
    )
    winner = winners[0].json()["assigned_user_id"]
    assert _ok(r.owner.get(f"/restaurant/orders/{order['id']}"))["assigned_user_id"] == winner
    assert len(_events(r.owner, order["id"], "CLAIMED")) == 1


def test_simultaneous_claims_of_one_employee_respect_the_cooldown(
    resto: SimpleNamespace,
) -> None:
    r = resto
    alice = _join(r, "alice@ro-claim.example.com", [_roles(r)["seller"]])
    _settings(r, cooldown=10)
    orders = [_ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201) for _ in range(5)]
    results = _race(
        [lambda o=o: alice.api.post(f"/restaurant/orders/{o['id']}/claim") for o in orders]
    )
    assert sorted(res.status_code for res in results) == [200, 409, 409, 409, 409]
    assert {_code(res) for res in results if res.status_code == 409} == {
        (409, "claim_cooldown_active")
    }


# --- Réattribution ------------------------------------------------------------------------------


def test_reassignment_is_immediate_justified_and_checked(
    resto: SimpleNamespace, provision: Any
) -> None:
    r = resto
    roles = _roles(r)
    manager = _join(r, "gerant@ro-claim.example.com", [roles["manager"]])
    alice = _join(r, "alice@ro-claim.example.com", [roles["seller"]])
    bruno = _join(r, "bruno@ro-claim.example.com", [roles["seller"]])
    viewer = _join(r, "lecteur@ro-claim.example.com", [roles["viewer"]])
    other = _ok(
        add_site(r.owner, "Maquis 2", "MAQ2", "restaurant", business_profile_code=MAQUIS), 201
    )
    elsewhere = _join(
        r,
        "ailleurs@ro-claim.example.com",
        [roles["seller"]],
        all_sites=False,
        site_ids=[other["id"]],
    )
    suspended = _join(r, "parti@ro-claim.example.com", [roles["seller"]])
    _ok(r.owner.post(f"/members/{suspended.membership_id}/deactivate"))
    custom = _ok(
        r.owner.post(
            "/roles",
            json={"name": "Chef de rang", "permissions": [f"{P}.view", f"{P}.claim"]},
        ),
        201,
    )
    chief = _join(r, "chef@ro-claim.example.com", [custom["id"]])
    t2 = provision("ro-claim-3", profile=MAQUIS, plan="ENTREPRISE")

    order = _ok(_order(r.owner, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    _ok(alice.api.post(f"{base}/claim"))

    def reassign(api: Api, user_id: Any, reason: str = "Fin de service") -> Any:
        return api.post(
            f"{base}/reassign", json={"assignee_user_id": str(user_id), "reason": reason}
        )

    # Permission : le Vendeur ne réattribue pas (appel direct refusé).
    assert _code(reassign(alice.api, bruno.user_id)) == (403, "permission_denied")
    # Motif obligatoire.
    assert reassign(manager.api, bruno.user_id, "   ").status_code == 422
    # Nouveau responsable sans ``order.claim`` effectif sur le site : refus.
    for user_id in (
        viewer.user_id,  # aucune permission de prise
        elsewhere.user_id,  # rôle valable sur un autre site seulement
        suspended.user_id,  # appartenance suspendue
        t2.owner_user_id,  # membre d'une autre entreprise
        uuid.uuid4(),  # inconnu
    ):
        assert _code(reassign(manager.api, user_id)) == (422, "assignee_not_eligible")
    # Immédiate malgré la protection d'Alice ; motif dans l'historique et l'audit.
    moved = _ok(reassign(manager.api, bruno.user_id))
    assert moved["assigned_user_id"] == bruno.user_id
    event = _events(r.owner, order["id"], "REASSIGNED")[0]
    assert event["reason"] == "Fin de service" and event["actor_user_id"] == manager.user_id
    assert event["data"] == {"previous_user_id": alice.user_id, "assignee_user_id": bruno.user_id}
    audit = _audit(r.owner, "restaurant_order.reassigned")[0]
    assert audit["data"]["reason"] == "Fin de service"
    assert (audit["data"]["previous_name"], audit["data"]["assignee_name"]) == ("alice", "bruno")
    # Rôle personnalisé portant ``order.claim`` : éligible (jamais un nom de rôle).
    assert _ok(reassign(manager.api, chief.user_id))["assigned_user_id"] == chief.user_id
    # Même responsable : rien ne change.
    same = _ok(reassign(manager.api, chief.user_id))
    assert len(_events(r.owner, order["id"], "REASSIGNED")) == 2
    assert same["version"] == _ok(r.owner.get(base))["version"]
    # Commande annulée : plus de réattribution.
    _ok(r.owner.post(f"{base}/cancel", json={"reason": "Client parti"}))
    assert _code(reassign(manager.api, bruno.user_id)) == (409, "order_cancelled")


# --- Modèles Serveur et Préparateur -------------------------------------------------------------


def test_waiter_and_preparer_templates_on_demand(resto: SimpleNamespace) -> None:
    r = resto
    # Jamais provisionnés (nouvelle entreprise de restauration).
    assert {"waiter", "preparer"}.isdisjoint(_roles(r))
    templates = {t["code"]: t for t in r.owner.get("/role-templates").json()}
    assert templates["waiter"]["instantiated"] is False
    waiter_role = _ok(r.owner.post("/roles/from-template", json={"template_code": "waiter"}), 201)
    preparer_role = _ok(
        r.owner.post("/roles/from-template", json={"template_code": "preparer"}), 201
    )
    assert set(waiter_role["permission_codes"]) == {
        "restaurant.menu.view",
        "restaurant.menu.availability",
        f"{P}.view",
        f"{P}.create",
        f"{P}.claim",
        f"{P}.serve",
    }
    assert set(preparer_role["permission_codes"]) == {
        "restaurant.menu.view",
        "restaurant.menu.availability",
        f"{P}.view",
        f"{P}.prepare",
    }
    again = r.owner.post("/roles/from-template", json={"template_code": "waiter"})
    assert _code(again) == (409, "role_template_exists")

    waiter = _join(r, "serveur@ro-claim.example.com", [waiter_role["id"]])
    preparer = _join(r, "cuisine@ro-claim.example.com", [preparer_role["id"]])
    order = _ok(_order(waiter.api, r.site, [_line(r.menu.unit)]), 201)
    base = f"/restaurant/orders/{order['id']}"
    _ok(waiter.api.post(f"{base}/claim"))
    # Préparateur : prépare, ne prend ni ne sert ; Serveur : sert, ne prépare pas.
    assert _code(preparer.api.post(f"{base}/claim")) == (403, "permission_denied")
    assert _code(waiter.api.post(f"{base}/start")) == (403, "permission_denied")
    _ok(preparer.api.post(f"{base}/start"))
    _ok(preparer.api.post(f"{base}/ready"))
    assert _code(preparer.api.post(f"{base}/serve")) == (403, "permission_denied")
    _ok(waiter.api.post(f"{base}/serve"))
    # Serveur : aucune permission d'encaissement ni d'annulation.
    caps = set(waiter.api.get("/me/capabilities").json()["permissions"])
    assert not [c for c in caps if c.startswith(("sales.", "cash_register.", "pos."))]
    assert f"{P}.cancel" not in caps and f"{P}.reassign" not in caps
    assert waiter.api.post("/sales", json={"site_id": r.site, "lines": []}).status_code == 403


def test_template_refused_when_a_custom_role_has_its_name(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    # Le nom d'un modèle est réservé aux nouveaux rôles personnalisés…
    reserved = r.owner.post("/roles", json={"name": "préparateur", "permissions": []})
    assert _code(reserved) == (409, "role_name_reserved")
    # …mais un rôle personnalisé créé AVANT l'arrivée du modèle peut porter ce nom.
    custom = _ok(
        r.owner.post("/roles", json={"name": "Équipe salle", "permissions": [f"{P}.view"]}), 201
    )
    owner_db.execute(text("UPDATE roles SET name = 'SERVEUR' WHERE id = :i"), {"i": custom["id"]})
    owner_db.commit()
    refused = r.owner.post("/roles/from-template", json={"template_code": "waiter"})
    assert _code(refused) == (409, "role_name_taken")
    assert refused.json()["role_id"] == custom["id"] and refused.json()["role_name"] == "SERVEUR"
    # Aucun renommage automatique, aucun rôle créé.
    names = {x["name"] for x in r.owner.get("/roles").json()}
    assert "SERVEUR" in names and "Serveur" not in names
    # L'autre modèle reste disponible.
    _ok(r.owner.post("/roles/from-template", json={"template_code": "preparer"}), 201)


# --- Permissions des modules planifiés : aucune régression ailleurs -----------------------------


def test_planned_filter_only_removes_planned_permissions() -> None:
    registry = get_registry()  # registre de production
    modules = registry.all()
    planned_with_permissions = {
        m.code for m in modules if m.status is ModuleStatus.PLANNED and m.permissions
    }
    assert planned_with_permissions == {ORDERS}
    every = [m.code for m in modules]
    features = {f for m in modules for f in m.features}
    expected = {
        p.code for m in modules if m.status is ModuleStatus.AVAILABLE for p in m.permissions
    }
    assert set(registry.available_permissions(every, features)) == expected
    assert not [c for c in expected if c.startswith(f"{ORDERS}.")]


def test_owner_keeps_every_permission_of_delivered_modules(provision: Any, api_for: Any) -> None:
    registry = get_registry()
    for slug, profile in (("ro-reg-retail", "retail.alimentation"), ("ro-reg-maquis", MAQUIS)):
        provision(slug, profile=profile, plan="ENTREPRISE")
        caps = api_for(f"owner@{slug}.example.com").get("/me/capabilities").json()
        modules = {m["code"] for m in caps["modules"]}
        features = set(caps["features"])
        expected = {
            p.code
            for code in modules
            if registry.get(code).status is ModuleStatus.AVAILABLE
            for p in registry.get(code).permissions
            if p.feature is None or p.feature in features
        }
        assert set(caps["permissions"]) | set(caps["restricted_permissions"]) == expected


def test_claim_permissions_follow_the_module_activation(resto: SimpleNamespace) -> None:
    r = resto
    caps = set(r.owner.get("/me/capabilities").json()["permissions"])
    assert {f"{P}.claim", f"{P}.reassign"} <= caps
    assert set_site_module(r.owner, uuid.UUID(r.site), ORDERS, False).status_code == 204
    selected = Api(r.owner.client, r.owner.token, uuid.UUID(r.site))
    caps = set(selected.get("/me/capabilities").json()["permissions"])
    assert not [c for c in caps if c.startswith(f"{ORDERS}.")]
