import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.catalog.loader import DATA_DIR, CatalogError, load_catalog
from app.platform.catalog.models import BusinessProfile
from app.platform.catalog.sync import sync_catalog
from app.platform.registry import get_registry


def test_catalog_files_are_valid() -> None:
    catalog = load_catalog(get_registry())
    assert set(catalog.plans) == {"STANDARD", "ENTREPRISE"}
    assert {"alimentation", "quincaillerie", "restaurant"} <= set(catalog.profiles)
    # Une quincaillerie ne se voit proposer aucun module restaurant.
    quincaillerie = catalog.profiles["quincaillerie"]
    assert not [m for m in quincaillerie.modules if m.startswith("restaurant.")]


def _copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


def test_unknown_module_in_profile_is_rejected(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    path = data / "profiles" / "quincaillerie.toml"
    path.write_text(path.read_text().replace('"catalog",', '"catalog", "teleportation",', 1))
    with pytest.raises(CatalogError, match="teleportation"):
        load_catalog(get_registry(), data)


def test_profile_must_offer_dependencies(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    path = data / "profiles" / "quincaillerie.toml"
    path.write_text(path.read_text().replace('"catalog", ', "", 1))
    with pytest.raises(CatalogError, match="dépend"):
        load_catalog(get_registry(), data)


def test_missing_subscription_policy_is_rejected(tmp_path: Path) -> None:
    data = _copy_data(tmp_path)
    path = data / "subscription_policies.toml"
    path.write_text(path.read_text().split("[policies.cancelled]")[0])
    with pytest.raises(CatalogError, match="statuts attendus"):
        load_catalog(get_registry(), data)


def test_role_templates_resolve_patterns() -> None:
    templates = load_catalog(get_registry()).role_templates
    assert set(templates) == {"administrator", "manager", "seller", "viewer"}
    available = {
        "users.member.view",
        "users.member.manage",
        "audit.log.view",
        "organization.site.view",
        "organization.module.view",
        # Permission d'un module futur : les motifs l'incluent sans migration (ADR-0013).
        "sales.sale.view",
        "sales.sale.create",
    }
    # Consultant : *.view sauf utilisateurs, audit et modules.
    assert templates["viewer"].resolve(available) == ["organization.site.view", "sales.sale.view"]
    # Administrateur : tout, y compris un module apparu après la création du tenant.
    assert templates["administrator"].resolve(available) == sorted(available)
    assert templates["administrator"].protected is True
    assert not any(t.protected for c, t in templates.items() if c != "administrator")
    # Vendeur : aucune permission de module non développé n'est déclarée.
    assert templates["seller"].resolve(available) == []


def test_sync_deactivates_removed_profiles(
    owner_db: Session, tmp_path: Path, clean_db: Any
) -> None:
    data = _copy_data(tmp_path)
    (data / "profiles" / "commerce_general.toml").unlink()
    report = sync_catalog(owner_db, load_catalog(get_registry(), data))
    assert report.deactivated_profiles == ["commerce_general"]
    profile = owner_db.scalars(
        select(BusinessProfile).where(BusinessProfile.code == "commerce_general")
    ).one()
    assert profile.is_active is False
    owner_db.rollback()
