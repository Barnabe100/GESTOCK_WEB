"""Phase 3.2 — Référentiel des pays, informations d'entreprise du tenant, paramètres
commerciaux des plans (préservés par la synchronisation), prix figé de l'abonnement,
limitation de fréquence persistante."""

import shutil
import threading
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.db import create_session_factory, set_db_context
from app.core.errors import AppError, TooManyRequestsError, register_error_handlers
from app.platform.catalog.loader import DATA_DIR, CatalogError, load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.ratelimit.service import RateLimiter
from app.platform.registry import get_registry
from app.shared.clock import utcnow
from tests.stock_helpers import member


def _copy_data(tmp_path: Path) -> Path:
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


# --- Référentiel des pays ------------------------------------------------------------------


def test_country_reference_is_complete_iso_3166() -> None:
    countries = load_catalog(get_registry()).countries
    assert len(countries) == 249
    assert all(len(code) == 2 and code.isupper() for code in countries)
    bf = countries["BF"]
    assert (bf.name, bf.currency, bf.calling_code, bf.timezone) == (
        "Burkina Faso",
        "XOF",
        226,
        "Africa/Ouagadougou",
    )
    assert countries["FR"].currency == "EUR"
    # Territoires sans population permanente : présents mais non proposés.
    inactive = {code for code, c in countries.items() if not c.is_active}
    assert inactive == {"AQ", "BV", "GS", "HM", "PN", "TF", "UM"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda s: s.replace('timezone = "Africa/Ouagadougou"', 'timezone = "Mars/Olympus"'),
            "fuseau",
        ),
        (
            lambda s: s.replace(
                '[countries.BF]\nname = "Burkina Faso"\ncurrency = "XOF"\n',
                '[countries.BF]\nname = "Burkina Faso"\n',
            ),
            "exige une devise",
        ),
        (lambda s: s.replace("[countries.BF]", "[countries.bf]"), "alpha-2"),
        (lambda s: s.replace('currency = "XOF"', 'currency = "xof"', 1), "ISO 4217"),
    ],
)
def test_invalid_country_data_is_rejected(tmp_path: Path, change: Any, message: str) -> None:
    data = _copy_data(tmp_path)
    path = data / "countries.toml"
    path.write_text(change(path.read_text()))
    with pytest.raises(CatalogError, match=message):
        load_catalog(get_registry(), data)


def test_public_countries_without_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/public/geo/countries")
    assert response.status_code == 200
    assert "max-age" in response.headers["cache-control"]
    countries = response.json()
    codes = [c["code"] for c in countries]
    assert len(codes) == 242
    assert "AQ" not in codes
    assert {
        "code": "BF",
        "name": "Burkina Faso",
        "currency": "XOF",
        "calling_code": 226,
        "timezone": "Africa/Ouagadougou",
    } in countries
    # Tri alphabétique sans tenir compte des accents : « Égypte » parmi les E.
    names = [c["name"] for c in countries]
    assert names.index("Égypte") < names.index("Espagne") < names.index("Éthiopie")


def test_removed_country_is_deactivated_never_deleted(
    tmp_path: Path, owner_db: Session, client: TestClient
) -> None:
    data = _copy_data(tmp_path)
    path = data / "countries.toml"
    source = path.read_text()
    start = source.index("[countries.ZW]")
    path.write_text(source[:start])
    try:
        report = sync_catalog(owner_db, load_catalog(get_registry(), data))
        owner_db.commit()
        assert report.deactivated_countries == ["ZW"]
        row = owner_db.execute(text("SELECT is_active FROM geo_countries WHERE code = 'ZW'"))
        assert row.scalar_one() is False
        codes = [c["code"] for c in client.get("/api/v1/public/geo/countries").json()]
        assert "ZW" not in codes
    finally:
        sync_catalog(owner_db, load_catalog(get_registry()))
        owner_db.commit()


# --- Plans : paramètres commerciaux jamais écrasés par la synchronisation ----------------------


def test_catalog_sync_never_overwrites_commercial_settings(owner_db: Session) -> None:
    fresh = owner_db.execute(
        text(
            "SELECT listed, price_display_enabled, monthly_price, monthly_price_enabled, "
            "annual_price_enabled, contact_required, trial_days FROM plans WHERE code = 'STANDARD'"
        )
    ).one()
    # Valeurs neutres : rien de publié, aucun prix, aucun essai.
    assert tuple(fresh) == (False, False, None, False, False, False, 0)
    owner_db.execute(
        text(
            "UPDATE plans SET listed = true, price_display_enabled = true, monthly_price = 5000, "
            "monthly_price_enabled = true, currency = 'XOF', trial_days = 7, "
            "commercial_description = 'Pour démarrer', display_order = 3 WHERE code = 'STANDARD'"
        )
    )
    owner_db.commit()
    try:
        sync_catalog(owner_db, load_catalog(get_registry()))
        owner_db.commit()
        row = owner_db.execute(
            text(
                "SELECT listed, monthly_price, currency, trial_days, commercial_description, "
                "display_order FROM plans WHERE code = 'STANDARD'"
            )
        ).one()
        assert tuple(row) == (True, Decimal("5000.00"), "XOF", 7, "Pour démarrer", 3)
    finally:
        _reset_plans(owner_db)


def _reset_plans(owner_db: Session) -> None:
    owner_db.execute(
        text(
            "UPDATE plans SET listed = false, price_display_enabled = false, monthly_price = NULL, "
            "monthly_price_enabled = false, annual_price = NULL, annual_price_enabled = false, "
            "currency = NULL, contact_required = false, commercial_description = NULL, "
            "display_order = 0, trial_days = 0"
        )
    )
    owner_db.commit()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE plans SET monthly_price = -1",
        "UPDATE plans SET trial_days = -1",
        "UPDATE plans SET monthly_price_enabled = true, monthly_price = NULL",
        "UPDATE plans SET annual_price_enabled = true, annual_price = 100, currency = NULL",
        "UPDATE plans SET currency = 'xof'",
    ],
)
def test_commercial_constraints_are_enforced_by_the_database(owner_db: Session, sql: str) -> None:
    with pytest.raises(Exception, match="check constraint"):
        owner_db.execute(text(sql))
    owner_db.rollback()


def test_subscription_price_is_frozen_at_subscription(provision: Any, owner_db: Session) -> None:
    owner_db.execute(
        text(
            "UPDATE plans SET monthly_price = 5000, monthly_price_enabled = true, "
            "currency = 'XOF' WHERE code = 'STANDARD'"
        )
    )
    owner_db.commit()
    try:
        priced = provision("alpha", plan="STANDARD")
        unpriced = provision("beta", plan="ENTREPRISE")
        owner_db.execute(text("UPDATE plans SET monthly_price = 9000 WHERE code = 'STANDARD'"))
        owner_db.commit()
        rows = dict(
            owner_db.execute(
                text(
                    "SELECT tenant_id, (price_at_subscription, currency_at_subscription)::text "
                    "FROM subscriptions WHERE tenant_id IN (:a, :b)"
                ),
                {"a": priced.tenant_id, "b": unpriced.tenant_id},
            ).all()
        )
        assert rows[priced.tenant_id] == "(5000.00,XOF)"
        assert rows[unpriced.tenant_id] == "(,)"
    finally:
        _reset_plans(owner_db)


# --- Provisioning : pays obligatoire, devise et fuseau par défaut --------------------------


def test_provisioning_requires_an_active_country(provision: Any, owner_db: Session) -> None:
    for code in ("ZZ", "AQ"):
        with pytest.raises(AppError) as error:
            provision("alpha", country=code)
        assert error.value.code == "unknown_country"
    t = provision("gamma", country="fr")
    row = owner_db.execute(
        text("SELECT country_code, currency, timezone FROM tenants WHERE id = :t"),
        {"t": t.tenant_id},
    ).one()
    assert tuple(row) == ("FR", "EUR", "Europe/Paris")
    audit = owner_db.execute(
        text("SELECT data FROM audit_logs WHERE tenant_id = :t AND action = 'tenant.provisioned'"),
        {"t": t.tenant_id},
    ).scalar_one()
    assert audit["country"] == "FR"


def test_cli_requires_country(settings: Any, capsys: Any, migrated: None) -> None:
    from app.cli import main

    base = [
        "create-tenant",
        "--name",
        "X",
        "--slug",
        "x",
        "--profile",
        "retail.sport",
        "--plan",
        "STANDARD",
        "--owner-email",
        "x@example.com",
        "--owner-name",
        "X",
    ]
    with pytest.raises(SystemExit):
        main(base, settings)
    assert "--country" in capsys.readouterr().err
    assert main([*base, "--country", "BF", "--currency", "ZZZ"], settings) == 1
    assert "Devise invalide" in capsys.readouterr().err


# --- Entreprise du tenant -------------------------------------------------------------------


COMPANY = {
    "trade_name": "Chez Awa",
    "email": "Contact@ChezAwa.BF",
    "phone": "+226 70 11 22 33",
    "address": "Avenue Kwame Nkrumah",
    "city": "Ouagadougou",
    "region": "Centre",
    "website": "https://chezawa.bf",
    "tax_id": "00012345A",
    "trade_register": "BF-OUA-2026-B-1234",
    "description": "Supérette de quartier.",
    "logo_url": "https://cdn.chezawa.bf/logo.png",
}


def test_company_information_round_trip(provision: Any, api_for: Any, owner_db: Session) -> None:
    t = provision("alpha")
    owner = api_for("owner@alpha.example.com")
    tenant = owner.get("/tenant").json()
    assert tenant["country_code"] == "BF"
    assert all(tenant[k] is None for k in COMPANY)

    response = owner.patch("/tenant", json=COMPANY)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["email"] == "Contact@chezawa.bf"  # domaine normalisé
    assert saved["phone"] == "+22670112233"
    assert saved["logo_url"] == COMPANY["logo_url"]

    # Champs recommandés ou facultatifs : effaçables (null ou vide) ; les autres inchangés.
    cleared = owner.patch("/tenant", json={"phone": None, "tax_id": "", "logo_url": None}).json()
    assert (cleared["phone"], cleared["tax_id"], cleared["logo_url"]) == (None, None, None)
    assert cleared["trade_name"] == "Chez Awa"
    # Nom et fuseau jamais effacés ; devise non modifiable (champ ignoré).
    kept = owner.patch("/tenant", json={"name": None, "currency": "EUR"}).json()
    assert (kept["name"], kept["currency"]) == ("Entreprise alpha", "XOF")

    audit = owner_db.execute(
        text("SELECT count(*) FROM audit_logs WHERE tenant_id = :t AND action = 'tenant.updated'"),
        {"t": t.tenant_id},
    ).scalar_one()
    assert audit == 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "pas-un-email"),
        ("phone", "abc"),
        ("website", "http://chezawa.bf"),
        ("logo_url", "javascript:alert(1)"),
        ("logo_url", "https://user:secret@cdn.example.com/logo.png"),
        ("trade_name", "x" * 151),
        ("country_code", "BFA"),
    ],
)
def test_invalid_company_fields_are_refused(
    provision: Any, api_for: Any, field: str, value: str
) -> None:
    provision("alpha")
    response = api_for("owner@alpha.example.com").patch("/tenant", json={field: value})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_country_is_required_and_never_cleared(
    provision: Any, api_for: Any, owner_db: Session
) -> None:
    t = provision("alpha")
    owner = api_for("owner@alpha.example.com")
    for body, code in (
        ({"country_code": None}, "country_required"),
        ({"country_code": "ZZ"}, "unknown_country"),
        ({"country_code": "AQ"}, "unknown_country"),
    ):
        response = owner.patch("/tenant", json=body)
        assert (response.status_code, response.json()["code"]) == (422, code)
    assert owner.patch("/tenant", json={"country_code": "ci"}).json()["country_code"] == "CI"

    # Tenant antérieur à la 3.2 : pays nul (aucun remplissage artificiel) jusqu'à sa saisie.
    owner_db.execute(
        text("UPDATE tenants SET country_code = NULL WHERE id = :t"), {"t": t.tenant_id}
    )
    owner_db.commit()
    assert owner.get("/tenant").json()["country_code"] is None
    assert owner.patch("/tenant", json={"trade_name": "Sans pays"}).status_code == 200
    assert owner.patch("/tenant", json={"country_code": "BF"}).json()["country_code"] == "BF"


def test_company_information_requires_permission_and_stays_in_its_tenant(
    provision: Any, api_for: Any, client: TestClient, app_engine: Engine, owner_db: Session
) -> None:
    a = provision("alpha")
    b = provision("beta")
    owner = api_for("owner@alpha.example.com")
    seller = member(SimpleNamespace(owner=owner), client, "vendeur@alpha.example.com", "seller")
    assert seller.patch("/tenant", json={"trade_name": "Pirate"}).status_code == 403

    assert owner.patch("/tenant", json={"trade_name": "Alpha SARL"}).status_code == 200
    assert api_for("owner@beta.example.com").get("/tenant").json()["trade_name"] is None

    # RLS (rôle applicatif, sans BYPASSRLS) : les informations du tenant B sont intouchables.
    with create_session_factory(app_engine)() as session:
        set_db_context(session, tenant_id=a.tenant_id, user_id=None)
        updated = session.execute(
            text("UPDATE tenants SET trade_name = 'Pirate', tax_id = 'X' WHERE id = :b"),
            {"b": b.tenant_id},
        )
        assert updated.rowcount == 0
        visible = session.execute(text("SELECT id FROM tenants")).scalars().all()
        assert visible == [a.tenant_id]
        session.rollback()
    row = owner_db.execute(
        text("SELECT trade_name FROM tenants WHERE id = :b"), {"b": b.tenant_id}
    ).scalar_one()
    assert row is None


def test_reference_tables_are_read_only_for_the_application(app_engine: Engine) -> None:
    for sql in ("UPDATE geo_countries SET is_active = false", "DELETE FROM geo_countries"):
        with app_engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                conn.execute(text(sql))
            conn.rollback()


# --- Limitation de fréquence -------------------------------------------------------------------


def test_rate_limiter_window_and_hashed_keys(app_engine: Engine, owner_db: Session) -> None:
    owner_db.execute(text("DELETE FROM rate_limit_hits"))
    owner_db.commit()
    now = utcnow()
    window = timedelta(hours=1)
    with create_session_factory(app_engine)() as session:
        limiter = RateLimiter(session, now)
        for _ in range(3):
            limiter.hit("signup", "203.0.113.7", limit=3, window=window)
        with pytest.raises(TooManyRequestsError) as error:
            limiter.hit("signup", "203.0.113.7", limit=3, window=window)
        assert 3500 < error.value.retry_after <= 3601
        # Autre clé, autre compartiment : indépendants.
        limiter.hit("signup", "203.0.113.8", limit=3, window=window)
        limiter.hit("other", "203.0.113.7", limit=3, window=window)
        # Fenêtre écoulée : de nouveau autorisé ; les tentatives anciennes sont purgées.
        RateLimiter(session, now + window + timedelta(seconds=1)).hit(
            "signup", "203.0.113.7", limit=3, window=window
        )
        session.commit()
    keys = owner_db.execute(text("SELECT key_hash FROM rate_limit_hits")).scalars().all()
    assert len(keys) == 3
    assert all(len(k) == 64 and "203.0.113" not in k for k in keys)


def test_rate_limiter_is_safe_across_concurrent_processes(
    app_engine: Engine, owner_db: Session
) -> None:
    owner_db.execute(text("DELETE FROM rate_limit_hits"))
    owner_db.commit()
    results: list[bool] = []
    barrier = threading.Barrier(6)

    def attempt() -> None:
        with create_session_factory(app_engine)() as session:
            barrier.wait()
            try:
                RateLimiter(session, utcnow()).hit(
                    "signup", "198.51.100.1", limit=3, window=timedelta(hours=1)
                )
                session.commit()
                results.append(True)
            except TooManyRequestsError:
                session.rollback()
                results.append(False)

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, False, False, True, True, True]


def test_rate_limit_errors_carry_retry_after() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/limited")
    def limited() -> None:
        raise TooManyRequestsError("Trop de tentatives.", retry_after=42)

    response = TestClient(app).get("/limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert response.json()["code"] == "rate_limited"
    assert response.json()["retry_after"] == 42
