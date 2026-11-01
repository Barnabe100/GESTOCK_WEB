"""R6.5 (ADR-0051) — moyens de réception internationaux.

D : identifiants de réception propres à un site ou communs, sans limite ; formats contrôlés
seulement par une règle du catalogue ; droits par site (commun : tous les sites) ; lecture
limitée aux sites accessibles ; doublons, concurrence, RLS, droits SQL ; migration 0053.

C : opérateur (catalogue OU personnalisé OU aucun), pays et devise des moyens ; pays et devise
par défaut de l'entreprise (référentiel des pays) ; règle de devise contrôlée par le serveur
(un moyen n'encaisse une vente que dans SA devise, aucune conversion) pour les ventes, la
caisse (POS) et les paiements ultérieurs ; migration 0052.
"""

import dataclasses
import threading
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.platform.catalog.loader import IdentifierRuleDef, PaymentProviderDef, load_catalog
from app.platform.catalog.sync import sync_catalog
from app.platform.registry import get_registry
from tests import stock_helpers as sh
from tests.conftest import Api
from tests.stock_helpers import World
from tests.test_encaissement import (  # noqa: F401
    _custom_member,
    _draft,
    _methods,
    _validate,
    priced,
)
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
    by_kind = _validate(
        w, _draft(w, 1), [{"method": "CARD", "amount": "10000", "received_confirmed": True}]
    )
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
        json={"payment_method_id": transfer, "amount": "6000", "received_confirmed": True},
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


# --- D : identifiants de réception ---------------------------------------------------------------


def _accounts_url(method_id: str) -> str:
    return f"{METHODS}/{method_id}/accounts"


def _account(api: Api, method_id: str, **body: Any) -> Any:
    return api.post(
        _accounts_url(method_id),
        json={"identifier_type": "MERCHANT_CODE", "value": "12345", **body},
    )


def _mobile(w: World) -> str:
    return str(_methods(w.owner)["Mobile Money"]["id"])


def _race(calls: list[Any]) -> list[Any]:
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def run(index: int) -> None:
        barrier.wait()
        results[index] = calls[index]()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def test_site_and_shared_identifiers_without_limit(world: World) -> None:
    w = world
    mobile = _mobile(w)
    shared = _account(w.owner, mobile, site_id=None, value="11111", holder_name="Société")
    assert shared.status_code == 201, shared.text
    assert (shared.json()["shared"], shared.json()["site_id"]) == (True, None)
    # Plusieurs identifiants pour un même moyen et un même site, aucun maximal.
    for value in ("22222", "33333", "44444"):
        assert _account(w.owner, mobile, site_id=w.site, value=value).status_code == 201
    other = _account(w.owner, mobile, site_id=w.site2, value="55555")
    assert other.status_code == 201, other.text
    assert other.json()["site_name"]
    # Liste d'administration : communs + sites accessibles ; filtre par site.
    every = w.owner.get(_accounts_url(mobile)).json()
    assert len(every) == 5
    site_a = [
        a["value"] for a in w.owner.get(_accounts_url(mobile), params={"site_id": w.site}).json()
    ]
    assert site_a == ["11111", "22222", "33333", "44444"]
    # Caisse : les moyens d'un site portent leurs identifiants ACTIFS (communs + du site).
    listed = {m["label"]: m for m in w.owner.get(METHODS, params={"site_id": w.site2}).json()}
    assert [a["value"] for a in listed["Mobile Money"]["accounts"]] == ["11111", "55555"]
    assert listed["Espèces"]["accounts"] == []
    assert w.owner.get(METHODS).json()[0]["accounts"] is None


def test_identifier_rules_types_and_duplicates(world: World) -> None:
    w = world
    mobile = _mobile(w)
    phone = _account(w.owner, mobile, identifier_type="PHONE", value="+226 70-11-22-33")
    assert phone.status_code == 201, phone.text
    # Séparateurs retirés, jamais réécrit avec un indicatif deviné.
    assert phone.json()["value"] == "+22670112233"
    local = _account(w.owner, mobile, identifier_type="PHONE", value="70 11 22 33")
    assert local.json()["value"] == "70112233"
    assert _code(_account(w.owner, mobile, identifier_type="PHONE", value="abc")) == (
        422,
        "payment_account_invalid",
    )
    assert _code(_account(w.owner, mobile, value="12\u000745")) == (422, "payment_account_invalid")
    assert _code(_account(w.owner, mobile, identifier_type="CUSTOM", value="X1")) == (
        422,
        "payment_account_label_required",
    )
    custom = _account(
        w.owner, mobile, identifier_type="CUSTOM", custom_label="Identifiant client", value="A-9"
    )
    assert custom.status_code == 201, custom.text
    cash = _methods(w.owner)["Espèces"]["id"]
    assert _code(_account(w.owner, cash)) == (422, "payment_account_not_allowed")
    # Doublon actif refusé (même moyen, même portée) ; permis après désactivation.
    first = _account(w.owner, mobile, site_id=w.site, value="777").json()
    assert _code(_account(w.owner, mobile, site_id=w.site, value="777")) == (
        409,
        "payment_account_duplicate",
    )
    assert _account(w.owner, mobile, site_id=w.site2, value="777").status_code == 201
    off = w.owner.post(f"{METHODS}/accounts/{first['id']}/deactivate")
    assert off.json()["is_active"] is False
    assert _account(w.owner, mobile, site_id=w.site, value="777").status_code == 201
    assert _code(w.owner.post(f"{METHODS}/accounts/{first['id']}/activate")) == (
        409,
        "payment_account_duplicate",
    )
    # Modification : valeur, titulaire, instructions ; type et portée immuables (non exposés).
    edited = w.owner.patch(
        f"{METHODS}/accounts/{custom.json()['id']}",
        json={"value": "B-1", "holder_name": "Caisse centrale", "instructions": "Indiquer n°"},
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()["value"], edited.json()["holder_name"]) == ("B-1", "Caisse centrale")
    assert (
        w.owner.patch(
            f"{METHODS}/accounts/{custom.json()['id']}", json={"site_id": w.site}
        ).status_code
        == 422
    )
    actions = [
        i["action"]
        for i in w.owner.get("/audit-logs", params={"limit": 100}).json()["items"]
        if i["action"].startswith("payment_method_account.")
    ]
    assert {
        "payment_method_account.created",
        "payment_method_account.updated",
        "payment_method_account.deactivated",
    } <= set(actions)


def test_formats_come_only_from_the_provider_catalog(world: World, providers: None) -> None:
    w = world
    demo = _create(w.owner, label="Démo", provider_code="demo-mobile").json()["id"]
    too_short = _account(w.owner, demo, value="12")
    assert _code(too_short) == (422, "payment_account_invalid")
    assert too_short.json()["reason"] == "provider_rule"
    assert _code(_account(w.owner, demo, value="12AB")) == (422, "payment_account_invalid")
    assert _account(w.owner, demo, value="1234").status_code == 201
    assert _code(_account(w.owner, demo, identifier_type="ACCOUNT_NUMBER", value="FR76")) == (
        422,
        "payment_account_type_not_accepted",
    )
    # Opérateur personnalisé : aucune règle supposée.
    custom = _create(w.owner, label="Perso", provider_name="Local").json()["id"]
    assert _account(w.owner, custom, value="AB").status_code == 201


def test_site_scope_hides_and_protects_other_sites(world: World, client: TestClient) -> None:
    w = world
    mobile = _mobile(w)
    on_a = _account(w.owner, mobile, site_id=w.site, value="AAAA").json()
    shared = _account(w.owner, mobile, site_id=None, value="SHARED").json()
    limited = _custom_member(
        w,
        client,
        "caissier-b@alpha.example.com",
        ["sales.payment_method.manage", "sales.payment.view"],
        all_sites=False,
        site_ids=[w.site2],
    )
    # Lecture : jamais les identifiants d'un site inaccessible.
    values = [a["value"] for a in limited.get(_accounts_url(mobile)).json()]
    assert values == ["SHARED"]
    assert limited.get(_accounts_url(mobile), params={"site_id": w.site}).status_code == 404
    assert limited.get(METHODS, params={"site_id": w.site}).status_code == 404
    assert limited.patch(f"{METHODS}/accounts/{on_a['id']}", json={"value": "X"}).status_code == 404
    assert limited.post(f"{METHODS}/accounts/{on_a['id']}/deactivate").status_code == 404
    # Écriture : son site seulement ; un identifiant commun exige tous les sites.
    assert _account(limited, mobile, site_id=w.site2, value="BBBB").status_code == 201
    assert _account(limited, mobile, site_id=w.site, value="CCCC").status_code == 404
    common = _account(limited, mobile, site_id=None, value="DDDD")
    assert _code(common) == (403, "permission_denied")
    assert common.json()["scope"] == "all_sites"
    assert _code(limited.patch(f"{METHODS}/accounts/{shared['id']}", json={"value": "Z"})) == (
        403,
        "permission_denied",
    )
    # Sans la permission de gérer : lecture seule.
    reader = _custom_member(w, client, "lecteur@alpha.example.com", ["sales.payment.view"])
    assert reader.get(_accounts_url(mobile)).status_code == 200
    assert _code(_account(reader, mobile, site_id=w.site)) == (403, "permission_denied")


def test_concurrent_identical_identifiers_create_one(world: World) -> None:
    w = world
    mobile = _mobile(w)
    results = _race([lambda: _account(w.owner, mobile, site_id=w.site, value="RACE")] * 3)
    assert sorted(r.status_code for r in results) == [201, 409, 409]


def test_identifiers_rls_composite_keys_and_privileges(
    world: World, provision: Any, db: Session
) -> None:
    w = world
    mobile = _mobile(w)
    account = _account(w.owner, mobile, site_id=w.site).json()
    other = provision("rm-other")
    tenant = db.execute(text("SELECT tenant_id FROM sites WHERE id = :s"), {"s": w.site}).scalar()
    assert tenant is None  # sans contexte : rien n'est visible (RLS)
    set_db_context(db, tenant_id=other.tenant_id)
    assert db.execute(text("SELECT count(*) FROM payment_method_accounts")).scalar_one() == 0
    db.rollback()
    owner_tenant = uuid.UUID(w.owner.get("/me/capabilities").json()["tenant"]["id"])
    set_db_context(db, tenant_id=owner_tenant)
    for sql in (
        "DELETE FROM payment_method_accounts",
        "UPDATE payment_method_accounts SET site_id = NULL",
        "UPDATE payment_method_accounts SET payment_method_id = payment_method_id",
        "UPDATE payment_method_accounts SET identifier_type = 'PHONE'",
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(sql))
        db.rollback()
        set_db_context(db, tenant_id=owner_tenant)
    # FK composite : jamais le site d'une autre entreprise.
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO payment_method_accounts (id, tenant_id, payment_method_id, site_id, "
                "identifier_type, value, sort_order, is_active) VALUES (gen_random_uuid(), :t, "
                ":m, :s, 'PHONE', '999', 0, true)"
            ),
            {"t": owner_tenant, "m": mobile, "s": other.site_id},
        )
    db.rollback()
    assert account["id"]


def test_migration_0053_downgrade_refuses_to_lose_identifiers(
    world: World, owner_db: Session, owner_engine: Engine
) -> None:
    _account(world.owner, _mobile(world), site_id=world.site)
    owner_db.close()
    owner_engine.dispose()
    try:
        with pytest.raises(RuntimeError, match="identifiant\\(s\\) de réception seraient perdus"):
            command.downgrade(_alembic(), "0052")
    finally:
        owner_engine.dispose()
        command.upgrade(_alembic(), "head")


def test_identifier_types_match_the_shared_vocabulary() -> None:
    from app.modules.sales.models import ReceivingIdentifierType
    from app.shared.receiving import IDENTIFIER_TYPES

    assert tuple(t.value for t in ReceivingIdentifierType) == IDENTIFIER_TYPES


# --- E : confirmation manuelle et instantané -----------------------------------------------------


def _pay(w: World, sale: dict[str, Any], **payment: Any) -> Any:
    return w.owner.post(f"/sales/{sale['id']}/payments", json=payment)


def _credit_sale(w: World) -> dict[str, Any]:
    """Vente validée à crédit (10 000 dus) : paiements ultérieurs possibles."""
    sale = _draft(w, 1, customer_id=sh.credit_customer(w))
    done = _validate(w, sale, [])
    assert done.status_code == 200, done.text
    return dict(done.json())


def test_selecting_a_method_never_confirms_a_payment(priced: World) -> None:  # noqa: F811
    w = priced
    mobile = _mobile(w)
    sale = _credit_sale(w)
    refused = _pay(w, sale, payment_method_id=mobile, amount="4000")
    assert _code(refused) == (422, "payment_confirmation_required")
    assert w.owner.get(f"/sales/{sale['id']}/payments").json()["items"] == []
    # Validation avec paiement immédiat et POS : même règle, rien n'est enregistré.
    draft = _draft(w, 1)
    assert _code(_validate(w, draft, [{"payment_method_id": mobile, "amount": "10000"}])) == (
        422,
        "payment_confirmation_required",
    )
    assert w.owner.get(f"/sales/{draft['id']}").json()["status"] == "DRAFT"
    checkout = w.owner.post(
        "/pos/checkout",
        json={
            "site_id": w.site,
            "lines": [{"article_id": w.articles[0], "quantity": "1"}],
            "payments": [{"payment_method_id": mobile, "amount": "10000"}],
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert _code(checkout) == (422, "payment_confirmation_required")
    # Confirmation explicite : auteur et horodatage conservés ; référence facultative.
    ok = _pay(w, sale, payment_method_id=mobile, amount="4000", received_confirmed=True)
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["status"] == "COMPLETED" and body["reference"] is None
    assert body["confirmed_at"] and body["confirmed_by_name"]
    assert body["currency"] == "XOF"
    # Espèces : remises en main propre, l'enregistrement vaut confirmation (auteur, heure).
    cash = _methods(w.owner)["Espèces"]["id"]
    paid = _pay(w, sale, payment_method_id=cash, amount_received="6000")
    assert paid.status_code == 201, paid.text
    assert paid.json()["confirmed_by_name"] == body["confirmed_by_name"]


def test_receipt_keeps_the_identifier_actually_used(priced: World, providers: None) -> None:  # noqa: F811
    w = priced
    demo = _create(w.owner, label="Démo Pay", provider_code="demo-mobile").json()["id"]
    account = _account(
        w.owner, demo, site_id=w.site, value="4321", holder_name="Boutique Centre"
    ).json()
    sale = _draft(w, 1)
    validated = _validate(
        w,
        sale,
        [
            {
                "payment_method_id": demo,
                "amount": "10000",
                "payment_method_account_id": account["id"],
                "received_confirmed": True,
            }
        ],
    )
    assert validated.status_code == 200, validated.text
    # La configuration change ensuite : l'historique et le reçu gardent l'instantané.
    w.owner.patch(f"{METHODS}/accounts/{account['id']}", json={"value": "9999"})
    w.owner.post(f"{METHODS}/accounts/{account['id']}/deactivate")
    w.owner.patch(f"{METHODS}/{demo}", json={"provider_name": "Autre opérateur"})
    payment = w.owner.get(f"/sales/{sale['id']}/payments").json()["items"][0]
    assert (
        payment["provider_label"],
        payment["account_identifier_type"],
        payment["account_value"],
        payment["account_holder"],
        payment["payment_method_account_id"],
    ) == ("Démo Mobile", "MERCHANT_CODE", "4321", "Boutique Centre", account["id"])
    receipt = w.owner.get(f"/sales/{sale['id']}/receipt").json()
    line = receipt["payments"][0]
    assert (line["method_label"], line["provider_label"], line["account_value"]) == (
        "Démo Pay",
        "Démo Mobile",
        "4321",
    )
    assert (line["account_identifier_type"], line["account_holder"]) == (
        "MERCHANT_CODE",
        "Boutique Centre",
    )


def test_identifier_must_belong_to_the_method_and_the_site(priced: World) -> None:  # noqa: F811
    w = priced
    mobile = _mobile(w)
    transfer = _methods(w.owner)["Virement"]["id"]
    on_site2 = _account(w.owner, mobile, site_id=w.site2, value="S2").json()
    of_transfer = _account(
        w.owner, transfer, identifier_type="ACCOUNT_NUMBER", value="ACC-1"
    ).json()
    inactive = _account(w.owner, mobile, value="OFF").json()
    w.owner.post(f"{METHODS}/accounts/{inactive['id']}/deactivate")
    only = _account(w.owner, mobile, site_id=w.site, value="ONLY").json()
    sale = _credit_sale(w)
    for account_id in (on_site2["id"], of_transfer["id"], inactive["id"], str(uuid.uuid4())):
        refused = _pay(
            w,
            sale,
            payment_method_id=mobile,
            amount="1000",
            received_confirmed=True,
            payment_method_account_id=account_id,
        )
        assert _code(refused) == (422, "payment_account_unavailable")
    cash = _methods(w.owner)["Espèces"]["id"]
    assert _code(
        _pay(w, sale, payment_method_id=cash, amount="1000", payment_method_account_id=only["id"])
    ) == (422, "payment_account_not_allowed")
    # Aucun identifiant envoyé : rien n'est deviné, même avec un seul identifiant actif.
    plain = _pay(w, sale, payment_method_id=mobile, amount="1000", received_confirmed=True)
    assert plain.status_code == 201, plain.text
    assert (plain.json()["payment_method_account_id"], plain.json()["account_value"]) == (
        None,
        None,
    )
    used = _pay(
        w,
        sale,
        payment_method_id=mobile,
        amount="1000",
        received_confirmed=True,
        payment_method_account_id=only["id"],
    )
    assert used.json()["account_value"] == "ONLY"


def test_payments_stay_immutable_and_confirmed_in_sql(
    priced: World,  # noqa: F811
    db: Session,
) -> None:
    w = priced
    sale = _credit_sale(w)
    _pay(w, sale, payment_method_id=_mobile(w), amount="1000", received_confirmed=True)
    tenant = uuid.UUID(w.owner.get("/me/capabilities").json()["tenant"]["id"])
    set_db_context(db, tenant_id=tenant)
    for column, value in (
        ("confirmed_by", "NULL"),
        ("account_value", "'X'"),
        ("currency", "'EUR'"),
        ("provider_label", "'X'"),
    ):
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text(f"UPDATE payments SET {column} = {value}"))
        db.rollback()
        set_db_context(db, tenant_id=tenant)


def test_migration_0054_downgrade_refuses_to_lose_snapshots(
    priced: World,  # noqa: F811
    owner_db: Session,
    owner_engine: Engine,
) -> None:
    w = priced
    mobile = _mobile(w)
    account = _account(w.owner, mobile, site_id=w.site, value="SNAP").json()
    sale = _credit_sale(w)
    _pay(
        w,
        sale,
        payment_method_id=mobile,
        amount="1000",
        received_confirmed=True,
        payment_method_account_id=account["id"],
    )
    owner_db.close()
    owner_engine.dispose()
    try:
        with pytest.raises(RuntimeError, match="coordonnées de réception"):
            command.downgrade(_alembic(), "0053")
    finally:
        owner_engine.dispose()
        command.upgrade(_alembic(), "head")
