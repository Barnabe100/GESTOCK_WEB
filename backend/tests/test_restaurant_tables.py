"""Palier R5-A (ADR-0049 ; conception R5 consignée dans ``docs/DOC_BACKLOG.md``) — zones et
tables des sites de restauration : configuration seulement.

``restaurant.tables`` reste PLANIFIÉ dans le registre de production (A2) : ces tests utilisent un
registre de test où il est disponible avec sa dépendance ``restaurant.orders`` (fixture
``tclient``) ; un test vérifie que la production ne l'expose pas.

Couvert : unicités par site insensibles à la casse (nom de zone, libellé de table), zone
facultative du MÊME site et active, désactivations protégées (zone avec tables actives, table
avec commande en cours, module avec commandes sur des tables), audit, permissions et modèles de
rôles, portée des sites, isolation des entreprises (API, RLS, FK composites, droits SQL), et
concurrence de la configuration jouée PAS À PAS (verrou tenu par une transaction, attente
vérifiée dans ``pg_stat_activity``, puis validation).
"""

import threading
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.platform.registry import ModuleStatus, get_registry
from tests.conftest import PASSWORD, Api, add_site, set_site_module
from tests.test_restaurant_orders import _api, _code, _line, _menu, _ok, _order

RESTAURANT = "restaurant.restaurant"
MAQUIS = "restaurant.maquis"
TABLES = "/restaurant/tables"
ZONES = "/restaurant/tables/zones"
MODULE = "restaurant.tables"


@pytest.fixture
def resto(provision: Any, tclient: TestClient) -> SimpleNamespace:
    """Restaurant ENTREPRISE, deux sites (tables activées par défaut sur chacun : profil
    Restaurant, D11 ; sites créés après la migration 0046)."""
    t = provision("rt-main", profile=RESTAURANT, plan="ENTREPRISE")
    owner = _api(tclient, "owner@rt-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER", "restaurant"), 201)["id"]
    return SimpleNamespace(tenant=t.tenant_id, owner=owner, client=tclient, a=a, b=b)


def _zone(r: SimpleNamespace, name: str, site: str | None = None, api: Api | None = None) -> Any:
    return (api or r.owner).post(ZONES, json={"site_id": site or r.a, "name": name})


def _table(
    r: SimpleNamespace,
    label: str,
    site: str | None = None,
    api: Api | None = None,
    **extra: Any,
) -> Any:
    return (api or r.owner).post(TABLES, json={"site_id": site or r.a, "label": label, **extra})


def _tables(api: Api, **params: Any) -> list[dict[str, Any]]:
    return list(_ok(api.get(TABLES, params={"limit": 100, **params}))["items"])


def _audit(r: SimpleNamespace, action: str) -> list[dict[str, Any]]:
    items = _ok(r.owner.get("/audit-logs", params={"action": action, "limit": 50}))["items"]
    return list(reversed(items))


def _selected(api: Api, site: str) -> Api:
    return Api(api.client, api.token, uuid.UUID(site))


def _member(r: SimpleNamespace, email: str, template: str, **access: Any) -> Api:
    """Membre avec le rôle du modèle ``template`` (créé à la demande pour les modèles
    facultatifs de la restauration)."""
    roles = {x["template_code"]: x["id"] for x in r.owner.get("/roles").json()}
    if template not in roles:
        roles[template] = _ok(
            r.owner.post("/roles/from-template", json={"template_code": template}), 201
        )["id"]
    body = {
        "email": email,
        "full_name": email,
        "password": "Provisoire-123",
        "roles": [{"role_id": roles[template]}],
        **({"all_sites": True} | access),
    }
    _ok(r.owner.post("/members", json=body), 201)
    temporary = _api(r.client, email, "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return _api(r.client, email)


# --- Production : module planifié -----------------------------------------------------------


def test_production_registry_keeps_tables_planned_and_dependent_on_orders(
    provision: Any, api_for: Any
) -> None:
    manifest = get_registry().get(MODULE)
    assert manifest.status is ModuleStatus.PLANNED
    assert manifest.depends_on == ("restaurant.orders",)
    # Les commandes ne dépendent jamais des tables (D1, A2).
    assert MODULE not in get_registry().get("restaurant.orders").depends_on
    t = provision("rt-prod", profile=RESTAURANT, plan="ENTREPRISE")
    owner = api_for("owner@rt-prod.example.com")
    assert owner.get(TABLES).status_code == 404
    assert owner.get(ZONES).status_code == 404
    refused = set_site_module(owner, t.site_id, MODULE, True)
    assert _code(refused) == (422, "module_not_implemented")
    # Activation inerte d'un nouveau site Restaurant (défaut D11, politique E.1 conservée) :
    # aucune permission accordée.
    caps = Api(owner.client, owner.token, t.site_id).get("/me/capabilities").json()
    assert not [p for p in caps["permissions"] if p.startswith("restaurant.tables.")]


# --- Zones ----------------------------------------------------------------------------------


def test_zones_are_per_site_unique_case_insensitive_and_audited(resto: SimpleNamespace) -> None:
    r = resto
    salle = _ok(_zone(r, "  Salle  "), 201)
    assert (salle["name"], salle["site_id"], salle["is_active"]) == ("Salle", r.a, True)
    assert (salle["table_count"], salle["active_table_count"]) == (0, 0)
    assert _code(_zone(r, "SALLE")) == (409, "zone_name_taken")
    # Même nom sur un autre site : permis.
    _ok(_zone(r, "Salle", r.b), 201)
    terrasse = _ok(_zone(r, "Terrasse"), 201)
    renamed = _ok(
        r.owner.put(f"{ZONES}/{terrasse['id']}", json={"name": "Jardin", "sort_order": 1})
    )
    assert (renamed["name"], renamed["sort_order"]) == ("Jardin", 1)
    assert _code(r.owner.put(f"{ZONES}/{terrasse['id']}", json={"name": "salle"})) == (
        409,
        "zone_name_taken",
    )
    # Casse seule : la zone elle-même peut changer la casse de son nom.
    _ok(r.owner.put(f"{ZONES}/{terrasse['id']}", json={"name": "JARDIN", "sort_order": 1}))
    assert _code(_zone(r, "   ")) == (422, "validation_error")
    created = _audit(r, "restaurant_zone.created")
    assert {e["data"]["name"] for e in created} == {"Salle", "Terrasse"}
    change = _audit(r, "restaurant_zone.updated")[0]["data"]
    assert change["before"]["name"] == "Terrasse" and change["after"]["name"] == "Jardin"


def test_zone_deactivation_is_refused_while_it_holds_active_tables(
    resto: SimpleNamespace,
) -> None:
    r = resto
    zone = _ok(_zone(r, "Salle"), 201)["id"]
    table = _ok(_table(r, "T1", zone_id=zone), 201)["id"]
    refused = r.owner.post(f"{ZONES}/{zone}/deactivate")
    assert _code(refused) == (409, "zone_has_active_tables")
    assert refused.json()["active_tables"] == 1
    _ok(r.owner.post(f"{TABLES}/{table}/deactivate"))
    inactive = _ok(r.owner.post(f"{ZONES}/{zone}/deactivate"))
    assert (inactive["is_active"], inactive["table_count"], inactive["active_table_count"]) == (
        False,
        1,
        0,
    )
    # Zone inactive : plus aucune table rattachable, ni réactivable dedans.
    assert _code(_table(r, "T2", zone_id=zone)) == (422, "zone_inactive")
    assert _code(r.owner.post(f"{TABLES}/{table}/activate")) == (422, "zone_inactive")
    # Réactivée : même ligne, la table aussi.
    assert _ok(r.owner.post(f"{ZONES}/{zone}/activate"))["id"] == zone
    assert _ok(r.owner.post(f"{TABLES}/{table}/activate"))["is_active"] is True
    assert len(_audit(r, "restaurant_zone.deactivated")) == 1
    assert len(_audit(r, "restaurant_zone.activated")) == 1


# --- Tables ---------------------------------------------------------------------------------


def test_tables_are_unique_per_site_across_zones_with_optional_zone(
    resto: SimpleNamespace,
) -> None:
    r = resto
    salle = _ok(_zone(r, "Salle"), 201)["id"]
    terrasse = _ok(_zone(r, "Terrasse"), 201)["id"]
    t1 = _ok(_table(r, " Table 1 ", zone_id=salle, seats=4), 201)
    assert (t1["label"], t1["zone_id"], t1["zone_name"], t1["seats"]) == (
        "Table 1",
        salle,
        "Salle",
        4,
    )
    # Zone facultative (A4).
    bar = _ok(_table(r, "Comptoir"), 201)
    assert (bar["zone_id"], bar["zone_name"], bar["zone_active"], bar["seats"]) == (
        None,
        None,
        None,
        None,
    )
    # Libellé unique par site, toutes zones confondues, sans distinction de casse.
    assert _code(_table(r, "TABLE 1", zone_id=terrasse)) == (409, "table_label_taken")
    assert _code(_table(r, "table 1")) == (409, "table_label_taken")
    _ok(_table(r, "Table 1", r.b), 201)
    # Places : 1 à 999.
    assert _code(_table(r, "T0", seats=0)) == (422, "validation_error")
    assert _code(_table(r, "T1000", seats=1000)) == (422, "validation_error")
    assert _code(_table(r, "x" * 41)) == (422, "validation_error")
    # Déplacement de zone, retrait de la zone ; audit avant / après.
    moved = _ok(
        r.owner.put(
            f"{TABLES}/{t1['id']}",
            json={"label": "Table 1", "zone_id": terrasse, "seats": 6, "sort_order": 2},
        )
    )
    assert (moved["zone_name"], moved["seats"], moved["sort_order"]) == ("Terrasse", 6, 2)
    unzoned = _ok(r.owner.put(f"{TABLES}/{t1['id']}", json={"label": "Table 1"}))
    assert (unzoned["zone_id"], unzoned["seats"]) == (None, None)
    assert _code(r.owner.put(f"{TABLES}/{t1['id']}", json={"label": "comptoir"})) == (
        409,
        "table_label_taken",
    )
    change = _audit(r, "restaurant_table.updated")[0]["data"]
    assert change["before"]["zone_id"] == salle and change["after"]["zone_id"] == terrasse
    assert {e["data"]["label"] for e in _audit(r, "restaurant_table.created")} == {
        "Table 1",
        "Comptoir",
    }
    # Filtres et ordre du plan de salle : zones dans leur ordre, sans zone en dernier.
    _ok(_table(r, "T2", zone_id=salle), 201)
    assert [t["label"] for t in _tables(r.owner, site_id=r.a)] == ["T2", "Comptoir", "Table 1"]
    assert [t["label"] for t in _tables(r.owner, zone_id=salle)] == ["T2"]
    assert {t["label"] for t in _tables(r.owner, site_id=r.a, without_zone=True)} == {
        "Comptoir",
        "Table 1",
    }
    assert [t["label"] for t in _tables(r.owner, search="compt")] == ["Comptoir"]


def test_zone_of_another_site_is_never_usable(resto: SimpleNamespace) -> None:
    r = resto
    zone_b = _ok(_zone(r, "Salle", r.b), 201)["id"]
    assert _code(_table(r, "T1", zone_id=zone_b)) == (404, "zone_not_found")
    table = _ok(_table(r, "T1"), 201)["id"]
    moved = r.owner.put(f"{TABLES}/{table}", json={"label": "T1", "zone_id": zone_b})
    assert _code(moved) == (404, "zone_not_found")
    assert _code(_table(r, "T2", zone_id=str(uuid.uuid4()))) == (404, "zone_not_found")


def test_table_deactivation_is_refused_while_an_order_is_open_on_it(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    """A6 / R-6 : une commande non finale rattachée à la table bloque sa désactivation (le
    rattachement par l'API arrive en R5-B : simulé ici en base) ; close ou annulée, non."""
    r = resto
    table = _ok(_table(r, "T1"), 201)["id"]
    menu = _menu(r.owner, r.a, "T")
    order = _ok(_order(r.owner, r.a, [_line(menu.unit)]), 201)["id"]
    owner_db.execute(
        text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"), {"t": table, "o": order}
    )
    owner_db.commit()
    refused = r.owner.post(f"{TABLES}/{table}/deactivate")
    assert _code(refused) == (409, "table_has_open_orders")
    assert refused.json()["open_orders"] == 1
    _ok(r.owner.post(f"/restaurant/orders/{order}/cancel", json={"reason": "Client parti"}))
    assert _ok(r.owner.post(f"{TABLES}/{table}/deactivate"))["is_active"] is False
    # La commande annulée garde sa table (clé étrangère RESTRICT, aucune suppression).
    kept = owner_db.execute(
        text("SELECT table_id FROM restaurant_orders WHERE id = :o"), {"o": order}
    ).scalar_one()
    assert str(kept) == table
    assert len(_audit(r, "restaurant_table.deactivated")) == 1


# --- Module : désactivation, empreinte, portée -----------------------------------------------


def test_module_deactivation_is_refused_with_orders_on_tables_and_keeps_configuration(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    zone = _ok(_zone(r, "Salle", r.b), 201)["id"]
    table = _ok(_table(r, "T1", r.b, zone_id=zone), 201)["id"]
    menu = _menu(r.owner, r.b, "M")
    order = _ok(_order(r.owner, r.b, [_line(menu.unit)]), 201)["id"]
    owner_db.execute(
        text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"), {"t": table, "o": order}
    )
    owner_db.commit()
    refused = set_site_module(r.owner, r.b, MODULE, False)
    assert _code(refused) == (409, "module_has_open_operations")
    assert refused.json()["operations"] == {"restaurant_orders_on_tables": 1}
    _ok(r.owner.post(f"/restaurant/orders/{order}/cancel", json={"reason": "Erreur"}))
    assert set_site_module(r.owner, r.b, MODULE, False).status_code == 204
    # Configuration conservée, mais plus lisible ni modifiable sur B.
    on_b = _selected(r.owner, r.b)
    assert _code(on_b.get(TABLES)) == (403, "module_unavailable")
    assert _code(_zone(r, "Bar", r.b, api=on_b)) == (403, "module_unavailable")
    assert _tables(r.owner, site_id=r.b) == []
    assert _code(r.owner.get(f"{TABLES}/{table}")) == (404, "table_not_found")
    assert _code(r.owner.get(f"{ZONES}/{zone}")) == (404, "zone_not_found")
    assert _code(_zone(r, "Bar", r.b)) == (403, "permission_denied")
    kept = owner_db.execute(
        text("SELECT count(*) FROM restaurant_tables WHERE site_id = :s"), {"s": r.b}
    ).scalar_one()
    assert kept == 1
    # Réactivé : retrouvé intact.
    assert set_site_module(r.owner, r.b, MODULE, True).status_code == 204
    assert _ok(r.owner.get(f"{TABLES}/{table}"))["zone_name"] == "Salle"
    # Les commandes ne peuvent pas être désactivées tant que les tables (disponibles) en
    # dépendent ; les tables désactivées, si.
    blocked = set_site_module(r.owner, r.b, "restaurant.orders", False)
    assert _code(blocked) == (409, "module_has_dependents")


def test_footprint_counts_configuration_for_a_profile_change(
    resto: SimpleNamespace,
) -> None:
    r = resto
    _ok(_zone(r, "Salle"), 201)
    _ok(_table(r, "T1"), 201)
    _ok(_table(r, "T2"), 201)
    preview = _ok(
        r.owner.get(
            f"/sites/{r.a}/business-profile/preview", params={"profile_code": "retail.alimentation"}
        )
    )
    configuration = {
        (item["module"], item["kind"]): item["count"] for item in preview["configuration"]
    }
    assert configuration[(MODULE, "restaurant_zones")] == 1
    assert configuration[(MODULE, "restaurant_tables")] == 2


def test_selected_site_and_member_scope(resto: SimpleNamespace) -> None:
    r = resto
    table_b = _ok(_table(r, "B1", r.b), 201)["id"]
    _ok(_table(r, "A1"), 201)
    on_a = _selected(r.owner, r.a)
    assert {t["site_id"] for t in _tables(on_a)} == {r.a}
    assert _code(on_a.get(f"{TABLES}/{table_b}")) == (403, "site_mismatch")
    assert _code(_table(r, "B2", r.b, api=on_a)) == (403, "site_mismatch")
    assert _code(on_a.post(TABLES, json={"label": "Sans site"})) == (201, None)
    assert _code(r.owner.post(TABLES, json={"label": "Sans site"})) == (422, "site_required")
    local = _member(r, "gestion-a@rt-main.example.com", "manager", all_sites=False, site_ids=[r.a])
    assert {t["site_id"] for t in _tables(local)} == {r.a}
    assert _tables(local, site_id=r.b) == []
    assert _code(local.get(f"{TABLES}/{table_b}")) == (404, "table_not_found")
    assert _code(local.post(f"{TABLES}/{table_b}/deactivate")) == (404, "table_not_found")
    assert _code(_table(r, "B3", r.b, api=local)) == (403, "site_access_denied")


def test_pending_site_can_be_configured(resto: SimpleNamespace) -> None:
    """Abonnement du site en attente d'activation : la configuration (nature ``admin``) est
    permise, comme le menu."""
    r = resto
    pending = _ok(add_site(r.owner, "Futur", "FUT", "restaurant", active=False), 201)["id"]
    zone = _ok(_zone(r, "Salle", pending), 201)["id"]
    _ok(_table(r, "T1", pending, zone_id=zone), 201)


# --- Permissions et modèles de rôles --------------------------------------------------------


def test_role_templates_and_permissions(resto: SimpleNamespace) -> None:
    r = resto
    zone = _ok(_zone(r, "Salle"), 201)["id"]
    table = _ok(_table(r, "T1", zone_id=zone), 201)["id"]
    manager = _member(r, "gestion@rt-main.example.com", "manager")
    _ok(_table(r, "T2", api=manager), 201)
    _ok(manager.put(f"{ZONES}/{zone}", json={"name": "Salle", "sort_order": 3}))
    readers = {
        "seller": _member(r, "vendeur@rt-main.example.com", "seller"),
        "waiter": _member(r, "serveur@rt-main.example.com", "waiter"),
        "preparer": _member(r, "prepa@rt-main.example.com", "preparer"),
        "viewer": _member(r, "consult@rt-main.example.com", "viewer"),
    }
    for template, api in readers.items():
        assert {t["label"] for t in _tables(api, site_id=r.a)} == {"T1", "T2"}, template
        assert _ok(api.get(f"{ZONES}/{zone}"))["name"] == "Salle", template
        assert _code(_table(r, f"X-{template}", api=api)) == (403, "permission_denied"), template
        assert _code(_zone(r, f"Z-{template}", api=api)) == (403, "permission_denied"), template
        assert _code(api.post(f"{TABLES}/{table}/deactivate")) == (403, "permission_denied")
        assert _code(api.put(f"{ZONES}/{zone}", json={"name": "X"})) == (403, "permission_denied")
    caps = _selected(readers["waiter"], r.a).get("/me/capabilities").json()["permissions"]
    assert "restaurant.tables.table.view" in caps
    assert "restaurant.tables.table.manage" not in caps
    manager_caps = _selected(manager, r.a).get("/me/capabilities").json()["permissions"]
    assert "restaurant.tables.table.manage" in manager_caps


def test_custom_role_limited_to_one_site(resto: SimpleNamespace) -> None:
    r = resto
    _ok(_table(r, "A1"), 201)
    _ok(_table(r, "B1", r.b), 201)
    role = _ok(
        r.owner.post(
            "/roles",
            json={
                "name": "Plan de salle A",
                "permissions": ["restaurant.tables.table.view", "restaurant.tables.table.manage"],
            },
        ),
        201,
    )["id"]
    body = {
        "email": "salle@rt-main.example.com",
        "full_name": "Salle",
        "password": "Provisoire-123",
        "roles": [{"role_id": role, "site_id": r.a}],
        "all_sites": True,
    }
    _ok(r.owner.post("/members", json=body), 201)
    temporary = _api(r.client, "salle@rt-main.example.com", "Provisoire-123")
    temporary.post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    api = _api(r.client, "salle@rt-main.example.com")
    on_a = _selected(api, r.a)
    assert {t["label"] for t in _tables(on_a)} == {"A1"}
    _ok(_table(r, "A2", api=on_a), 201)
    # Sur B : aucune permission (rôle limité au site A).
    on_b = _selected(api, r.b)
    assert _code(on_b.get(TABLES)) == (403, "permission_denied")
    assert _code(_table(r, "B2", r.b, api=on_b)) == (403, "permission_denied")


# --- Isolation des entreprises, RLS, FK, droits SQL ------------------------------------------


def test_companies_are_isolated(resto: SimpleNamespace, provision: Any) -> None:
    r = resto
    zone = _ok(_zone(r, "Salle"), 201)["id"]
    table = _ok(_table(r, "T1", zone_id=zone), 201)["id"]
    provision("rt-other", profile=RESTAURANT, plan="ENTREPRISE")
    other = _api(r.client, "owner@rt-other.example.com")
    assert _ok(other.get(TABLES))["items"] == []
    assert _ok(other.get(ZONES))["items"] == []
    assert _code(other.get(f"{TABLES}/{table}")) == (404, "table_not_found")
    assert _code(other.get(f"{ZONES}/{zone}")) == (404, "zone_not_found")
    assert _code(other.post(f"{TABLES}/{table}/deactivate")) == (404, "table_not_found")
    assert _code(other.put(f"{ZONES}/{zone}", json={"name": "Pirate"})) == (404, "zone_not_found")
    assert _code(_table(r, "Pirate", r.a, api=other)) == (403, "site_access_denied")
    # Zone d'une autre entreprise : introuvable (jamais révélée).
    other_site = str(other.get("/sites").json()[0]["id"])
    assert _code(
        other.post(TABLES, json={"site_id": other_site, "label": "T", "zone_id": zone})
    ) == (
        404,
        "zone_not_found",
    )


def test_rls_composite_fks_checks_and_sql_privileges(
    resto: SimpleNamespace, provision: Any, db: Session, owner_db: Session
) -> None:
    r = resto
    zone_a = _ok(_zone(r, "Salle"), 201)["id"]
    zone_b = _ok(_zone(r, "Salle", r.b), 201)["id"]
    table_a = _ok(_table(r, "T1", zone_id=zone_a), 201)["id"]
    table_b = _ok(_table(r, "T1", r.b), 201)["id"]
    menu = _menu(r.owner, r.a, "R")
    order = _ok(_order(r.owner, r.a, [_line(menu.unit)]), 201)["id"]
    counter = _ok(_order(r.owner, r.a, [_line(menu.unit)], service_mode="COUNTER"), 201)["id"]
    other = provision("rt-rls", profile=RESTAURANT, plan="ENTREPRISE")
    # Aucun contexte : aucune ligne ; contexte d'une autre entreprise : aucune ligne de A.
    for table in ("restaurant_zones", "restaurant_tables"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    set_db_context(db, tenant_id=other.tenant_id)
    for table in ("restaurant_zones", "restaurant_tables"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    with pytest.raises(DBAPIError):
        db.execute(
            text(
                "INSERT INTO restaurant_zones (id, tenant_id, site_id, name, sort_order, "
                "is_active) VALUES (gen_random_uuid(), :t, :s, 'Pirate', 0, true)"
            ),
            {"t": r.tenant, "s": r.a},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    for table in ("restaurant_zones", "restaurant_tables"):
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(f"DELETE FROM {table}"))
        db.rollback()
        set_db_context(db, tenant_id=r.tenant)
    # FK composite : la zone d'un autre site est inutilisable EN BASE.
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO restaurant_tables (id, tenant_id, site_id, zone_id, label, "
                "sort_order, is_active) VALUES (gen_random_uuid(), :t, :s, :z, 'X', 0, true)"
            ),
            {"t": r.tenant, "s": r.a, "z": zone_b},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    # Unicité du libellé en base, sans distinction de casse.
    with pytest.raises(IntegrityError, match="uq_restaurant_tables_site_label"):
        db.execute(
            text(
                "INSERT INTO restaurant_tables (id, tenant_id, site_id, label, sort_order, "
                "is_active) VALUES (gen_random_uuid(), :t, :s, 't1', 0, true)"
            ),
            {"t": r.tenant, "s": r.a},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    with pytest.raises(IntegrityError, match="uq_restaurant_zones_site_name"):
        db.execute(
            text(
                "INSERT INTO restaurant_zones (id, tenant_id, site_id, name, sort_order, "
                "is_active) VALUES (gen_random_uuid(), :t, :s, 'SALLE', 0, true)"
            ),
            {"t": r.tenant, "s": r.a},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    # Commandes : une table d'un autre site est impossible en base (FK composite) ; une table
    # seulement « sur place » (B5).
    with pytest.raises(IntegrityError):
        db.execute(
            text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"),
            {"t": table_b, "o": order},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    with pytest.raises(IntegrityError, match="ck_restaurant_orders_table_only_on_site"):
        db.execute(
            text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"),
            {"t": table_a, "o": counter},
        )
    db.rollback()
    set_db_context(db, tenant_id=r.tenant)
    # Rôle applicatif : ``UPDATE (table_id)`` permis sur une commande ouverte « sur place ».
    db.execute(
        text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"),
        {"t": table_a, "o": order},
    )
    db.rollback()
    for table in ("restaurant_zones", "restaurant_tables"):
        flags = owner_db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
        assert tuple(flags) == (True, True)
    privileges = owner_db.execute(
        text(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name IN ('restaurant_zones', 'restaurant_tables') "
            "AND grantee = 'stockmanager_app' ORDER BY 1, 2"
        )
    ).all()
    assert [tuple(p) for p in privileges] == [
        ("restaurant_tables", "INSERT"),
        ("restaurant_tables", "SELECT"),
        ("restaurant_zones", "INSERT"),
        ("restaurant_zones", "SELECT"),
    ]
    updatable = owner_db.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.column_privileges "
            "WHERE table_name IN ('restaurant_zones', 'restaurant_tables') "
            "AND grantee = 'stockmanager_app' AND privilege_type = 'UPDATE' ORDER BY 1, 2"
        )
    ).all()
    assert [tuple(p) for p in updatable] == [
        ("restaurant_tables", "is_active"),
        ("restaurant_tables", "label"),
        ("restaurant_tables", "seats"),
        ("restaurant_tables", "sort_order"),
        ("restaurant_tables", "updated_at"),
        ("restaurant_tables", "zone_id"),
        ("restaurant_zones", "is_active"),
        ("restaurant_zones", "name"),
        ("restaurant_zones", "sort_order"),
        ("restaurant_zones", "updated_at"),
    ]
    order_columns = owner_db.execute(
        text(
            "SELECT count(*) FROM information_schema.column_privileges "
            "WHERE table_name = 'restaurant_orders' AND grantee = 'stockmanager_app' "
            "AND privilege_type = 'UPDATE' AND column_name = 'table_id'"
        )
    ).scalar_one()
    assert order_columns == 1
    others = owner_db.execute(
        text(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE table_name IN ('restaurant_zones', 'restaurant_tables') "
            "AND grantee IN ('stockmanager_platform', 'stockmanager_purge')"
        )
    ).scalar_one()
    assert others == 0


# --- Concurrence de la configuration (pas à pas) ---------------------------------------------


def test_concurrent_tables_with_the_same_label_one_created_one_409(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    results: list[tuple[int, str | None]] = []
    barrier = threading.Barrier(2)

    def create(label: str) -> None:
        barrier.wait()
        response = _table(r, label)
        results.append((response.status_code, response.json().get("code")))

    threads = [threading.Thread(target=create, args=(n,)) for n in ("Table 9", "TABLE 9")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results, key=lambda x: x[0]) == [(201, None), (409, "table_label_taken")]
    count = owner_db.execute(
        text(
            "SELECT count(*) FROM restaurant_tables WHERE site_id = :s AND lower(label) = 'table 9'"
        ),
        {"s": r.a},
    ).scalar_one()
    assert count == 1


def _waiting(owner_db: Session, timeout: float = 10.0) -> bool:
    """Vrai dès qu'une session de la base attend un verrou de ligne."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiting = owner_db.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        ).scalar_one()
        owner_db.rollback()
        if waiting:
            return True
        time.sleep(0.05)
    return False


def _step_by_step(
    app_engine: Engine,
    owner_db: Session,
    tenant: uuid.UUID,
    hold: Callable[[Session], None],
    call: Callable[[], Any],
) -> Any:
    """Transaction A (rôle applicatif réel) : prend ses verrous (``hold``) et reste ouverte ;
    l'appel B (API) démarre dans un fil, doit ATTENDRE (vérifié dans ``pg_stat_activity``) ;
    A valide ; B se termine. Renvoie la réponse de B."""
    session = create_session_factory(app_engine)()
    result: list[Any] = []
    try:
        set_db_context(session, tenant_id=tenant)
        hold(session)
        thread = threading.Thread(target=lambda: result.append(call()))
        thread.start()
        assert _waiting(owner_db), "l'appel concurrent n'a pas attendu le verrou"
        assert thread.is_alive()
        session.commit()
        thread.join(timeout=30)
        assert not thread.is_alive(), "l'appel concurrent ne s'est pas terminé"
    finally:
        session.close()
    return result[0]


def test_table_creation_waits_for_a_zone_deactivation_then_is_refused(
    resto: SimpleNamespace, app_engine: Engine, owner_db: Session
) -> None:
    r = resto
    zone = _ok(_zone(r, "Salle"), 201)["id"]

    def deactivate(session: Session) -> None:
        session.execute(
            text("SELECT id FROM restaurant_zones WHERE id = :z FOR UPDATE"), {"z": zone}
        )
        session.execute(
            text("UPDATE restaurant_zones SET is_active = false WHERE id = :z"), {"z": zone}
        )

    response = _step_by_step(
        app_engine, owner_db, r.tenant, deactivate, lambda: _table(r, "T1", zone_id=zone)
    )
    assert _code(response) == (422, "zone_inactive")
    assert _tables(r.owner, site_id=r.a) == []


def test_zone_deactivation_waits_for_a_table_creation_then_is_refused(
    resto: SimpleNamespace, app_engine: Engine, owner_db: Session
) -> None:
    r = resto
    zone = _ok(_zone(r, "Salle"), 201)["id"]

    def create_table(session: Session) -> None:
        # Comme le service : zone en partage, puis insertion de la table.
        session.execute(
            text("SELECT id FROM restaurant_zones WHERE id = :z FOR SHARE"), {"z": zone}
        )
        session.execute(
            text(
                "INSERT INTO restaurant_tables (id, tenant_id, site_id, zone_id, label, "
                "sort_order, is_active) VALUES (gen_random_uuid(), :t, :s, :z, 'T1', 0, true)"
            ),
            {"t": r.tenant, "s": r.a, "z": zone},
        )

    response = _step_by_step(
        app_engine,
        owner_db,
        r.tenant,
        create_table,
        lambda: r.owner.post(f"{ZONES}/{zone}/deactivate"),
    )
    assert _code(response) == (409, "zone_has_active_tables")
    assert _ok(r.owner.get(f"{ZONES}/{zone}"))["is_active"] is True


def test_table_reactivation_waits_for_a_zone_deactivation_then_is_refused(
    resto: SimpleNamespace, app_engine: Engine, owner_db: Session
) -> None:
    r = resto
    zone = _ok(_zone(r, "Salle"), 201)["id"]
    table = _ok(_table(r, "T1", zone_id=zone), 201)["id"]
    _ok(r.owner.post(f"{TABLES}/{table}/deactivate"))

    def deactivate(session: Session) -> None:
        session.execute(
            text("SELECT id FROM restaurant_zones WHERE id = :z FOR UPDATE"), {"z": zone}
        )
        session.execute(
            text("UPDATE restaurant_zones SET is_active = false WHERE id = :z"), {"z": zone}
        )

    response = _step_by_step(
        app_engine,
        owner_db,
        r.tenant,
        deactivate,
        lambda: r.owner.post(f"{TABLES}/{table}/activate"),
    )
    assert _code(response) == (422, "zone_inactive")
    assert _ok(r.owner.get(f"{TABLES}/{table}"))["is_active"] is False


def test_table_deactivation_waits_for_an_order_attachment_then_is_refused(
    resto: SimpleNamespace, app_engine: Engine, owner_db: Session
) -> None:
    """Simule l'écriture de R5-B (table prise en partage, puis commande rattachée) : la
    désactivation de la table attend, puis voit la commande et refuse."""
    r = resto
    table = _ok(_table(r, "T1"), 201)["id"]
    menu = _menu(r.owner, r.a, "C")
    order = _ok(_order(r.owner, r.a, [_line(menu.unit)]), 201)["id"]

    def attach(session: Session) -> None:
        session.execute(
            text("SELECT id FROM restaurant_tables WHERE id = :t FOR SHARE"), {"t": table}
        )
        session.execute(
            text("UPDATE restaurant_orders SET table_id = :t WHERE id = :o"),
            {"t": table, "o": order},
        )

    response = _step_by_step(
        app_engine,
        owner_db,
        r.tenant,
        attach,
        lambda: r.owner.post(f"{TABLES}/{table}/deactivate"),
    )
    assert _code(response) == (409, "table_has_open_orders")
    assert _ok(r.owner.get(f"{TABLES}/{table}"))["is_active"] is True


# --- Activation sans surprise ---------------------------------------------------------------


def test_new_site_activation_follows_the_profile_defaults(
    provision: Any, tclient: TestClient
) -> None:
    """Nouveau site Restaurant : activé par défaut (D11) ; Maquis : facultatif, désactivé, et
    activable explicitement par l'administrateur du site."""
    t = provision("rt-defaults", profile=MAQUIS, plan="ENTREPRISE")
    owner = _api(tclient, "owner@rt-defaults.example.com")
    modules = {m["code"]: m for m in _ok(owner.get(f"/sites/{t.site_id}/modules"))}
    assert modules[MODULE]["activated_for_site"] is False
    # Module effectif sur aucun site de l'entreprise : routeur refusé.
    assert _code(owner.post(TABLES, json={"site_id": str(t.site_id), "label": "T1"})) == (
        403,
        "module_unavailable",
    )
    assert set_site_module(owner, t.site_id, MODULE, True).status_code == 204
    _ok(owner.post(TABLES, json={"site_id": str(t.site_id), "label": "T1"}), 201)
    resto_site = _ok(
        add_site(owner, "Brasserie", "BRA", "restaurant", business_profile_code=RESTAURANT), 201
    )["id"]
    modules = {m["code"]: m for m in _ok(owner.get(f"/sites/{resto_site}/modules"))}
    assert modules[MODULE]["activated_for_site"] is True
    assert modules[MODULE]["effective"] is True
