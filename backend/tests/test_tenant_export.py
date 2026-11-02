"""Restauration par entreprise — export depuis une copie restaurée (SAUVEGARDE_RESTAURATION §6) :
toutes les tables portant ``tenant_id`` (découvertes dans le schéma), lignes de CETTE entreprise
seulement, aucun secret (``password_hash``), empreintes SHA-256 exactes, lecture seule,
répertoire neuf. Ici la « copie » est la base de test (même schéma)."""

import csv
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.cli import main
from app.core.config import Settings
from app.platform.backup.tenant_export import PLATFORM_TABLES, TenantExportError, export_tenant
from tests import stock_helpers as sh
from tests.stock_helpers import World


@pytest.fixture
def two_tenants(world: World, provision: Any, api_for: Any) -> dict[str, Any]:
    sh.validated_entry(world, [(0, "10", "100")])
    sale = world.owner.post(
        "/sales",
        json={"site_id": world.site, "lines": [{"article_id": world.articles[0], "quantity": "1"}]},
    ).json()
    beta = provision("beta")
    beta_api = api_for("owner@beta.example.com")
    category = beta_api.post("/catalog/categories", json={"name": "Secret beta"}).json()
    return {"sale": sale, "beta": beta.tenant_id, "beta_category": category["id"]}


def _alpha(owner_db: Session) -> uuid.UUID:
    return uuid.UUID(
        str(owner_db.execute(text("SELECT id FROM tenants WHERE slug = 'alpha'")).scalar_one())
    )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_export_contains_only_this_tenant_and_no_secret(
    two_tenants: dict[str, Any], owner_engine: Engine, owner_db: Session, tmp_path: Path
) -> None:
    alpha = _alpha(owner_db)
    out = tmp_path / "alpha"
    manifest = export_tenant(owner_engine, alpha, out)
    names = {t["name"] for t in manifest["tables"]}
    # Toutes les tables d'entreprise du schéma, sauf le journal de la console.
    expected = (
        set(
            owner_db.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
                )
            ).scalars()
        )
        - PLATFORM_TABLES
    )
    assert expected | {"tenants", "users"} == names
    assert (
        manifest["alembic_revision"]
        == owner_db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    )
    for table in manifest["tables"]:
        path = out / f"{table['name']}.csv"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == table["sha256"]
        assert path.stat().st_mode & 0o077 == 0
        content = path.read_text(encoding="utf-8")
        assert str(two_tenants["beta"]) not in content, table["name"]
        assert "Secret beta" not in content
        for row in _rows(path):
            if "tenant_id" in row:
                assert row["tenant_id"] == str(alpha), table["name"]
    assert out.stat().st_mode & 0o077 == 0
    users = _rows(out / "users.csv")
    assert "password_hash" not in users[0]
    assert {u["email"] for u in users} == {"owner@alpha.example.com"}
    sales = _rows(out / "sales.csv")
    assert [s["id"] for s in sales] == [two_tenants["sale"]["id"]]
    assert json.loads((out / "manifest.json").read_text())["tenant_id"] == str(alpha)


def test_export_is_read_only_and_refuses_unsafe_targets(
    two_tenants: dict[str, Any], owner_engine: Engine, owner_db: Session, tmp_path: Path
) -> None:
    alpha = _alpha(owner_db)
    before = owner_db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()
    export_tenant(owner_engine, alpha, tmp_path / "first")
    owner_db.expire_all()
    assert owner_db.execute(text("SELECT count(*) FROM audit_logs")).scalar_one() == before
    # Répertoire non vide : refus (jamais d'écrasement).
    with pytest.raises(TenantExportError, match="pas vide"):
        export_tenant(owner_engine, alpha, tmp_path / "first")
    with pytest.raises(TenantExportError, match="absente"):
        export_tenant(owner_engine, uuid.uuid4(), tmp_path / "unknown")


def test_cli_exports_from_the_given_database(
    two_tenants: dict[str, Any], settings: Settings, owner_db: Session, tmp_path: Path
) -> None:
    alpha = _alpha(owner_db)
    out = tmp_path / "cli"
    code = main(
        [
            "tenant",
            "export",
            "--tenant-id",
            str(alpha),
            "--out",
            str(out),
            "--database-url",
            settings.migration_database_url,
        ],
        settings,
    )
    assert code == 0
    assert (out / "manifest.json").exists()
    assert main(["tenant", "export", "--tenant-id", str(alpha), "--out", str(out)], settings) == 1
