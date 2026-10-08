"""Profils / modules par site, palier C : activation des modules portée par le SITE.

PLAN (abonnement du site) ≠ PROFIL (du site) ≠ ACTIVATION SITE (``site_modules``) :
``effective`` = proposé par le profil du site ∩ inclus dans l'abonnement du site ∩ activé sur
le site, dépendances résolues. ``tenant_modules`` (legacy) n'est ni lu ni écrit.
"""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from tests.conftest import OWNER_URL, Api, add_site, set_site_module
from tests.stock_helpers import member
from tests.test_signup import _api as _signup_api
from tests.test_signup import _signup, offers  # noqa: F401


def _alembic() -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = OWNER_URL
    return config


@pytest.fixture
def at_head(owner_engine: Engine) -> Iterator[None]:
    try:
        yield
    finally:
        owner_engine.dispose()
        command.upgrade(_alembic(), "head")


def _site_modules(api: Api, site: Any) -> dict[str, dict[str, Any]]:
    response = api.get(f"/sites/{site}/modules")
    assert response.status_code == 200, response.text
    return {m["code"]: m for m in response.json()}


def _caps(api: Api, site: Any = None) -> dict[str, Any]:
    scoped = Api(api.client, api.token, uuid.UUID(str(site)) if site else None)
    response = scoped.get("/me/capabilities")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _modules(caps: dict[str, Any]) -> set[str]:
    return {m["code"] for m in caps["modules"]}


def _state(module: dict[str, Any]) -> tuple[bool, bool, bool, bool]:
    return (
        module["in_profile"],
        module["in_plan"],
        module["activated_for_site"],
        module["effective"],
    )


@pytest.fixture
def two_sites(provision: Any, api_for: Any) -> SimpleNamespace:
    """Tenant ENTREPRISE (Quincaillerie) à deux sites créés avec le même profil."""
    t = provision("modules", profile="retail.quincaillerie", plan="ENTREPRISE")
    owner = api_for("owner@modules.example.com")
    b = add_site(owner, "Dépôt", "DEP", "warehouse")
    assert b.status_code == 201, b.text
    return SimpleNamespace(owner=owner, a=str(t.site_id), b=b.json()["id"], tenant=t.tenant_id)


# --- Indépendance des sites ----------------------------------------------------------------------


def test_disabling_and_enabling_pos_on_a_never_touches_b(
    two_sites: SimpleNamespace, owner_db: Session
) -> None:
    w = two_sites
    assert set_site_module(w.owner, w.a, "pos", False).status_code == 204
    assert _site_modules(w.owner, w.a)["pos"]["activated_for_site"] is False
    assert _site_modules(w.owner, w.b)["pos"]["activated_for_site"] is True
    assert "pos" not in _modules(_caps(w.owner, w.a))
    assert "pos" in _modules(_caps(w.owner, w.b))
    # Réactivation sur A : B toujours inchangé.
    assert set_site_module(w.owner, w.a, "pos", True).status_code == 204
    assert _state(_site_modules(w.owner, w.a)["pos"]) == (True, True, True, True)
    assert _state(_site_modules(w.owner, w.b)["pos"]) == (True, True, True, True)
    # Audit : une entrée par site concerné, avec le site.
    rows = owner_db.execute(
        text(
            "SELECT action, site_id, data FROM audit_logs "
            "WHERE entity_type = 'module' AND entity_id = 'pos' ORDER BY occurred_at, id"
        )
    ).all()
    assert [(r.action, str(r.site_id)) for r in rows] == [
        ("module.disabled", w.a),
        ("module.enabled", w.a),
    ]
    assert rows[0].data["previous"] is True and rows[0].data["enabled"] is False


def test_new_site_never_inherits_another_site_configuration(
    two_sites: SimpleNamespace,
) -> None:
    w = two_sites
    assert set_site_module(w.owner, w.a, "alerts", False).status_code == 204
    c = add_site(w.owner, "Annexe", "ANX")
    assert c.status_code == 201, c.text
    annex = c.json()["id"]
    # Défauts de SON profil ∩ son abonnement : aucune copie de A.
    assert _site_modules(w.owner, annex)["alerts"]["activated_for_site"] is True
    assert _site_modules(w.owner, w.b)["alerts"]["activated_for_site"] is True
    assert _site_modules(w.owner, w.a)["alerts"]["activated_for_site"] is False


# --- Quatre états : profil, plan, activation, effectif ------------------------------------------


def test_profile_plan_and_activation_are_all_required(
    two_sites: SimpleNamespace, owner_db: Session
) -> None:
    w = two_sites
    # Site B passé au profil Entrepôt (hors API) : POS activé sur le site mais absent du
    # profil → jamais effectif.
    owner_db.execute(
        text("UPDATE sites SET business_profile_code = 'distribution.entrepot' WHERE id = :s"),
        {"s": w.b},
    )
    owner_db.commit()
    rows = owner_db.execute(
        text("SELECT enabled FROM site_modules WHERE site_id = :s AND module_code = 'pos'"),
        {"s": w.b},
    ).scalar_one()
    assert rows is True
    assert "pos" not in _site_modules(w.owner, w.b)  # hors profil du site : non proposé
    assert "pos" not in _modules(_caps(w.owner, w.b))
    # Activé + profil + plan : effectif (site A).
    assert _state(_site_modules(w.owner, w.a)["pos"]) == (True, True, True, True)


def test_offered_but_not_activated_is_not_effective(provision: Any, api_for: Any) -> None:
    t = provision("resto-opt", profile="restaurant.restaurant", plan="ENTREPRISE")
    owner = api_for("owner@resto-opt.example.com")
    # QR : optionnel du profil, inclus dans ENTREPRISE, non activé à la création du site.
    assert _state(_site_modules(owner, t.site_id)["restaurant.qr"]) == (True, True, False, False)
    assert set_site_module(owner, t.site_id, "restaurant.qr", True).status_code == 204
    qr = _site_modules(owner, t.site_id)["restaurant.qr"]
    assert qr["activated_for_site"] is True
    # Module planifié (non implémenté) : jamais exposé comme utilisable par l'interface, mais
    # effectif au sens des capacités (comportement inchangé).
    assert qr["effective"] is True


def test_module_outside_the_plan_is_never_effective(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("resto-std", profile="restaurant.restaurant", plan="STANDARD")
    owner = api_for("owner@resto-std.example.com")
    refused = set_site_module(owner, t.site_id, "restaurant.qr", True)
    assert (refused.status_code, refused.json()["code"]) == (422, "module_not_offered")
    # Même activé directement en base : hors abonnement du site, jamais effectif.
    owner_db.execute(
        text(
            "INSERT INTO site_modules (id, tenant_id, site_id, module_code, enabled) "
            "VALUES (gen_random_uuid(), :t, :s, 'restaurant.qr', true)"
        ),
        {"t": t.tenant_id, "s": t.site_id},
    )
    owner_db.commit()
    assert _state(_site_modules(owner, t.site_id)["restaurant.qr"]) == (True, False, True, False)
    assert "restaurant.qr" not in _modules(_caps(owner, t.site_id))
    # Fonctionnalité de plan : STANDARD sans transferts, quelle que soit l'activation.
    assert "stock.transfers" not in _caps(owner, t.site_id)["features"]


def test_dependencies_stay_consistent_per_site(
    two_sites: SimpleNamespace, owner_db: Session
) -> None:
    w = two_sites
    busy = set_site_module(w.owner, w.a, "sales", False)
    assert (busy.status_code, busy.json()["code"]) == (409, "module_has_dependents")
    # Dépendance retirée hors API : les modules qui en dépendent ne sont plus effectifs, sans
    # être désactivés pour autant ; aucune activation implicite.
    owner_db.execute(
        text(
            "UPDATE site_modules SET enabled = false WHERE site_id = :s AND module_code = 'sales'"
        ),
        {"s": w.a},
    )
    owner_db.commit()
    a = _site_modules(w.owner, w.a)
    assert a["pos"]["activated_for_site"] is True and a["pos"]["effective"] is False
    assert a["sales"]["effective"] is False
    assert "pos" in _modules(_caps(w.owner, w.b))
    # Réactiver un module dont la dépendance est désactivée sur ce site : refusé.
    assert set_site_module(w.owner, w.a, "pos", False).status_code == 204
    missing = set_site_module(w.owner, w.a, "pos", True)
    assert (missing.status_code, missing.json()["code"]) == (409, "module_dependency_missing")
    assert missing.json()["missing"] == ["sales"]


# --- Sécurité ------------------------------------------------------------------------------------


def test_sites_of_another_tenant_are_out_of_reach(
    two_sites: SimpleNamespace, provision: Any, api_for: Any
) -> None:
    other = provision("autre", profile="retail.alimentation")
    stranger = api_for("owner@autre.example.com")
    assert stranger.get(f"/sites/{two_sites.a}/modules").status_code == 404
    refused = set_site_module(stranger, two_sites.a, "pos", False)
    assert refused.status_code == 404
    assert set_site_module(two_sites.owner, other.site_id, "pos", False).status_code == 404
    assert _site_modules(two_sites.owner, two_sites.a)["pos"]["activated_for_site"] is True


def test_only_authorised_members_change_modules_of_their_sites(
    two_sites: SimpleNamespace, client: TestClient
) -> None:
    w = two_sites
    owner_ns = SimpleNamespace(owner=w.owner)
    seller = member(owner_ns, client, "vendeur@modules.example.com", "seller")  # type: ignore[arg-type]
    denied = set_site_module(seller, w.a, "pos", False)
    assert (denied.status_code, denied.json()["code"]) == (403, "permission_denied")
    # Administrateur limité au site A : le site B lui est inaccessible.
    admin_a = member(
        owner_ns,  # type: ignore[arg-type]
        client,
        "admin-a@modules.example.com",
        "administrator",
        all_sites=False,
        site_ids=[w.a],
    )
    assert set_site_module(admin_a, w.a, "alerts", False).status_code == 204
    refused = set_site_module(admin_a, w.b, "alerts", False)
    assert (refused.status_code, refused.json()["code"]) == (403, "site_access_denied")
    assert _site_modules(w.owner, w.b)["alerts"]["activated_for_site"] is True
    # Route de l'entreprise retirée : jamais une activation globale.
    retired = w.owner.put("/modules/pos", json={"enabled": False})
    assert (retired.status_code, retired.json()["code"]) == (409, "module_is_per_site")


# --- Vue « Tous les sites » et synthèse ----------------------------------------------------------


def test_all_sites_view_unions_accessible_sites(
    two_sites: SimpleNamespace, client: TestClient
) -> None:
    w = two_sites
    assert set_site_module(w.owner, w.a, "pos", False).status_code == 204
    consolidated = _caps(w.owner)
    assert consolidated["profile_scope"] == "reference"
    assert "pos" in _modules(consolidated)  # actif sur B
    summary = {m["code"]: m for m in w.owner.get("/modules").json()}
    assert summary["pos"]["enabled"] is True  # activé sur au moins un site accessible
    # Membre limité au site A : la vue consolidée ne reprend que A.
    only_a = member(
        SimpleNamespace(owner=w.owner),  # type: ignore[arg-type]
        client,
        "gestion-a@modules.example.com",
        "manager",
        all_sites=False,
        site_ids=[w.a],
    )
    assert "pos" not in _modules(_caps(only_a))


def test_signup_without_site_computes_defaults_then_first_site_owns_them(
    client: TestClient,
    offers: None,  # noqa: F811
    owner_db: Session,
) -> None:
    owner = _signup_api(client, _signup(client))
    caps = _caps(owner)
    assert {"pos", "cash_register", "stock"} <= _modules(caps)
    assert owner_db.execute(text("SELECT count(*) FROM site_modules")).scalar_one() == 0
    assert owner_db.execute(text("SELECT count(*) FROM tenant_modules")).scalar_one() == 0
    site = owner.post("/sites", json={"name": "Boutique", "code": "BTQ"})
    assert site.status_code == 201, site.text
    modules = _site_modules(owner, site.json()["id"])
    assert modules["pos"]["activated_for_site"] is True
    assert owner_db.execute(text("SELECT count(*) FROM tenant_modules")).scalar_one() == 0


# --- Migration 0040 ------------------------------------------------------------------------------


def _copy_site_modules_to_tenant(owner_db: Session, tenant: Any, site: str) -> None:
    owner_db.execute(
        text(
            "INSERT INTO tenant_modules (tenant_id, module_code, enabled) "
            "SELECT tenant_id, module_code, enabled FROM site_modules WHERE site_id = :s"
        ),
        {"s": site},
    )


def test_migration_0040_copies_tenant_activations_to_every_site(
    two_sites: SimpleNamespace, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    w = two_sites
    # État « avant 0040 » : activations du tenant identiques sur ses deux sites.
    _copy_site_modules_to_tenant(owner_db, w.tenant, w.a)
    owner_db.commit()
    owner_db.close()
    owner_engine.dispose()
    command.downgrade(_alembic(), "0039")
    with owner_engine.begin() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'site_modules'"
                )
            ).scalar_one()
            == 0
        )
        # Configuration du tenant modifiée avant la migration : la reprise copie l'état ACTUEL.
        conn.execute(text("UPDATE tenant_modules SET enabled = false WHERE module_code = 'alerts'"))
        legacy = dict(
            conn.execute(
                text("SELECT module_code, enabled FROM tenant_modules WHERE tenant_id = :t"),
                {"t": w.tenant},
            ).all()
        )
    owner_engine.dispose()
    command.upgrade(_alembic(), "head")
    with owner_engine.begin() as conn:
        for site in (w.a, w.b):
            copied = dict(
                conn.execute(
                    text("SELECT module_code, enabled FROM site_modules WHERE site_id = :s"),
                    {"s": site},
                ).all()
            )
            assert copied == legacy  # copie exacte : ni module inventé, ni module perdu
        assert legacy["alerts"] is False
        rls = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'site_modules'"
            )
        ).one()
        assert tuple(rls) == (True, True)
        assert (
            conn.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'tenant_modules'")
            ).scalar_one()
            is True
        )
    # Configuration cohérente : capacités de chaque site conformes à la reprise.
    for site in (w.a, w.b):
        modules = _modules(_caps(w.owner, site))
        assert "alerts" not in modules and {"pos", "stock"} <= modules


def test_migration_0040_downgrade_refuses_to_lose_site_specific_settings(
    two_sites: SimpleNamespace, owner_db: Session, owner_engine: Engine, at_head: None
) -> None:
    w = two_sites
    _copy_site_modules_to_tenant(owner_db, w.tenant, w.a)
    owner_db.commit()
    # Configuration propre au site A.
    assert set_site_module(w.owner, w.a, "pos", False).status_code == 204
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="activation de modules propre"):
        command.downgrade(_alembic(), "0039")
    with owner_engine.begin() as conn:
        kept = conn.execute(
            text("SELECT enabled FROM site_modules WHERE site_id = :s AND module_code = 'pos'"),
            {"s": w.a},
        ).scalar_one()
        assert kept is False


def test_site_modules_are_isolated_by_rls(
    two_sites: SimpleNamespace, provision: Any, db: Session
) -> None:
    from app.core.db import set_db_context

    other = provision("rls-mod", profile="retail.alimentation")
    set_db_context(db, tenant_id=other.tenant_id)
    sites = {row[0] for row in db.execute(text("SELECT DISTINCT site_id FROM site_modules"))}
    assert sites == {other.site_id}
    updated = db.execute(
        text("UPDATE site_modules SET enabled = false WHERE site_id = :s"),
        {"s": two_sites.a},
    )
    assert updated.rowcount == 0  # type: ignore[attr-defined]
    db.rollback()
    set_db_context(db, tenant_id=other.tenant_id)
    with pytest.raises(Exception, match="row-level security|violates foreign key"):
        db.execute(
            text(
                "INSERT INTO site_modules (id, tenant_id, site_id, module_code, enabled) "
                "VALUES (gen_random_uuid(), :t, :s, 'pos', true)"
            ),
            {"t": two_sites.tenant, "s": two_sites.a},
        )
    db.rollback()
