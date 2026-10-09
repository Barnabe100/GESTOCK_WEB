"""Palier R2-A (ADR-0049) — socle de plateforme des commandes de restauration.

- ``site_setup`` : un module DISPONIBLE qui devient activé sur un site y est initialisé dans la
  transaction de l'activation (création du premier site, nouveau site, activation manuelle,
  changement de profil du site) ; jamais un module planifié ; jamais à la désactivation ; un
  échec annule l'activation.
- ``next_value`` : compteur brut par entreprise et par clé (numéro court des commandes).
- ``auto_provision`` : un modèle de rôle facultatif n'est jamais créé automatiquement ; il
  s'ajoute à la demande.
- ``module_settings`` : réglages par défaut des modules dans les profils, validés par le
  catalogue ; profils de restauration conformes à D11.
"""

import dataclasses
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import create_session_factory, set_db_context
from app.core.errors import BusinessRuleError
from app.platform.catalog.loader import (
    CatalogError,
    RoleTemplate,
    load_catalog,
    role_templates,
    validate_catalog,
)
from app.platform.provisioning.service import ProvisionTenantCommand, TenantProvisioningService
from app.platform.registry import get_registry
from app.platform.sequences.service import next_number, next_value
from app.platform.subscriptions.models import BillingPeriod
from app.shared.clock import utcnow
from tests.conftest import PASSWORD, add_site, set_site_module

MENU = "restaurant.menu"  # livré (R1)
ORDERS = "restaurant.orders"  # planifié jusqu'à R2-E
MAQUIS = "restaurant.maquis"

Call = tuple[str, str, str]  # (module, site, profil)


@pytest.fixture
def setups() -> Iterator[list[Call]]:
    """Enregistre les appels du ``site_setup`` du menu (disponible) et des commandes
    (planifiées) dans le registre du serveur, restauré à la fin du test."""
    calls: list[Call] = []
    registry = get_registry()
    originals = {code: registry.get(code) for code in (MENU, ORDERS)}

    def recorder(code: str) -> Any:
        def setup(session: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, profile: Any) -> None:
            calls.append((code, str(site_id), profile.code))

        return setup

    for code, manifest in originals.items():
        registry._modules[code] = dataclasses.replace(manifest, site_setup=recorder(code))
    try:
        yield calls
    finally:
        registry._modules.update(originals)


def _activated(owner_db: Session, site: Any, code: str) -> bool | None:
    owner_db.expire_all()
    value = owner_db.execute(
        text("SELECT enabled FROM site_modules WHERE site_id = :s AND module_code = :c"),
        {"s": str(site), "c": code},
    ).scalar_one_or_none()
    return None if value is None else bool(value)


# --- site_setup ---------------------------------------------------------------------------------


def test_first_site_sets_up_enabled_available_modules_only(
    provision: Any, setups: list[Call]
) -> None:
    t = provision("r2a-first", profile=MAQUIS)
    # Menu : disponible et activé par défaut → initialisé. Commandes : activées par défaut mais
    # planifiées → activation inerte, aucune initialisation.
    assert setups == [(MENU, str(t.site_id), MAQUIS)]


def test_new_site_is_set_up_with_its_own_profile(
    provision: Any, api_for: Any, setups: list[Call]
) -> None:
    t = provision("r2a-newsite")  # commerce : ni menu ni commandes
    assert setups == []
    owner = api_for("owner@r2a-newsite.example.com")
    created = add_site(owner, "Maquis", "MAQ", "restaurant", business_profile_code=MAQUIS)
    assert created.status_code == 201, created.text
    assert setups == [(MENU, created.json()["id"], MAQUIS)]
    assert str(t.site_id) not in {site for _, site, _ in setups}


def test_manual_activation_sets_up_and_deactivation_does_not(
    provision: Any, api_for: Any, owner_db: Session, setups: list[Call]
) -> None:
    t = provision("r2a-toggle", profile=MAQUIS)
    owner = api_for("owner@r2a-toggle.example.com")
    setups.clear()
    assert set_site_module(owner, t.site_id, MENU, False).status_code == 204
    assert setups == []  # désactiver ne supprime ni ne réinitialise rien
    assert set_site_module(owner, t.site_id, MENU, True).status_code == 204
    assert setups == [(MENU, str(t.site_id), MAQUIS)]
    # Module planifié : activation refusée, aucune initialisation.
    refused = set_site_module(owner, t.site_id, ORDERS, True)
    assert refused.json()["code"] == "module_not_implemented"
    assert setups == [(MENU, str(t.site_id), MAQUIS)]
    assert _activated(owner_db, t.site_id, MENU) is True


def test_profile_change_sets_up_modules_it_enables(
    provision: Any, api_for: Any, setups: list[Call]
) -> None:
    t = provision("r2a-change")
    owner = api_for("owner@r2a-change.example.com")
    preview = owner.get(
        f"/sites/{t.site_id}/business-profile/preview", params={"profile_code": MAQUIS}
    )
    assert preview.status_code == 200, preview.text
    changed = owner.put(
        f"/sites/{t.site_id}/business-profile",
        json={"profile_code": MAQUIS, "preview_fingerprint": preview.json()["fingerprint"]},
    )
    assert changed.status_code == 200, changed.text
    assert setups == [(MENU, str(t.site_id), MAQUIS)]


def test_failing_setup_cancels_the_activation(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("r2a-fail", profile=MAQUIS)
    owner = api_for("owner@r2a-fail.example.com")
    assert set_site_module(owner, t.site_id, MENU, False).status_code == 204
    registry = get_registry()
    original = registry.get(MENU)

    def setup(session: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, profile: Any) -> None:
        raise BusinessRuleError("Initialisation impossible", code="site_setup_failed")

    registry._modules[MENU] = dataclasses.replace(original, site_setup=setup)
    try:
        refused = set_site_module(owner, t.site_id, MENU, True)
    finally:
        registry._modules[MENU] = original
    assert (refused.status_code, refused.json()["code"]) == (422, "site_setup_failed")
    # Tout ou rien : l'activation n'est pas enregistrée.
    assert _activated(owner_db, t.site_id, MENU) is False


# --- next_value ---------------------------------------------------------------------------------


def _session(app_engine: Engine, tenant_id: uuid.UUID, user_id: uuid.UUID) -> Session:
    session = create_session_factory(app_engine)()
    set_db_context(session, tenant_id=tenant_id, user_id=user_id)
    return session


def test_next_value_counts_per_key_and_tenant(provision: Any, app_engine: Engine) -> None:
    a = provision("r2a-seq-a")
    b = provision("r2a-seq-b")
    day = f"r2a:{uuid.uuid4().hex}:20261009"
    with _session(app_engine, a.tenant_id, a.owner_user_id) as db:
        assert [next_value(db, a.tenant_id, day) for _ in range(3)] == [1, 2, 3]
        assert next_value(db, a.tenant_id, day + "x") == 1  # autre clé, autre compteur
        db.commit()
    with _session(app_engine, b.tenant_id, b.owner_user_id) as db:
        assert next_value(db, b.tenant_id, day) == 1  # autre entreprise
        db.rollback()
    with _session(app_engine, a.tenant_id, a.owner_user_id) as db:
        assert next_value(db, a.tenant_id, day) == 4
        db.rollback()  # valeur annulée avec la transaction
    with _session(app_engine, a.tenant_id, a.owner_user_id) as db:
        assert next_value(db, a.tenant_id, day) == 4
        # Numéros formatés : inchangés (même compteur sous-jacent).
        assert next_number(db, a.tenant_id, "r2a", "ENT") == "ENT-000001"
        db.rollback()


def test_next_value_serialises_concurrent_takers(provision: Any, app_engine: Engine) -> None:
    t = provision("r2a-seq-conc")
    key = f"r2a:{uuid.uuid4().hex}:20261009"
    values: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def take() -> None:
        with _session(app_engine, t.tenant_id, t.owner_user_id) as db:
            barrier.wait()
            value = next_value(db, t.tenant_id, key)
            db.commit()
            with lock:
                values.append(value)

    threads = [threading.Thread(target=take) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(values) == list(range(1, 9))


# --- auto_provision -----------------------------------------------------------------------------

OPTIONAL = RoleTemplate(
    code="r2a_optional",
    name="Modèle facultatif R2-A",
    description="Créé à la demande seulement.",
    permission_patterns=("catalog.article.view",),
    auto_provision=False,
)


def test_optional_template_is_never_provisioned_but_can_be_added(
    app_engine: Engine,
    owner_engine: Engine,
    owner_db: Session,
    settings: Settings,
    api_for: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    templates = {**role_templates(), OPTIONAL.code: OPTIONAL}
    monkeypatch.setattr("app.platform.access.service.role_templates", lambda: templates)
    monkeypatch.setattr("app.platform.access.permissions.role_templates", lambda: templates)
    with create_session_factory(app_engine)() as session:
        result = TenantProvisioningService(
            session, settings, get_registry(), templates, utcnow()
        ).provision(
            ProvisionTenantCommand(
                name="Entreprise r2a-roles",
                slug="r2a-roles",
                profile_code="retail.alimentation",
                plan_code="ENTREPRISE",
                billing_period=BillingPeriod.MONTHLY,
                owner_email="owner@r2a-roles.example.com",
                owner_full_name="Owner r2a-roles",
                country_code="BF",
                owner_password=PASSWORD,
            ),
            actor="test",
        )
        session.commit()
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET must_change_password = false WHERE id = :id"),
            {"id": result.owner_user_id},
        )
    codes = set(
        owner_db.execute(
            text("SELECT template_code FROM roles WHERE tenant_id = :t AND is_system"),
            {"t": result.tenant_id},
        ).scalars()
    )
    assert codes == {"administrator", "manager", "seller", "viewer"}
    owner = api_for("owner@r2a-roles.example.com")
    listed = {t["code"]: t["instantiated"] for t in owner.get("/role-templates").json()}
    assert listed[OPTIONAL.code] is False
    created = owner.post("/roles/from-template", json={"template_code": OPTIONAL.code})
    assert created.status_code == 201, created.text
    assert created.json()["permission_codes"] == ["catalog.article.view"]


def test_protected_template_must_be_provisioned() -> None:
    catalog = load_catalog(get_registry())
    admin = catalog.role_templates["administrator"]
    broken = dataclasses.replace(
        catalog,
        role_templates={
            **catalog.role_templates,
            "administrator": dataclasses.replace(admin, auto_provision=False),
        },
    )
    with pytest.raises(CatalogError, match="rôle protégé non provisionné"):
        validate_catalog(broken, get_registry())


def test_base_templates_stay_provisioned() -> None:
    assert all(t.auto_provision for t in role_templates().values())


# --- module_settings et profils D11 -------------------------------------------------------------


def _with_settings(settings: dict[str, Any]) -> Any:
    catalog = load_catalog(get_registry())
    profile = dataclasses.replace(catalog.profiles[MAQUIS], module_settings=settings)
    return dataclasses.replace(catalog, profiles={**catalog.profiles, MAQUIS: profile})


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"inconnu.module": {"x": 1}}, "réglages d'un module inconnu inconnu.module"),
        ({"automobile.workshop": {"x": 1}}, "non proposé par le profil"),
        ({ORDERS: {"payment_timing": ["AT_END"]}}, "valeur scalaire attendue"),
        ({ORDERS: "AT_END"}, "table attendue"),
    ],
)
def test_module_settings_are_validated(settings: dict[str, Any], message: str) -> None:
    with pytest.raises(CatalogError, match=message):
        validate_catalog(_with_settings(settings), get_registry())


D11 = {
    # profil : (activés par défaut, facultatifs, paiement par défaut)
    "maquis": ({MENU, ORDERS}, {"tables", "kitchen", "qr", "recipes"}, "AT_END"),
    "bar": ({MENU, ORDERS}, {"tables", "kitchen", "qr", "recipes"}, "AT_END"),
    "cafe": ({MENU, ORDERS}, {"tables", "kitchen", "qr", "recipes"}, "AT_END"),
    "boulangerie": ({MENU, ORDERS}, {"tables", "kitchen", "qr", "recipes"}, "AT_ORDER"),
    "restaurant": ({MENU, ORDERS, "tables", "kitchen"}, {"qr", "recipes"}, "AT_END"),
    "pizzeria": ({MENU, ORDERS, "tables", "kitchen"}, {"qr", "recipes"}, "AT_END"),
    "traiteur": ({MENU, ORDERS, "kitchen"}, {"tables", "qr", "recipes"}, "AT_ORDER"),
    "fast_food": ({MENU, ORDERS, "kitchen"}, {"tables", "qr", "recipes"}, "AT_ORDER"),
}


def _restaurant(codes: set[str]) -> set[str]:
    return {c if c.startswith("restaurant.") else f"restaurant.{c}" for c in codes}


@pytest.mark.parametrize("activity", sorted(D11))
def test_restaurant_profiles_follow_d11(activity: str) -> None:
    default, optional, timing = D11[activity]
    profile = load_catalog(get_registry()).profiles[f"restaurant.{activity}"]
    assert {m for m in profile.modules if m.startswith("restaurant.")} == _restaurant(default)
    assert {m for m in profile.optional_modules if m.startswith("restaurant.")} == _restaurant(
        optional
    )
    assert profile.module_settings == {ORDERS: {"payment_timing": timing}}
    # Le reste de l'offre de la restauration est inchangé.
    assert {"catalog", "stock", "sales", "pos", "cash_register"} <= set(profile.modules)


def test_other_sectors_declare_no_module_settings() -> None:
    profiles = load_catalog(get_registry()).profiles.values()
    assert all(not p.module_settings for p in profiles if not p.code.startswith("restaurant."))
