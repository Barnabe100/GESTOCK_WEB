"""R6.5-B (ADR-0051) — catalogue des opérateurs et établissements financiers.

Données de référence gérées par TechNova (``payment_providers.toml``), validées au chargement,
synchronisées en base (``ref_payment_providers``, lecture seule pour l'application), VIDES au
départ, jamais requises ; API de lecture filtrée par pays, devise et type de moyen.
"""

import dataclasses
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.sales.models import PaymentMethod
from app.platform.catalog.loader import (
    DATA_DIR,
    CatalogError,
    IdentifierRuleDef,
    PaymentProviderDef,
    load_catalog,
    load_payment_providers,
    payment_provider_errors,
)
from app.platform.catalog.sync import sync_catalog
from app.platform.registry import get_registry
from app.shared.receiving import RECEIVING_KINDS
from tests.conftest import PASSWORD, Api, login

PROVIDERS = "/payment-providers"


def _provider(**over: Any) -> PaymentProviderDef:
    base: dict[str, Any] = {
        "code": "demo-mobile",
        "name": "Démo Mobile",
        "names": {"en": "Demo Mobile"},
        "kinds": ("MOBILE_MONEY",),
        "countries": ("BF",),
        "currencies": ("XOF",),
        "identifiers": {
            "PHONE": IdentifierRuleDef(),
            "MERCHANT_CODE": IdentifierRuleDef(min_length=4, max_length=8, pattern="^[0-9]+$"),
        },
        "instructions": {"fr": "Composez le code marchand."},
        "is_active": True,
    }
    return PaymentProviderDef(**{**base, **over})


def test_the_initial_catalog_is_empty_and_the_kinds_match_the_sales_types() -> None:
    catalog = load_catalog(get_registry())
    assert catalog.payment_providers == {}
    # Vocabulaire technique partagé : identique aux types de moyens des ventes.
    assert tuple(k.value for k in PaymentMethod) == RECEIVING_KINDS


def test_a_valid_entry_has_no_error_and_is_parsed_from_toml() -> None:
    assert payment_provider_errors(_provider(), {"BF"}) == []
    parsed = load_payment_providers(
        {
            "providers": {
                "demo-bank": {
                    "name": "Banque Démo",
                    "kinds": ["BANK_TRANSFER"],
                    "currencies": ["EUR", "XOF"],
                    "identifiers": {"ACCOUNT_NUMBER": {"max_length": 34}},
                }
            }
        }
    )
    bank = parsed["demo-bank"]
    # International (aucun pays), règle facultative conservée, actif par défaut.
    assert (bank.countries, bank.is_active) == ((), True)
    assert bank.identifiers["ACCOUNT_NUMBER"].max_length == 34
    assert payment_provider_errors(bank, {"BF"}) == []


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"code": "Bad Code"}, "code invalide"),
        ({"name": " "}, "nom manquant"),
        ({"names": {"EN": "x"}}, "langue invalide"),
        ({"kinds": ()}, "au moins un type"),
        ({"kinds": ("CHEQUE",)}, "type de moyen inconnu"),
        ({"countries": ("BFA",)}, "pays ISO 3166-1 invalide"),
        ({"countries": ("ZZ",)}, "pays absent du référentiel"),
        ({"currencies": ()}, "au moins une devise"),
        ({"currencies": ("xof",)}, "devise ISO 4217 invalide"),
        ({"identifiers": {"IBAN": IdentifierRuleDef()}}, "type d'identifiant inconnu"),
        ({"identifiers": {"PHONE": IdentifierRuleDef(min_length=0)}}, "entier de 1 à 100"),
        (
            {"identifiers": {"PHONE": IdentifierRuleDef(min_length=9, max_length=4)}},
            "min_length > max_length",
        ),
        ({"identifiers": {"PHONE": IdentifierRuleDef(pattern="[0-9")}}, "pas une expression"),
    ],
)
def test_invalid_entries_are_rejected(over: dict[str, Any], fragment: str) -> None:
    errors = payment_provider_errors(_provider(**over), {"BF"})
    assert any(fragment in e for e in errors), errors


def test_an_invalid_file_stops_the_catalog_loading(tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(DATA_DIR, data)
    (data / "payment_providers.toml").write_text(
        '[providers.demo]\nname = "Démo"\nkinds = ["MOBILE_MONEY"]\ncurrencies = ["ZZZZ"]\n',
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="devise ISO 4217 invalide"):
        load_catalog(get_registry(), data)


@pytest.fixture
def synced(owner_db: Session) -> Iterator[None]:
    """Catalogue de démonstration synchronisé le temps du test, puis catalogue réel rétabli
    (les opérateurs de démonstration y sont désactivés, puis supprimés)."""
    real = load_catalog(get_registry())
    demo = {
        "demo-mobile": _provider(),
        "demo-intl": _provider(
            code="demo-intl",
            name="Carte Internationale",
            names={},
            kinds=("CARD",),
            countries=(),
            currencies=("EUR", "XOF", "USD"),
            identifiers={},
            instructions={},
        ),
        "demo-off": _provider(code="demo-off", name="Ancien opérateur", is_active=False),
        "demo-ci": _provider(code="demo-ci", name="Opérateur CI", countries=("CI",)),
    }
    sync_catalog(owner_db, dataclasses.replace(real, payment_providers=demo))
    owner_db.commit()
    try:
        yield
    finally:
        report = sync_catalog(owner_db, real)
        owner_db.commit()
        assert sorted(report.deactivated_payment_providers) == sorted(demo)
        owner_db.execute(text("DELETE FROM ref_payment_providers WHERE code LIKE 'demo-%'"))
        owner_db.commit()


def test_sync_inserts_updates_and_deactivates(synced: None, owner_db: Session) -> None:
    rows = dict(owner_db.execute(text("SELECT code, is_active FROM ref_payment_providers")).all())
    assert rows == {"demo-mobile": True, "demo-intl": True, "demo-off": False, "demo-ci": True}
    rule = owner_db.execute(
        text("SELECT identifiers FROM ref_payment_providers WHERE code = 'demo-mobile'")
    ).scalar_one()
    # Règle facultative : seules les bornes déclarées sont enregistrées.
    assert rule == {
        "PHONE": {},
        "MERCHANT_CODE": {"min_length": 4, "max_length": 8, "pattern": "^[0-9]+$"},
    }


# --- Synchronisation : mise à jour, réactivation, références, idempotence -----------------------
# Opérateurs de DÉMONSTRATION uniquement (fixture ``synced``) : le fichier livré reste vide et le
# catalogue réel est rétabli à la fin de chaque test.

ROW_COLUMNS = (
    "code, name, names, instructions, kinds, countries, currencies, identifiers, is_active, "
    "created_at"
)


def _sync_providers(owner_db: Session, providers: dict[str, PaymentProviderDef]) -> Any:
    """Synchronise le catalogue réel dont seuls les opérateurs sont remplacés."""
    real = load_catalog(get_registry())
    report = sync_catalog(owner_db, dataclasses.replace(real, payment_providers=providers))
    owner_db.commit()
    return report


def _demo_rows(owner_db: Session) -> dict[str, Any]:
    rows = owner_db.execute(
        text(f"SELECT {ROW_COLUMNS} FROM ref_payment_providers WHERE code LIKE 'demo-%'")
    ).mappings()
    return {row["code"]: dict(row) for row in rows}


def _demo_catalog() -> dict[str, PaymentProviderDef]:
    """Même contenu que la fixture ``synced``."""
    return {
        "demo-mobile": _provider(),
        "demo-intl": _provider(
            code="demo-intl",
            name="Carte Internationale",
            names={},
            kinds=("CARD",),
            countries=(),
            currencies=("EUR", "XOF", "USD"),
            identifiers={},
            instructions={},
        ),
        "demo-off": _provider(code="demo-off", name="Ancien opérateur", is_active=False),
        "demo-ci": _provider(code="demo-ci", name="Opérateur CI", countries=("CI",)),
    }


def test_sync_updates_an_existing_provider_in_place(synced: None, owner_db: Session) -> None:
    before = _demo_rows(owner_db)["demo-mobile"]
    changed = _provider(
        name="Démo Mobile Plus",
        names={"en": "Demo Mobile Plus"},
        kinds=("MOBILE_MONEY", "OTHER"),
        countries=("BF", "CI"),
        currencies=("XOF", "EUR"),
        identifiers={"AGENT_CODE": IdentifierRuleDef(min_length=6, max_length=6)},
        instructions={"fr": "Donnez le code agent.", "en": "Give the agent code."},
    )
    report = _sync_providers(owner_db, {**_demo_catalog(), "demo-mobile": changed})
    assert report.deactivated_payment_providers == []
    rows = _demo_rows(owner_db)
    assert set(rows) == {"demo-mobile", "demo-intl", "demo-off", "demo-ci"}  # aucune ligne en plus
    after = rows["demo-mobile"]
    # Même ligne (clé et création inchangées), tous les champs du fichier remplacés.
    assert after["created_at"] == before["created_at"]
    assert after == {
        **before,
        "name": "Démo Mobile Plus",
        "names": {"en": "Demo Mobile Plus"},
        "kinds": ["MOBILE_MONEY", "OTHER"],
        "countries": ["BF", "CI"],
        "currencies": ["XOF", "EUR"],
        # Règle remplacée entièrement : les anciens types d'identifiants disparaissent.
        "identifiers": {"AGENT_CODE": {"min_length": 6, "max_length": 6}},
        "instructions": {"fr": "Donnez le code agent.", "en": "Give the agent code."},
    }


def test_sync_reactivates_a_provider_put_back_in_the_file(synced: None, owner_db: Session) -> None:
    created_at = _demo_rows(owner_db)["demo-ci"]["created_at"]
    without_ci = {code: p for code, p in _demo_catalog().items() if code != "demo-ci"}
    report = _sync_providers(owner_db, without_ci)
    assert report.deactivated_payment_providers == ["demo-ci"]
    assert _demo_rows(owner_db)["demo-ci"]["is_active"] is False  # désactivé, jamais supprimé

    report = _sync_providers(owner_db, _demo_catalog())
    assert report.deactivated_payment_providers == []
    row = _demo_rows(owner_db)["demo-ci"]
    # Remis dans le fichier : la MÊME ligne redevient active.
    assert (row["is_active"], row["created_at"]) == (True, created_at)


def test_a_deactivated_provider_stays_referenced_by_its_payment_methods(
    synced: None, owner_db: Session, provision: Any, api_for: Any
) -> None:
    provision("pp-ref")
    owner = api_for("owner@pp-ref.example.com")
    created = owner.post(
        "/payment-methods",
        json={"label": "Démo référencé", "kind": "MOBILE_MONEY", "provider_code": "demo-mobile"},
    )
    assert created.status_code == 201, created.text
    method_id = created.json()["id"]
    try:
        without_mobile = {c: p for c, p in _demo_catalog().items() if c != "demo-mobile"}
        report = _sync_providers(owner_db, without_mobile)
        assert report.deactivated_payment_providers == ["demo-mobile"]
        # Ligne conservée (la clé étrangère du moyen la référence), désactivée.
        assert _demo_rows(owner_db)["demo-mobile"]["is_active"] is False
        method = next(m for m in owner.get("/payment-methods").json() if m["id"] == method_id)
        assert (method["provider_code"], method["provider_label"]) == (
            "demo-mobile",
            "Démo Mobile",
        )
        # Plus proposé dans le catalogue consultable.
        assert "demo-mobile" not in [p["code"] for p in owner.get(PROVIDERS).json()]
    finally:
        # Préparation hors API, comme les tests des moyens de réception : la fixture supprime
        # ensuite les opérateurs de démonstration.
        owner_db.execute(
            text("UPDATE payment_methods SET provider_code = NULL WHERE id = :id"),
            {"id": method_id},
        )
        owner_db.commit()


def test_sync_is_idempotent(synced: None, owner_db: Session) -> None:
    before = _demo_rows(owner_db)
    for _ in range(2):
        report = _sync_providers(owner_db, _demo_catalog())
        assert report.payment_providers == 4
        assert report.deactivated_payment_providers == []
        assert _demo_rows(owner_db) == before


def _member(owner: Api, client: TestClient, email: str, template: str) -> Api:
    roles = {r["template_code"]: r["id"] for r in owner.get("/roles").json()}
    created = owner.post(
        "/members",
        json={
            "email": email,
            "full_name": email,
            "password": "Provisoire-123",
            "roles": [{"role_id": roles[template]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    token = login(client, email, "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    return Api(client, login(client, email).json()["access_token"])


def test_api_filters_by_country_currency_and_kind(
    synced: None, provision: Any, api_for: Any
) -> None:
    provision("pp-api")
    owner = api_for("owner@pp-api.example.com")
    all_active = owner.get(PROVIDERS).json()
    # Actifs seulement, triés par nom ; libellé et instructions dans la langue de l'entreprise.
    assert [p["code"] for p in all_active] == ["demo-intl", "demo-mobile", "demo-ci"]
    mobile = next(p for p in all_active if p["code"] == "demo-mobile")
    assert mobile["name"] == "Démo Mobile"
    assert mobile["instructions"] == "Composez le code marchand."
    assert mobile["identifiers"] == [
        {"type": "PHONE", "min_length": None, "max_length": None, "pattern": None},
        {"type": "MERCHANT_CODE", "min_length": 4, "max_length": 8, "pattern": "^[0-9]+$"},
    ]
    # Pays : opérateurs du pays et internationaux ; devise ; type.
    assert [p["code"] for p in owner.get(f"{PROVIDERS}?country=BF").json()] == [
        "demo-intl",
        "demo-mobile",
    ]
    assert [p["code"] for p in owner.get(f"{PROVIDERS}?currency=EUR").json()] == ["demo-intl"]
    assert [p["code"] for p in owner.get(f"{PROVIDERS}?kind=MOBILE_MONEY").json()] == [
        "demo-mobile",
        "demo-ci",
    ]
    assert owner.get(f"{PROVIDERS}?country=bf").status_code == 422
    assert owner.get(f"{PROVIDERS}?kind=CHEQUE").status_code == 422


def test_api_requires_a_payment_permission(
    synced: None, provision: Any, api_for: Any, client: TestClient
) -> None:
    provision("pp-perm")
    owner = api_for("owner@pp-perm.example.com")
    # Consultant : ``*.view`` comprend la consultation des paiements.
    viewer = _member(owner, client, "consultant@pp-perm.example.com", "viewer")
    assert viewer.get(PROVIDERS).status_code == 200
    roles = owner.get("/roles").json()
    custom = owner.post(
        "/roles",
        json={"name": "Stock seul", "permissions": ["stock.level.view"]},
    )
    assert custom.status_code == 201, custom.text
    created = owner.post(
        "/members",
        json={
            "email": "stock@pp-perm.example.com",
            "full_name": "Stock",
            "password": "Provisoire-123",
            "roles": [{"role_id": custom.json()["id"]}],
            "all_sites": True,
        },
    )
    assert created.status_code == 201, created.text
    assert roles  # rôles de base présents
    token = login(client, "stock@pp-perm.example.com", "Provisoire-123").json()["access_token"]
    Api(client, token).post(
        "/me/password", json={"current_password": "Provisoire-123", "new_password": PASSWORD}
    )
    stock = Api(client, login(client, "stock@pp-perm.example.com").json()["access_token"])
    response = stock.get(PROVIDERS)
    assert (response.status_code, response.json()["code"]) == (403, "permission_denied")


def test_the_application_role_only_reads_the_catalog(db: Session) -> None:
    assert db.execute(text("SELECT count(*) FROM ref_payment_providers")).scalar_one() == 0
    for sql in (
        "INSERT INTO ref_payment_providers (code, name, names, instructions, kinds, countries, "
        "currencies, identifiers, is_active) VALUES ('pirate', 'Pirate', '{}', '{}', "
        "ARRAY['CASH'], ARRAY[]::varchar[], ARRAY['XOF'], '{}', true)",
        "UPDATE ref_payment_providers SET is_active = false",
        "DELETE FROM ref_payment_providers",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(sql))
        db.rollback()
