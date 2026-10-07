"""Phase 3.3-B1 — 1 site = 1 abonnement (ADR-0033).

Rattachement de l'abonnement pris à l'inscription au premier site, abonnement « en attente
d'activation » de tout nouveau site (plan publié choisi par l'administrateur, sans essai),
capacités par site (écriture limitée aux sites dont l'abonnement l'autorise, lecture
consolidée), limites par site, fonctionnalités exigées des deux sites d'un transfert,
contraintes en base (clé étrangère composite, unicités), vues entreprise et console.
"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import (
    CONSOLE_PREFIX,
    Api,
    activate_site,
    add_site,
    console_login,
)
from tests.test_members_roles import _activate
from tests.test_signup import _api, _signup, offers  # noqa: F401

ARTICLE = {
    "reference": "RIZ-25",
    "designation": "Riz 25 kg",
    "unit": "sac",
    "purchase_price": "10000",
    "sale_price": "12500",
}


def _subscription_of(owner_db: Session, site_id: str | uuid.UUID) -> Any:
    return owner_db.execute(
        text("SELECT * FROM subscriptions WHERE site_id = :s"), {"s": str(site_id)}
    ).one()


def _article(api: Api, site_ids: list[str] | None = None) -> Any:
    category = api.post("/catalog/categories", json={"name": "Céréales"}).json()["id"]
    return api.post(
        "/catalog/articles",
        json=ARTICLE | {"category_id": category, "site_ids": site_ids or []},
    )


def _on_site(api: Api, site_id: str) -> Api:
    return Api(api.client, api.token, site_id=uuid.UUID(site_id))


# --- Rattachement et création ------------------------------------------------------------


def test_provisioned_first_site_owns_the_subscription(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    tenant = provision("alpha", plan="STANDARD")
    row = _subscription_of(owner_db, tenant.site_id)
    assert row.id == tenant.subscription_id
    assert row.requested_activations == 1
    listed = api_for("owner@alpha.example.com").get("/subscriptions").json()
    assert [(s["site"]["id"], s["plan_code"]) for s in listed] == [
        (str(tenant.site_id), "STANDARD")
    ]


def test_signup_subscription_is_attached_to_the_first_site(
    client: TestClient,
    offers: None,  # noqa: F811
    owner_db: Session,
) -> None:
    api = _api(client, _signup(client))
    [pending] = api.get("/subscriptions").json()
    assert pending["site"] is None and pending["status"] == "pending_activation"

    chosen = api.post(
        "/sites",
        json={
            "name": "Boutique",
            "code": "BTQ",
            "plan_code": "STANDARD",
            "billing_period": "monthly",
        },
    )
    assert chosen.status_code == 422
    assert chosen.json()["code"] == "site_subscription_preselected"

    site = api.post("/sites", json={"name": "Boutique", "code": "BTQ", "requested_activations": 3})
    assert site.status_code == 201, site.text
    [attached] = api.get("/subscriptions").json()
    assert attached["id"] == pending["id"]  # même abonnement, désormais celui du site
    assert attached["site"]["id"] == site.json()["id"]
    assert attached["requested_activations"] == 3
    assert attached["status"] == "pending_activation"  # rattacher n'active rien
    tenant_id = owner_db.execute(
        text("SELECT tenant_id FROM subscriptions WHERE id = :i"), {"i": pending["id"]}
    ).scalar_one()
    actions = owner_db.scalars(
        text("SELECT action FROM audit_logs WHERE tenant_id = :t"), {"t": tenant_id}
    ).all()
    assert "subscription.site_attached" in actions


def test_a_new_site_has_its_own_pending_subscription(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    tenant = provision("alpha", plan="ENTREPRISE")
    owner = api_for("owner@alpha.example.com")
    missing = owner.post("/sites", json={"name": "Dépôt", "code": "DEP"})
    assert missing.status_code == 422
    assert missing.json()["code"] == "site_plan_required"
    unpublished = owner.post(
        "/sites",
        json={"name": "Dépôt", "code": "DEP", "plan_code": "STANDARD", "billing_period": "monthly"},
    )
    assert unpublished.status_code == 422
    assert unpublished.json()["code"] == "plan_not_available"
    for invalid in ({"requested_activations": 0}, {"plan_code": "DEMO", "billing_period": "x"}):
        body = {"name": "Dépôt", "code": "DEP", **invalid}
        assert owner.post("/sites", json=body).status_code == 422
    assert owner_db.scalar(text("SELECT count(*) FROM sites")) == 1

    depot = add_site(owner, "Dépôt", "DEP", plan="STANDARD", active=False, requested_activations=2)
    assert depot.status_code == 201, depot.text
    row = _subscription_of(owner_db, depot.json()["id"])
    assert (row.plan_code, row.status, row.requested_activations) == (
        "STANDARD",
        "pending_activation",
        2,
    )
    assert (row.price_at_subscription, row.currency_at_subscription) == (10000, "XOF")
    assert row.id != tenant.subscription_id
    # L'abonnement du premier site n'a pas changé.
    assert _subscription_of(owner_db, tenant.site_id).status == "active"
    audit = owner_db.execute(
        text("SELECT site_id, data FROM audit_logs WHERE action = 'subscription.created'")
    ).one()
    assert str(audit.site_id) == depot.json()["id"]
    assert audit.data["plan"] == "STANDARD" and audit.data["requested_activations"] == 2


# --- Capacités par site ------------------------------------------------------------------


def test_writes_follow_the_subscription_of_each_site(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    tenant = provision("alpha", plan="ENTREPRISE", profile="retail.quincaillerie")
    owner = api_for("owner@alpha.example.com")
    main = str(tenant.site_id)
    depot = add_site(owner, "Dépôt", "DEP", "warehouse", active=False).json()["id"]
    # Données de l'entreprise ; assortiment des deux sites (ADR-0046 : préparable même sur un
    # site en attente d'activation, permission de nature ``admin``).
    article = _article(owner, [main, depot])
    assert article.status_code == 201, article.text
    supplier = owner.post("/suppliers", json={"name": "Faso Import"}).json()["id"]
    body = {
        "supplier_id": supplier,
        "lines": [{"article_id": article.json()["id"], "quantity": "5", "unit_cost": "10000"}],
    }

    caps = owner.get("/me/capabilities").json()
    assert {s["id"]: s["subscription_status"] for s in caps["sites"]} == {
        main: "active",
        depot: "pending_activation",
    }
    # Site en attente d'activation : aucune opération, qu'il soit sélectionné…
    on_depot = _on_site(owner, depot)
    pending_caps = on_depot.get("/me/capabilities").json()
    assert pending_caps["subscription"]["status"] == "pending_activation"
    assert "stock.entry.create" in pending_caps["restricted_permissions"]
    selected = on_depot.post("/stock/entries", json=body | {"site_id": depot})
    assert selected.status_code == 403
    assert selected.json()["code"] == "subscription_restricted"
    # … ou désigné depuis la vue consolidée de l'entreprise.
    consolidated = owner.post("/stock/entries", json=body | {"site_id": depot})
    assert consolidated.status_code == 403
    assert consolidated.json()["code"] == "subscription_restricted"
    assert consolidated.json()["site_id"] == depot
    # Le site actif reste pleinement opérationnel.
    assert owner.post("/stock/entries", json=body | {"site_id": main}).status_code == 201
    assert _on_site(owner, main).post("/stock/entries", json=body).status_code == 201
    # Lecture consolidée : tous les sites accessibles.
    assert owner.get("/stock/entries").json()["total"] == 2

    activate_site(depot)  # paiement confirmé + licence (3.3-B)
    draft = owner.post("/stock/entries", json=body | {"site_id": depot})
    assert draft.status_code == 201
    # Document existant d'un site dont l'abonnement a expiré : consultable, plus validable.
    owner_db.execute(
        text(
            "UPDATE subscriptions SET current_period_end = now() - interval '60 days' "
            "WHERE site_id = :s"
        ),
        {"s": depot},
    )
    owner_db.commit()
    entry_id = draft.json()["id"]
    assert owner.get(f"/stock/entries/{entry_id}").status_code == 200
    refused = owner.post(f"/stock/entries/{entry_id}/validate")
    assert refused.status_code == 403
    assert refused.json()["code"] == "subscription_restricted"


def test_site_subscription_status_does_not_leak_to_other_sites(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    tenant = provision("alpha", plan="ENTREPRISE")
    owner = api_for("owner@alpha.example.com")
    depot = add_site(owner, "Dépôt", "DEP").json()["id"]
    owner_db.execute(
        text(
            "UPDATE subscriptions SET status = 'active', "
            "current_period_end = now() - interval '60 days' WHERE site_id = :s"
        ),
        {"s": str(tenant.site_id)},
    )
    owner_db.commit()
    main_caps = _on_site(owner, str(tenant.site_id)).get("/me/capabilities").json()
    assert main_caps["subscription"]["status"] == "expired"
    depot_caps = _on_site(owner, depot).get("/me/capabilities").json()
    assert depot_caps["subscription"]["status"] == "active"
    # Données de l'entreprise : au moins un site actif.
    assert _article(owner).status_code == 201


def test_transfers_need_the_feature_on_both_sites(provision: Any, api_for: Any) -> None:
    tenant = provision("alpha", plan="ENTREPRISE", profile="retail.quincaillerie")
    owner = api_for("owner@alpha.example.com")
    standard = add_site(owner, "Annexe", "ANX", plan="STANDARD").json()["id"]
    article = _article(owner).json()["id"]
    response = owner.post(
        "/stock/transfers",
        json={
            "source_site_id": str(tenant.site_id),
            "destination_site_id": standard,
            "lines": [{"article_id": article, "quantity": "1"}],
        },
    )
    assert response.status_code == 403, response.text


def test_subscriptions_are_limited_to_accessible_sites(
    provision: Any, api_for: Any, client: TestClient
) -> None:
    provision("alpha", plan="ENTREPRISE")
    owner = api_for("owner@alpha.example.com")
    depot = add_site(owner, "Dépôt", "DEP").json()["id"]
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    created = owner.post(
        "/members",
        json={
            "email": "gerant@alpha.example.com",
            "full_name": "Gérant",
            "password": "Provisoire-123",
            "roles": [{"role_id": roles["viewer"]}],
            "site_ids": [depot],
        },
    )
    assert created.status_code == 201, created.text
    _activate(client, "gerant@alpha.example.com")
    manager = api_for("gerant@alpha.example.com")
    assert [s["site"]["id"] for s in manager.get("/subscriptions").json()] == [depot]
    assert len(owner.get("/subscriptions").json()) == 2


# --- Base de données ---------------------------------------------------------------------


def test_database_binds_each_subscription_to_one_site_of_its_tenant(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    a = provision("alpha")
    b = provision("beta")
    columns = (
        "(id, tenant_id, site_id, plan_code, billing_period, status, started_at, "
        "current_period_start, current_period_end)"
    )
    values = (
        "(gen_random_uuid(), :t, :s, 'STANDARD', 'monthly', 'pending_activation', "
        "now(), now(), now())"
    )
    for tenant, site, constraint in (
        (a.tenant_id, b.site_id, "fk_subscriptions_tenant_id_site_id_sites"),  # autre tenant
        (a.tenant_id, a.site_id, "uq_subscriptions_tenant_id_site_id"),  # second abonnement
    ):
        with pytest.raises(IntegrityError, match=constraint):
            owner_db.execute(
                text(f"INSERT INTO subscriptions {columns} VALUES {values}"),
                {"t": tenant, "s": site},
            )
        owner_db.rollback()
    insert = text(f"INSERT INTO subscriptions {columns} VALUES {values}")
    owner_db.execute(insert, {"t": a.tenant_id, "s": None})
    with pytest.raises(IntegrityError, match="uq_subscriptions_unattached"):
        owner_db.execute(insert, {"t": a.tenant_id, "s": None})
    owner_db.rollback()
    with pytest.raises(IntegrityError, match="requested_activations_positive"):
        owner_db.execute(
            text("UPDATE subscriptions SET requested_activations = 0 WHERE tenant_id = :t"),
            {"t": a.tenant_id},
        )
    owner_db.rollback()


# --- Console ---------------------------------------------------------------------------------


def test_console_shows_and_acts_on_each_site_subscription(
    console: TestClient,
    platform_admin: Any,
    provision: Any,
    api_for: Any,
    owner_db: Session,
) -> None:
    tenant = provision("alpha", plan="ENTREPRISE")
    owner = api_for("owner@alpha.example.com")
    depot = add_site(owner, "Dépôt", "DEP", plan="STANDARD", active=False).json()["id"]
    platform_admin()
    assert console_login(console).status_code == 200

    listed = console.get(f"{CONSOLE_PREFIX}/tenants").json()["items"][0]
    assert listed["subscription_count"] == 2
    assert listed["plan_codes"] == ["ENTREPRISE", "STANDARD"]
    assert listed["effective_statuses"] == ["active", "pending_activation"]
    pending = console.get(
        f"{CONSOLE_PREFIX}/tenants", params={"subscription_status": "pending_activation"}
    ).json()
    assert pending["total"] == 1

    detail = console.get(f"{CONSOLE_PREFIX}/tenants/{tenant.tenant_id}").json()
    by_site = {s["site"]["code"]: s for s in detail["subscriptions"]}
    assert by_site["DEP"]["effective_status"] == "pending_activation"
    assert by_site["DEP"]["actions"]["can_activate"]
    assert by_site["DEP"]["usage"]["max_users"] == {"used": 1, "limit": 5}

    activated = console.post(
        f"{CONSOLE_PREFIX}/tenants/{tenant.tenant_id}/subscriptions/{by_site['DEP']['id']}/activate",
        json={"reason": "Activation du dépôt"},
        headers={"X-TechNova-Console": "1"},
    )
    assert activated.status_code == 200, activated.text
    statuses = {s["site"]["code"]: s["status"] for s in activated.json()["subscriptions"]}
    assert statuses == {"DEP": "active", next(iter(set(by_site) - {"DEP"})): "active"}
    audit = owner_db.execute(
        text(
            "SELECT target_id, data FROM platform_audit_logs "
            "WHERE action = 'subscription.manually_activated'"
        )
    ).one()
    assert audit.target_id == by_site["DEP"]["id"] and audit.data["site_id"] == depot
    # Abonnement d'une autre entreprise : introuvable par ce chemin.
    other = provision("beta")
    wrong = console.post(
        f"{CONSOLE_PREFIX}/tenants/{tenant.tenant_id}/subscriptions/{other.subscription_id}/extend",
        json={"reason": "x", "period_end": "2099-01-01"},
        headers={"X-TechNova-Console": "1"},
    )
    assert wrong.status_code == 404
