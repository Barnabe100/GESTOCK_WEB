"""R6.5 (ADR-0051) — moyens de réception internationaux.

C : opérateur (catalogue OU personnalisé OU aucun), pays et devise des moyens ; pays et devise
par défaut de l'entreprise (référentiel des pays) ; règle de devise contrôlée par le serveur
(un moyen n'encaisse une vente que dans SA devise, aucune conversion) pour les ventes, la
caisse (POS) et les paiements ultérieurs ; migration 0052.
"""

import dataclasses
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.platform.catalog.loader import IdentifierRuleDef, PaymentProviderDef, load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.registry import get_registry
from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World
from tests.test_encaissement import _draft, _methods, _validate, priced  # noqa: F401
from tests.test_restaurant_menu_migration import _alembic

METHODS = "/payment-methods"


def _code(response: Any) -> tuple[int, str | None]:
    return response.status_code, response.json().get("code")


def _create(api: Api, **body: Any) -> Any:
    return api.post(METHODS, json={"label": "Moyen", "kind": "MOBILE_MONEY", **body})


def _demo_provider(**over: Any) -> PaymentProviderDef:
    base: dict[str, Any] = {
        "code": "demo-mobile",
        "name": "Démo Mobile",
        "names": {},
        "kinds": ("MOBILE_MONEY",),
        "countries": ("BF",),
        "currencies": ("XOF",),
        "identifiers": {
            "PHONE": IdentifierRuleDef(),
            "MERCHANT_CODE": IdentifierRuleDef(min_length=4, max_length=8, pattern="^[0-9]+$"),
        },
        "instructions": {},
        "is_active": True,
    }
    return PaymentProviderDef(**{**base, **over})


@pytest.fixture
def providers(owner_db: Session) -> Iterator[None]:
    """Catalogue de démonstration le temps du test (catalogue réel : vide), puis rétabli."""
    real = load_catalog(get_registry())
    demo = {
        "demo-mobile": _demo_provider(),
        "demo-old": _demo_provider(code="demo-old", name="Ancien", is_active=False),
        "demo-bank": _demo_provider(
            code="demo-bank",
            name="Banque Démo",
            kinds=("BANK_TRANSFER",),
            countries=(),
            currencies=("EUR", "XOF"),
            identifiers={"ACCOUNT_NUMBER": IdentifierRuleDef()},
        ),
    }
    sync_catalog(owner_db, dataclasses.replace(real, payment_providers=demo))
    owner_db.commit()
    try:
        yield
    finally:
        owner_db.rollback()
        owner_db.execute(text("UPDATE payment_methods SET provider_code = NULL"))
        owner_db.execute(text("DELETE FROM ref_payment_providers WHERE code LIKE 'demo-%'"))
        owner_db.commit()
        sync_catalog(owner_db, real)
        owner_db.commit()


# --- C : pays et devise par défaut ---------------------------------------------------------------


def test_defaults_come_from_the_company_country(provision: Any, api_for: Any) -> None:
    provision("rm-bf")
    provision("rm-fr", country="FR")
    bf = _methods(api_for("owner@rm-bf.example.com"))
    fr = _methods(api_for("owner@rm-fr.example.com"))
    assert {(m["country_code"], m["currency"]) for m in bf.values()} == {("BF", "XOF")}
    assert {(m["country_code"], m["currency"]) for m in fr.values()} == {("FR", "EUR")}
    # Aucun opérateur deviné : les moyens par défaut n'en ont pas.
    assert {m["provider_label"] for m in fr.values()} == {None}
    created = _create(api_for("owner@rm-fr.example.com"), label="Virement SEPA")
    assert created.status_code == 201, created.text
    assert (created.json()["country_code"], created.json()["currency"]) == ("FR", "EUR")


# --- C : opérateur personnalisé ou du catalogue -------------------------------------------------


def test_custom_provider_never_needs_the_catalog(world: World) -> None:
    created = _create(world.owner, label="Mobile maison", provider_name="  Opérateur Local  ")
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["provider_code"], body["provider_name"], body["provider_label"]) == (
        None,
        "Opérateur Local",
        "Opérateur Local",
    )
    # International : ``null`` explicite ; devise d'un autre pays acceptée à la configuration.
    intl = _create(world.owner, label="Carte EUR", kind="CARD", country_code=None, currency="EUR")
    assert intl.status_code == 201, intl.text
    assert (intl.json()["country_code"], intl.json()["currency"]) == (None, "EUR")
    both = _create(world.owner, label="Ambigu", provider_code="demo-x", provider_name="Y")
    assert both.status_code == 422
    unknown_country = _create(world.owner, label="Pays ZZ", country_code="ZZ")
    assert _code(unknown_country) == (422, "country_unknown")
    unknown_currency = _create(world.owner, label="Devise ZZZ", currency="ZZZ")
    assert _code(unknown_currency) == (422, "currency_unknown")
    cash = _create(world.owner, label="Caisse 2", kind="CASH", provider_name="Opérateur")
    assert _code(cash) == (422, "payment_provider_not_allowed")


def test_catalog_provider_must_cover_kind_currency_and_country(
    world: World, providers: None
) -> None:
    ok = _create(world.owner, label="Démo", provider_code="demo-mobile")
    assert ok.status_code == 201, ok.text
    assert ok.json()["provider_label"] == "Démo Mobile"
    assert _code(_create(world.owner, label="Inconnu", provider_code="demo-none")) == (
        422,
        "payment_provider_unknown",
    )
    assert _code(_create(world.owner, label="Désactivé", provider_code="demo-old")) == (
        422,
        "payment_provider_unknown",
    )
    for body, field in (
        ({"kind": "CARD"}, "kind"),
        ({"currency": "EUR"}, "currency"),
        ({"country_code": "CI"}, "country"),
    ):
        refused = _create(world.owner, label=f"Refus {field}", provider_code="demo-mobile", **body)
        assert _code(refused) == (422, "payment_provider_mismatch")
        assert refused.json()["field"] == field
    # Opérateur international : tout pays ; devise couverte.
    bank = _create(
        world.owner,
        label="Virement Démo",
        kind="BANK_TRANSFER",
        provider_code="demo-bank",
        country_code="CI",
    )
    assert bank.status_code == 201, bank.text


def test_update_switches_or_removes_the_provider_and_is_audited(
    world: World, providers: None
) -> None:
    method = _create(world.owner, label="Mobile", provider_name="Local").json()
    url = f"{METHODS}/{method['id']}"
    switched = world.owner.patch(url, json={"provider_code": "demo-mobile"})
    assert switched.status_code == 200, switched.text
    assert (switched.json()["provider_code"], switched.json()["provider_name"]) == (
        "demo-mobile",
        None,
    )
    back = world.owner.patch(url, json={"provider_name": "Autre"}).json()
    assert (back["provider_code"], back["provider_name"]) == (None, "Autre")
    cleared = world.owner.patch(url, json={"provider_name": None, "country_code": None}).json()
    assert (cleared["provider_label"], cleared["country_code"]) == (None, None)
    # La devise ne peut pas être vidée ; ``null`` est ignoré.
    assert world.owner.patch(url, json={"currency": None}).json()["currency"] == "XOF"
    # Un opérateur désactivé ensuite ne bloque pas la modification du libellé.
    world.owner.patch(url, json={"provider_code": "demo-mobile"})
    items = world.owner.get("/audit-logs", params={"limit": 50}).json()["items"]
    updates = [i for i in items if i["action"] == "payment_method.updated"]
    assert updates
    assert {"provider_code", "provider_name", "country_code", "currency"} <= set(
        updates[0]["data"]["after"]
    )


# --- C : règle de devise -----------------------------------------------------------------------


def test_a_method_in_another_currency_never_cashes_a_sale(priced: World) -> None:  # noqa: F811
    w = priced
    euro = _create(w.owner, label="Carte EUR", kind="CARD", country_code=None, currency="EUR")
    assert euro.status_code == 201, euro.text
    euro_id = euro.json()["id"]
    listed = {m["label"]: m for m in w.owner.get(METHODS, params={"site_id": w.site}).json()}
    assert (listed["Carte EUR"]["available"], listed["Carte EUR"]["unavailable_reason"]) == (
        False,
        "currency_mismatch",
    )
    assert (
        listed["Carte bancaire"]["available"],
        listed["Carte bancaire"]["unavailable_reason"],
    ) == (
        True,
        None,
    )
    # Validation avec paiement immédiat : refus serveur, rien n'est enregistré.
    sale = _draft(w, 1)
    refused = _validate(w, sale, [{"payment_method_id": euro_id, "amount": "10000"}])
    assert _code(refused) == (422, "payment_method_currency_mismatch")
    assert refused.json()["currency"] == "EUR" and refused.json()["sale_currency"] == "XOF"
    assert w.owner.get(f"/sales/{sale['id']}").json()["status"] == "DRAFT"
    # Point de vente : même règle (même service).
    checkout = w.owner.post(
        "/pos/checkout",
        json={
            "site_id": w.site,
            "lines": [{"article_id": w.articles[0], "quantity": "1"}],
            "payments": [{"payment_method_id": euro_id, "amount": "10000"}],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert _code(checkout) == (422, "payment_method_currency_mismatch")
    # Compatibilité par type : un moyen d'une autre devise n'est jamais choisi d'office.
    w.owner.patch(
        f"{METHODS}/{_methods(w.owner)['Carte bancaire']['id']}", json={"is_active": False}
    )
    by_kind = _validate(w, _draft(w, 1), [{"method": "CARD", "amount": "10000"}])
    assert _code(by_kind) == (422, "payment_method_unavailable")


def test_payment_of_a_validated_sale_checks_the_currency(priced: World) -> None:  # noqa: F811
    w = priced
    sale = _draft(w, 1, customer_id=sh.credit_customer(w))
    cash = _methods(w.owner)["Espèces"]["id"]
    done = _validate(w, sale, [{"payment_method_id": cash, "amount_received": "4000"}])
    assert done.status_code == 200, done.text
    euro = _create(w.owner, label="Virement EUR", kind="BANK_TRANSFER", currency="EUR").json()
    late = w.owner.post(
        f"/sales/{sale['id']}/payments",
        json={"payment_method_id": euro["id"], "amount": "6000"},
    )
    assert _code(late) == (422, "payment_method_currency_mismatch")
    transfer = _methods(w.owner)["Virement"]["id"]
    paid = w.owner.post(
        f"/sales/{sale['id']}/payments",
        json={"payment_method_id": transfer, "amount": "6000"},
    )
    assert paid.status_code == 201, paid.text


# --- C : migration 0052 ------------------------------------------------------------------------


def test_migration_0052_backfills_and_refuses_to_lose_data(
    provision: Any, api_for: Any, owner_db: Session, owner_engine: Engine
) -> None:
    provision("rm-mig-fr", country="FR")
    owner = api_for("owner@rm-mig-fr.example.com")
    owner_db.close()
    owner_engine.dispose()
    try:
        command.downgrade(_alembic(), "0051")
        owner_engine.dispose()
        command.upgrade(_alembic(), "0052")
        # Reprise : pays et devise de l'entreprise (comportement inchangé).
        assert {(m["country_code"], m["currency"]) for m in _methods(owner).values()} == {
            ("FR", "EUR")
        }
        _create(owner, label="Perso", provider_name="Local")
        owner_engine.dispose()
        with pytest.raises(RuntimeError, match="perdraient leur opérateur"):
            command.downgrade(_alembic(), "0051")
    finally:
        owner_engine.dispose()
        command.upgrade(_alembic(), "head")
