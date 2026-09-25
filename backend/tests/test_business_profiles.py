"""Phase 3.1 — Secteurs, profils d'activité, profils UX (ADR-0024).

Registre et validation du catalogue, configuration par défaut vs expérience effective,
combinaisons profil × plan × module × permission × abonnement, provisioning, changement de
profil contrôlé, isolation des tenants (API et RLS), portée des sites, extensibilité.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.platform.catalog.loader import DATA_DIR, CatalogError, load_catalog, profile_ux
from app.platform.catalog.sync import sync_catalog
from app.platform.registry import get_registry
from tests.conftest import Api
from tests.stock_helpers import member

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _caps(api: Api) -> dict[str, Any]:
    response = api.get("/me/capabilities")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _groups(caps: dict[str, Any]) -> dict[str, list[str]]:
    return {g["group"]: g["modules"] for g in caps["ux"]["navigation"]}


def _copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


# --- Registre et validation du catalogue ------------------------------------------------


def test_catalog_declares_hierarchy_sectors_profiles_and_ux_profiles() -> None:
    catalog = load_catalog(get_registry())
    assert [s.code for s in sorted(catalog.sectors.values(), key=lambda s: s.sort_order)] == [
        "retail",
        "restaurant",
        "automobile",
        "distribution",
    ]
    assert set(catalog.ux_profiles) == {
        "retail.default",
        "restaurant.default",
        "automobile.default",
        "distribution.default",
    }
    reference = {
        "retail.alimentation",
        "retail.vetements",
        "retail.quincaillerie",
        "restaurant.restaurant",
        "restaurant.maquis",
        "automobile.garage",
        "automobile.pieces_detachees",
        "distribution.grossiste",
        "distribution.entrepot",
    }
    assert reference <= set(catalog.profiles)
    for profile in catalog.profiles.values():
        # Code technique stable <secteur>.<activité>, jamais un libellé.
        assert profile.code.startswith(f"{profile.sector}.")
        assert profile.ux_profile in catalog.ux_profiles
    # Plusieurs profils partagent un profil UX, avec leurs propres surcharges.
    maquis = profile_ux(catalog, catalog.profiles["restaurant.maquis"])
    resto = profile_ux(catalog, catalog.profiles["restaurant.restaurant"])
    assert maquis == resto
    assert resto.terminology["fr"]["catalog"]["item"] == "Produit"
    # Surcharges : navigation (remplacée), thème (fusionné), terminologie (fusionnée).
    alimentation = profile_ux(catalog, catalog.profiles["retail.alimentation"])
    assert [g.group for g in alimentation.navigation][:3] == ["home", "sales", "cash"]
    assert alimentation.theme == {"accent": "green", "density": "comfortable"}
    pneus = profile_ux(catalog, catalog.profiles["automobile.pneumatique"])
    assert pneus.terminology["fr"]["catalog"] == {"item": "Pneu", "items": "Pneus"}
    # Modules : ceux du profil UX, sauf déclaration du profil (défaut, jamais « actifs »).
    assert "pos" not in catalog.profiles["distribution.entrepot"].modules
    assert "automobile.workshop" in catalog.profiles["automobile.garage"].modules
    assert "automobile.workshop" not in catalog.profiles["automobile.pieces_detachees"].modules


@pytest.mark.parametrize(
    ("relative", "change", "message"),
    [
        (
            "profiles/retail/sport.toml",
            lambda s: s.replace('sector = "retail"', 'sector = "spatial"'),
            "correspondre au chemin",
        ),
        (
            "profiles/retail/sport.toml",
            lambda s: s.replace('"retail.default"', '"retail.inconnu"'),
            "profil UX inconnu",
        ),
        (
            "ux_profiles/retail.default.toml",
            lambda s: s.replace('accent = "blue"', 'accent = "#ff00ff"'),
            "hors palette",
        ),
        (
            "ux_profiles/retail.default.toml",
            lambda s: s.replace('modules = ["catalog", "suppliers"]', 'modules = ["teleport"]'),
            "module inconnu teleport",
        ),
        (
            "ux_profiles/retail.default.toml",
            lambda s: s.replace('"sales:today"', '"sales-today"'),
            "référence invalide",
        ),
        (
            "ux_profiles/retail.default.toml",
            lambda s: s.replace('"alerts:low_stock"', '"teleport:low_stock"'),
            "module inconnu",
        ),
        (
            "ux_profiles/restaurant.default.toml",
            lambda s: s.replace('group = "cash"', 'group = "sales"'),
            "rubrique de navigation en double",
        ),
        (
            "sectors.toml",
            lambda s: s.replace("sort_order = 30\n", "sort_order = 30\nis_active = false\n"),
            "secteur inactif automobile",
        ),
    ],
)
def test_invalid_catalog_is_rejected(
    tmp_path: Path, relative: str, change: Any, message: str
) -> None:
    data = _copy_data(tmp_path)
    path = data / relative
    path.write_text(change(path.read_text()))
    with pytest.raises(CatalogError, match=message):
        load_catalog(get_registry(), data)


def test_profile_outside_a_sector_directory_is_rejected(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    shutil.copy(data / "profiles/retail/sport.toml", data / "profiles/sport.toml")
    with pytest.raises(CatalogError, match="hors d'un dossier de secteur"):
        load_catalog(get_registry(), data)


def test_inactive_profile_is_neither_listed_nor_provisioned(
    tmp_path: Path, owner_db: Session, provision: Any, api_for: Any
) -> None:
    provision("alpha")
    data = _copy_data(tmp_path)
    path = data / "profiles/retail/sport.toml"
    path.write_text(path.read_text() + "is_active = false\n")
    sync_catalog(owner_db, load_catalog(get_registry(), data))
    owner_db.commit()
    try:
        owner = api_for("owner@alpha.example.com")
        listed = owner.get("/business-profiles").json()
        assert "retail.sport" not in {p["code"] for p in listed["profiles"]}
        detail = owner.get("/business-profiles/retail.sport").json()
        assert detail["is_active"] is False
        with pytest.raises(Exception, match="Profil inconnu"):
            provision("beta", profile="retail.sport")
        response = owner.put("/tenant/business-profile", json={"code": "retail.sport"})
        assert response.status_code == 422
        assert response.json()["code"] == "unknown_profile"
    finally:
        sync_catalog(owner_db, load_catalog(get_registry()))
        owner_db.commit()


def test_catalog_api_lists_sectors_and_profiles_with_default_configuration(
    provision: Any, api_for: Any
) -> None:
    provision("alpha")
    owner = api_for("owner@alpha.example.com")
    catalog = owner.get("/business-profiles").json()
    assert [s["code"] for s in catalog["sectors"]] == [
        "retail",
        "restaurant",
        "automobile",
        "distribution",
    ]
    profiles = {p["code"]: p for p in catalog["profiles"]}
    assert profiles["restaurant.maquis"]["sector"] == "restaurant"
    assert profiles["restaurant.maquis"]["ux_profile"] == "restaurant.default"
    assert "restaurant.qr" in profiles["restaurant.restaurant"]["optional_modules"]
    # Classés par secteur puis par ordre du profil.
    sectors = [p["sector"] for p in catalog["profiles"]]
    assert sectors == sorted(
        sectors, key=["retail", "restaurant", "automobile", "distribution"].index
    )

    # Configuration PAR DÉFAUT : ce que le profil propose (ici, des rubriques planifiées).
    detail = owner.get("/business-profiles/restaurant.restaurant").json()
    groups = {g["group"]: g["modules"] for g in detail["ux"]["navigation"]}
    assert groups["restaurant"][:4] == [
        "restaurant.tables",
        "restaurant.orders",
        "restaurant.kitchen",
        "restaurant.menu",
    ]
    assert "restaurant.tables:occupied" in detail["ux"]["dashboard"]["widgets"]
    assert detail["ux"]["theme"]["accent"] == "orange"
    assert "restaurant.kitchen" in detail["upcoming"]

    unknown = owner.get("/business-profiles/restaurant.inconnu")
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "unknown_profile"


# --- Expérience effective : profil × module × plan × permission × abonnement ----------------


def test_restaurant_experience_shows_only_what_really_exists(provision: Any, api_for: Any) -> None:
    provision("resto", profile="restaurant.restaurant", plan="ENTREPRISE")
    caps = _caps(api_for("owner@resto.example.com"))
    assert caps["profile"]["code"] == "restaurant.restaurant"
    assert caps["profile"]["sector"]["code"] == "restaurant"
    assert caps["profile"]["ux_profile"] == "restaurant.default"
    groups = _groups(caps)
    # Salle, tables, commandes, cuisine, menu : planifiés → aucune rubrique « Restaurant ».
    assert "restaurant" not in groups
    assert list(groups) == ["home", "sales", "cash", "stock", "admin"]
    assert groups["stock"] == ["catalog", "stock", "inventory_count", "alerts", "suppliers"]
    assert not [w for w in caps["ux"]["dashboard"]["widgets"] if w.startswith("restaurant.")]
    assert caps["ux"]["dashboard"]["widgets"][:2] == ["sales:today", "cash_register:open_sessions"]
    # « À venir » : modules planifiés du profil, dans l'ordre de sa navigation.
    assert caps["ux"]["upcoming"] == [
        "restaurant.tables",
        "restaurant.orders",
        "restaurant.kitchen",
        "restaurant.menu",
        "restaurant.recipes",
        "restaurant.qr",
        "reports",
        "payments",
    ]
    assert caps["ux"]["theme"] == {"accent": "orange", "density": "comfortable", "icon": None}
    assert caps["terminology"]["fr"]["catalog"]["items"] == "Produits"
    # Aucune route planifiée n'est servie.
    owner = api_for("owner@resto.example.com")
    assert owner.get("/restaurant/tables").status_code == 404


@pytest.mark.parametrize(
    ("profile", "first_groups", "accent", "density", "item"),
    [
        (
            "retail.alimentation",
            ["home", "sales", "cash", "catalog"],
            "green",
            "comfortable",
            "Article",
        ),
        (
            "retail.quincaillerie",
            ["home", "catalog", "stock", "sales"],
            "blue",
            "comfortable",
            "Article",
        ),
        (
            "automobile.garage",
            ["home", "catalog", "stock", "sales"],
            "teal",
            "comfortable",
            "Pièce",
        ),
        (
            "distribution.grossiste",
            ["home", "stock", "catalog", "sales"],
            "indigo",
            "compact",
            "Produit",
        ),
    ],
)
def test_each_sector_gets_its_own_experience(
    provision: Any,
    api_for: Any,
    profile: str,
    first_groups: list[str],
    accent: str,
    density: str,
    item: str,
) -> None:
    provision("alpha", profile=profile)
    caps = _caps(api_for("owner@alpha.example.com"))
    assert caps["profile"]["code"] == profile
    assert list(_groups(caps))[:4] == first_groups
    assert (caps["ux"]["theme"]["accent"], caps["ux"]["theme"]["density"]) == (accent, density)
    assert caps["terminology"]["fr"]["catalog"]["item"] == item
    # La navigation plate (compatibilité) suit les rubriques.
    flat = [m for g in caps["ux"]["navigation"] for m in g["modules"]]
    assert caps["navigation"][: len(flat)] == flat


def test_garage_declares_workshop_as_upcoming_without_exposing_it(
    provision: Any, api_for: Any
) -> None:
    provision("garage", profile="automobile.garage")
    caps = _caps(api_for("owner@garage.example.com"))
    assert "workshop" not in _groups(caps)
    assert {"automobile.workshop", "automobile.vehicles"} <= set(caps["ux"]["upcoming"])
    assert "automobile.workshop:open_orders" not in caps["ux"]["dashboard"]["widgets"]


def test_default_modules_are_not_active_modules(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    """Défaut ≠ effectif : profil ∩ plan ∩ activations (dépendances comprises)."""
    provision("depot", profile="distribution.entrepot", plan="STANDARD")
    caps = _caps(api_for("owner@depot.example.com"))
    modules = {m["code"] for m in caps["modules"]}
    assert not {"pos", "cash_register"} & modules
    assert "pos" not in [m for g in caps["ux"]["navigation"] for m in g["modules"]]
    assert "pos:open" not in caps["ux"]["dashboard"]["shortcuts"]
    # STANDARD : pas de transferts (fonctionnalité du plan), même pour un profil « stock ».
    assert "stock.transfers" not in caps["features"]

    provision("shop", profile="retail.alimentation", plan="ENTREPRISE")
    owner = api_for("owner@shop.example.com")
    assert "pos:open" in _caps(owner)["ux"]["dashboard"]["shortcuts"]
    assert owner.put("/modules/pos", json={"enabled": False}).status_code == 204
    caps = _caps(owner)
    assert "pos" not in _groups(caps)["sales"]
    assert "pos:open" not in caps["ux"]["dashboard"]["shortcuts"]


def test_profile_never_grants_permissions(provision: Any, api_for: Any, client: TestClient) -> None:
    provision("resto", profile="restaurant.restaurant")
    owner = api_for("owner@resto.example.com")
    seller = member(SimpleNamespace(owner=owner), client, "vendeur@resto.example.com", "seller")
    viewer = member(SimpleNamespace(owner=owner), client, "consult@resto.example.com", "viewer")
    seller_caps = _caps(seller)
    viewer_caps = _caps(viewer)
    # Même présentation pour tous les membres (profil du tenant)…
    assert seller_caps["ux"]["navigation"] == _caps(owner)["ux"]["navigation"]
    # … mais les droits restent ceux des rôles : le frontend masque, le backend refuse.
    assert "stock.entry.create" not in seller_caps["permissions"]
    assert "pos.terminal.use" not in viewer_caps["permissions"]
    assert seller.post("/stock/entries", json={}).status_code == 403
    assert viewer.post("/pos/checkout", json={}).status_code == 403
    assert seller.get("/business-profiles").status_code == 403
    response = seller.put("/tenant/business-profile", json={"code": "retail.alimentation"})
    assert response.status_code == 403
    assert (
        viewer.put("/tenant/business-profile", json={"code": "retail.alimentation"}).status_code
        == 403
    )
    assert "organization.profile.view" in viewer_caps["permissions"]


def test_expired_subscription_is_not_bypassed_by_the_profile(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    provision("resto", profile="restaurant.restaurant")
    owner_db.execute(
        text("UPDATE subscriptions SET current_period_end = now() - interval '90 days'")
    )
    owner_db.commit()
    owner = api_for("owner@resto.example.com")
    caps = _caps(owner)
    assert caps["subscription"]["status"] == "expired"
    assert "pos.terminal.use" in caps["restricted_permissions"]
    assert "organization.profile.manage" in caps["restricted_permissions"]
    # La présentation reste celle du profil ; les écritures restent refusées.
    assert "sales" in _groups(caps)
    response = owner.put("/tenant/business-profile", json={"code": "restaurant.maquis"})
    assert response.status_code == 403
    assert response.json()["code"] == "subscription_restricted"


def test_profile_belongs_to_the_tenant_not_to_the_site(
    provision: Any, api_for: Any, client: TestClient
) -> None:
    t = provision("alpha", profile="retail.quincaillerie")
    owner = api_for("owner@alpha.example.com")
    depot = owner.post("/sites", json={"name": "Dépôt", "code": "DEP", "kind": "warehouse"})
    seller = member(
        SimpleNamespace(owner=owner),
        client,
        "vendeur@alpha.example.com",
        "seller",
        all_sites=False,
        site_ids=[str(t.site_id)],
    )
    for api, site in ((owner, depot.json()["id"]), (seller, str(t.site_id)), (owner, None)):
        scoped = Api(api.client, api.token, site)
        caps = _caps(scoped)
        assert caps["profile"]["code"] == "retail.quincaillerie"
        assert caps["ux"]["navigation"] == _caps(owner)["ux"]["navigation"]
    # La portée des sites reste appliquée indépendamment du profil.
    assert [s["id"] for s in _caps(seller)["sites"]] == [str(t.site_id)]
    refused = Api(seller.client, seller.token, depot.json()["id"]).get("/me/capabilities")
    assert refused.status_code == 403


# --- Provisioning ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "enabled", "absent"),
    [
        ("retail.alimentation", {"pos", "cash_register", "stock"}, {"restaurant.tables"}),
        ("restaurant.maquis", {"pos", "restaurant.tables", "restaurant.orders"}, {"restaurant.qr"}),
        ("automobile.garage", {"stock", "pos"}, {"automobile.workshop"}),
        ("distribution.entrepot", {"stock", "sales"}, {"pos", "cash_register"}),
    ],
)
def test_provisioning_initialises_profile_modules_within_the_plan(
    provision: Any, owner_db: Session, profile: str, enabled: set[str], absent: set[str]
) -> None:
    t = provision("alpha", profile=profile, plan="STANDARD")
    rows = dict(
        owner_db.execute(
            text("SELECT module_code, enabled FROM tenant_modules WHERE tenant_id = :t"),
            {"t": t.tenant_id},
        ).all()
    )
    assert {code for code, on in rows.items() if on} >= enabled
    # Optionnels désactivés ; hors plan (automobile planifié, qr en STANDARD) : aucune ligne.
    assert not absent & {code for code, on in rows.items() if on}
    audit = owner_db.execute(
        text("SELECT data FROM audit_logs WHERE tenant_id = :t AND action = 'tenant.provisioned'"),
        {"t": t.tenant_id},
    ).scalar_one()
    assert audit["profile"] == profile
    assert audit["sector"] == profile.split(".")[0]


# --- Changement de profil ----------------------------------------------------------------


def test_profile_change_is_controlled_audited_and_keeps_data(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("shop", profile="retail.alimentation", plan="ENTREPRISE")
    owner = api_for("owner@shop.example.com")
    category = owner.post("/catalog/categories", json={"name": "Divers"}).json()
    article = owner.post(
        "/catalog/articles",
        json={
            "reference": "A-1",
            "designation": "Riz 25 kg",
            "category_id": category["id"],
            "unit": "u",
            "purchase_price": "100",
            "sale_price": "150",
        },
    ).json()

    # Entrepôt : ni point de vente ni caisse ; ils sont activés → refus explicite.
    response = owner.put("/tenant/business-profile", json={"code": "distribution.entrepot"})
    assert response.status_code == 409
    assert response.json()["code"] == "profile_change_incompatible"
    assert response.json()["modules"] == ["cash_register", "pos"]
    assert owner.get("/tenant").json()["business_profile_code"] == "retail.alimentation"

    unknown = owner.put("/tenant/business-profile", json={"code": "retail.inconnu"})
    assert (unknown.status_code, unknown.json()["code"]) == (422, "unknown_profile")

    # Vers la restauration : modules proposés par défaut et inclus au plan activés.
    changed = owner.put("/tenant/business-profile", json={"code": "restaurant.maquis"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["business_profile_code"] == "restaurant.maquis"
    caps = _caps(owner)
    assert caps["profile"]["sector"]["code"] == "restaurant"
    assert caps["terminology"]["fr"]["catalog"]["item"] == "Produit"
    assert "restaurant.tables" in {m["code"] for m in caps["modules"]}
    # Données conservées.
    assert owner.get(f"/catalog/articles/{article['id']}").json()["designation"] == "Riz 25 kg"
    audit = owner_db.execute(
        text(
            "SELECT data FROM audit_logs WHERE tenant_id = :t AND action = 'tenant.profile_changed'"
        ),
        {"t": t.tenant_id},
    ).scalar_one()
    assert audit["previous_profile"] == "retail.alimentation"
    assert audit["profile"] == "restaurant.maquis"
    assert "restaurant.tables" in audit["enabled_modules"]

    # Même profil : aucun changement, aucun nouvel audit.
    assert (
        owner.put("/tenant/business-profile", json={"code": "restaurant.maquis"}).status_code == 200
    )
    count = owner_db.execute(
        text(
            "SELECT count(*) FROM audit_logs "
            "WHERE tenant_id = :t AND action = 'tenant.profile_changed'"
        ),
        {"t": t.tenant_id},
    ).scalar_one()
    assert count == 1

    # Après désactivation du point de vente puis de la caisse (données conservées), l'entrepôt
    # est accepté.
    assert owner.put("/modules/pos", json={"enabled": False}).status_code == 204
    assert owner.put("/modules/cash_register", json={"enabled": False}).status_code == 204
    moved = owner.put("/tenant/business-profile", json={"code": "distribution.entrepot"})
    assert moved.status_code == 200, moved.text
    assert _caps(owner)["ux"]["theme"]["density"] == "compact"


def test_cli_change_profile(provision: Any, settings: Any, capsys: Any, owner_db: Session) -> None:
    from app.cli import main

    t = provision("alpha", profile="retail.quincaillerie")
    assert (
        main(
            ["change-profile", "--tenant-id", str(t.tenant_id), "--profile", "retail.sport"],
            settings,
        )
        == 0
    )
    assert "retail.quincaillerie → retail.sport" in capsys.readouterr().out
    code = owner_db.execute(
        text("SELECT business_profile_code FROM tenants WHERE id = :t"), {"t": t.tenant_id}
    ).scalar_one()
    assert code == "retail.sport"
    assert (
        main(["change-profile", "--tenant-id", str(t.tenant_id), "--profile", "x.y"], settings) == 1
    )
    assert "Profil inconnu" in capsys.readouterr().err


# --- Isolation des tenants -----------------------------------------------------------------


def test_each_tenant_only_sees_and_changes_its_own_profile(
    provision: Any, api_for: Any, app_engine: Engine, owner_db: Session
) -> None:
    a = provision("alpha", profile="retail.alimentation")
    b = provision("beta", profile="restaurant.restaurant")
    alpha = api_for("owner@alpha.example.com")
    beta = api_for("owner@beta.example.com")
    assert _caps(alpha)["profile"]["code"] == "retail.alimentation"
    assert _caps(beta)["profile"]["code"] == "restaurant.restaurant"

    # Le tenant est celui du jeton : aucun paramètre ne permet d'en viser un autre.
    response = alpha.put(
        "/tenant/business-profile",
        json={"code": "retail.sport", "tenant_id": str(b.tenant_id)},
    )
    assert response.status_code == 200
    assert _caps(beta)["profile"]["code"] == "restaurant.restaurant"
    assert _caps(alpha)["profile"]["code"] == "retail.sport"

    # RLS (rôle applicatif, sans BYPASSRLS) : le tenant B est invisible et intouchable.
    with create_session_factory(app_engine)() as session:
        set_db_context(session, tenant_id=a.tenant_id, user_id=None)
        rows = session.execute(text("SELECT id, business_profile_code FROM tenants")).all()
        assert [(r[0], r[1]) for r in rows] == [(a.tenant_id, "retail.sport")]
        updated = session.execute(
            text("UPDATE tenants SET business_profile_code = 'retail.sport' WHERE id = :b"),
            {"b": b.tenant_id},
        )
        assert updated.rowcount == 0
        session.rollback()
    code = owner_db.execute(
        text("SELECT business_profile_code FROM tenants WHERE id = :b"), {"b": b.tenant_id}
    ).scalar_one()
    assert code == "restaurant.restaurant"


def test_catalog_tables_are_read_only_for_the_application_role(app_engine: Engine) -> None:
    for table in ("business_sectors", "ux_profiles", "business_profiles"):
        with app_engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(text(f"UPDATE {table} SET is_active = false"))
            conn.rollback()


# --- Extensibilité -------------------------------------------------------------------------


def test_new_profile_is_added_by_configuration_only(
    tmp_path: Path, owner_db: Session, provision: Any, api_for: Any
) -> None:
    """Ajouter ``retail.librairie`` = un fichier de définition (+ ses traductions côté
    interface) : aucun service métier, ni le POS, ni la RLS, ni le RBAC ne changent."""
    data = _copy_data(tmp_path)
    definition = data / "profiles/retail/librairie.toml"
    definition.unlink()
    assert "retail.librairie" not in load_catalog(get_registry(), data).profiles

    definition.write_text(
        'code = "retail.librairie"\n'
        'sector = "retail"\n'
        'ux_profile = "retail.default"\n'
        'name = "Librairie / Papeterie"\n'
        'description = "Livres et fournitures."\n'
        "sort_order = 60\n"
        "\n"
        "[terminology.fr.catalog]\n"
        'item = "Ouvrage"\n'
        'items = "Ouvrages"\n'
    )
    catalog = load_catalog(get_registry(), data)
    profile = catalog.profiles["retail.librairie"]  # profil reconnu
    assert profile.ux_profile == "retail.default"  # profil UX associé
    config = profile_ux(catalog, profile)
    assert [g.group for g in config.navigation] == [
        "home",
        "catalog",
        "stock",
        "sales",
        "cash",
        "reports",
        "admin",
    ]
    assert config.terminology["fr"]["catalog"]["items"] == "Ouvrages"
    assert "alerts:low_stock" in config.widgets

    sync_catalog(owner_db, catalog)
    owner_db.commit()
    try:
        provision("livres", profile="retail.librairie")
        caps = _caps(api_for("owner@livres.example.com"))
        assert caps["profile"]["code"] == "retail.librairie"
        assert caps["profile"]["sector"]["code"] == "retail"
        assert list(_groups(caps)) == ["home", "catalog", "stock", "sales", "cash", "admin"]
        assert caps["terminology"]["fr"]["catalog"]["items"] == "Ouvrages"
        assert "sales:recent" in caps["ux"]["dashboard"]["widgets"]
    finally:
        sync_catalog(owner_db, load_catalog(get_registry()))
        owner_db.commit()


def test_core_code_never_tests_a_sector_or_a_profile() -> None:
    """Aucun code de profil ni test de secteur dans le code applicatif : la spécialisation
    passe uniquement par les données du catalogue."""
    codes = set(load_catalog(get_registry()).profiles)
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        source = path.read_text()
        if any(f'"{code}"' in source for code in codes) or "business_type" in source:
            offenders.append(str(path.relative_to(APP_DIR)))
        if "business_profile_code ==" in source or "sector_code ==" in source:
            offenders.append(str(path.relative_to(APP_DIR)))
    assert offenders == []
