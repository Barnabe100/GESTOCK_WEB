import io
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.cli import main
from app.core.config import Settings
from app.core.errors import BusinessRuleError, ConflictError


def test_provisioning_creates_complete_tenant(provision: Any, owner_db: Session) -> None:
    result = provision("alpha", profile="restaurant", plan="ENTREPRISE", trial_days=30)
    row = owner_db.execute(
        text("SELECT business_profile_code, currency, locale FROM tenants WHERE id = :t"),
        {"t": result.tenant_id},
    ).one()
    assert tuple(row) == ("restaurant", "XOF", "fr")
    status = owner_db.execute(
        text("SELECT status FROM subscriptions WHERE tenant_id = :t"), {"t": result.tenant_id}
    ).scalar_one()
    assert status == "trial"
    roles = (
        owner_db.execute(
            text("SELECT template_code FROM roles WHERE tenant_id = :t AND is_system"),
            {"t": result.tenant_id},
        )
        .scalars()
        .all()
    )
    assert set(roles) == {"administrator", "stock_manager", "viewer"}
    modules = dict(
        owner_db.execute(
            text("SELECT module_code, enabled FROM tenant_modules WHERE tenant_id = :t"),
            {"t": result.tenant_id},
        ).all()
    )
    assert modules["restaurant.qr"] is False  # optionnel
    assert modules["restaurant.tables"] is True
    owner = owner_db.execute(
        text("SELECT is_owner, all_sites FROM tenant_memberships WHERE tenant_id = :t"),
        {"t": result.tenant_id},
    ).one()
    assert tuple(owner) == (True, True)
    assert result.owner_created is True


def test_provisioning_validations(provision: Any) -> None:
    with pytest.raises(BusinessRuleError, match="Slug"):
        provision("Pas Valide")
    with pytest.raises(BusinessRuleError, match="Profil"):
        provision("alpha", profile="inconnu")
    with pytest.raises(BusinessRuleError, match="Plan"):
        provision("alpha", plan="GOLD")
    provision("alpha")
    with pytest.raises(ConflictError):
        provision("alpha", owner_email="autre@example.com")


def test_cli_create_tenant(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    migrated: None,
    owner_db: Session,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("Provisoire-123\n"))
    code = main(
        [
            "create-tenant",
            "--name",
            "Boutique CLI",
            "--slug",
            "boutique-cli",
            "--profile",
            "commerce_general",
            "--plan",
            "STANDARD",
            "--billing",
            "annual",
            "--owner-email",
            "cli@example.com",
            "--owner-name",
            "Cli",
            "--owner-password-stdin",
        ],
        settings,
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Tenant créé" in out
    assert "changer son mot de passe" in out
    billing = owner_db.execute(
        text(
            "SELECT billing_period FROM subscriptions s JOIN tenants t ON t.id = s.tenant_id "
            "WHERE t.slug = 'boutique-cli'"
        )
    ).scalar_one()
    assert billing == "annual"


def test_cli_reports_errors(
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
    migrated: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    code = main(
        [
            "create-tenant",
            "--name",
            "X",
            "--slug",
            "x-shop",
            "--profile",
            "alimentation",
            "--plan",
            "STANDARD",
            "--owner-email",
            "nouveau@example.com",
            "--owner-name",
            "N",
            "--owner-password-stdin",
        ],
        settings,
    )
    assert code == 1
    assert "Mot de passe provisoire requis" in capsys.readouterr().err


def test_cli_catalog_commands(
    settings: Settings, capsys: pytest.CaptureFixture[str], migrated: None
) -> None:
    assert main(["catalog", "check"], settings) == 0
    assert main(["catalog", "sync"], settings) == 0
    assert "Catalogue synchronisé : 4 profils, 2 plans" in capsys.readouterr().out
