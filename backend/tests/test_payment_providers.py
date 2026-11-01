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
