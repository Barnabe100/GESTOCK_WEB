"""Phase 3.3-B4 — Renouvellement par site (ADR-0036).

R1 : postes reconduits depuis la licence de référence, changement seulement explicite.
R2 : continuité pendant la période de grâce ; au-delà, départ le jour même.
R3 : période et montant calculés par le serveur (tarif figé : plan + postes).
R4 : offre en vigueur (licence) ≠ offre au prochain renouvellement.
Postes maintenus au renouvellement ; révocation sans repli automatique.
"""

# Les fixtures partagées sont importées de test_licenses (paramètres homonymes attendus).
# ruff: noqa: F811
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.subscriptions.service import NO_TARIFF, Tariff
from tests.conftest import CONSOLE_HEADERS, CONSOLE_PREFIX, Api, _sites_engine
from tests.test_console import _reset_plans
from tests.test_licenses import (  # noqa: F401  (fixtures)
    _confirmed_payment,
    _cpost,
    _generate,
    _subscription,
    _today,
    admin,
    alpha,
    owner,
    signing,
)


@pytest.fixture(autouse=True)
def neutral_plans(owner_db: Session) -> Iterator[None]:
    """Paramètres commerciaux neutres avant et après chaque test (modifiés par la console)."""
    _reset_plans(owner_db)
    yield
    _reset_plans(owner_db)


def _tariff(subscription_id: Any, price: str = "10000", unit: Any = "3000") -> None:
    """Tarif figé de l'abonnement (comme à la souscription d'une offre publiée) : premier poste
    ``price``, poste supplémentaire ``unit``."""
    with _sites_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE subscriptions SET price_at_subscription = :p, currency_at_subscription = "
                "'XOF', activation_price_at_subscription = :u WHERE id = :s"
            ),
            {"p": price, "u": unit, "s": str(subscription_id)},
        )


def _shift(subscription_id: Any, days: int) -> None:
    """Recule dans le temps les licences et la période de l'abonnement de ``days`` jours
    (déclencheur d'immutabilité suspendu le temps du test) : simule le passage du temps."""
    interval = f"interval '{days} days'"
    with _sites_engine().begin() as conn:
        conn.execute(text("ALTER TABLE licenses DISABLE TRIGGER licenses_final"))
        conn.execute(
            text(
                f"UPDATE licenses SET starts_at = starts_at - {interval}, "
                f"ends_at = ends_at - {interval}, valid_from = valid_from - {days}, "
                f"valid_until = valid_until - {days} WHERE subscription_id = :s"
            ),
            {"s": str(subscription_id)},
        )
        conn.execute(text("ALTER TABLE licenses ENABLE TRIGGER licenses_final"))
        conn.execute(
            text(
                f"UPDATE subscriptions SET current_period_start = current_period_start - "
                f"{interval}, current_period_end = current_period_end - {interval} "
                "WHERE id = :s"
            ),
            {"s": str(subscription_id)},
        )


def _quote(owner: Api, subscription_id: Any, **params: Any) -> dict[str, Any]:
    response = owner.get(f"/subscriptions/{subscription_id}/renewal-quote", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _proposal(admin: TestClient, payment_id: str) -> dict[str, Any]:
    return dict(admin.get(f"{CONSOLE_PREFIX}/payments/{payment_id}/license-proposal").json())


def _day(value: str) -> date:
    return date.fromisoformat(value)


# --- R3 : devis serveur ----------------------------------------------------------------------


def test_first_quote_is_computed_by_the_server(alpha: Any, owner: Api) -> None:
    _tariff(alpha.subscription_id)
    quote = _quote(owner, alpha.subscription_id)
    assert quote["kind"] == "initial" and quote["renewal_due"] is False
    assert _day(quote["valid_from"]) == _today()
    # Période mensuelle : jusqu'à la veille du même jour le mois suivant.
    assert _day(quote["valid_until"]) > _today()
    # Aucune licence : postes demandés à la souscription (1) = prix du premier poste.
    assert (quote["activations"], quote["current_activations"]) == (1, None)
    assert quote["amount"] == "10000.00" and quote["currency"] == "XOF"
    # Chiffrage d'un nombre de postes supérieur : premier poste + 3 × 3000.
    assert _quote(owner, alpha.subscription_id, requested_activations=4)["amount"] == "19000.00"


def test_declaration_period_and_amount_come_from_the_server(alpha: Any, owner: Api) -> None:
    _tariff(alpha.subscription_id)
    base = {
        "subscription_id": str(alpha.subscription_id),
        "payment_method": "MOBILE_MONEY",
        "declared_reference": "OM-1",
        "idempotency_key": str(uuid.uuid4()),
    }
    # Période imposée par le client : refusée ; montant alors qu'un tarif existe : refusé.
    for extra in ({"period_start": "2020-01-01"}, {"period_end": "2099-12-31"}):
        assert owner.post("/subscription/payments", json=base | extra).status_code == 422
    forced = owner.post("/subscription/payments", json=base | {"amount": "1.00"})
    assert forced.status_code == 422 and forced.json()["code"] == "amount_computed_by_server"

    payment = owner.post("/subscription/payments", json=base).json()
    quote = _quote(owner, alpha.subscription_id)
    assert payment["amount"] == quote["amount"] == "10000.00"
    assert (payment["period_start"], payment["period_end"]) == (
        quote["valid_from"],
        quote["valid_until"],
    )
    assert payment["requested_activations"] is None


def test_offer_without_tariff_requires_the_agreed_amount(alpha: Any, owner: Api) -> None:
    body = {
        "subscription_id": str(alpha.subscription_id),
        "payment_method": "CASH",
        "declared_reference": "ESP-1",
        "idempotency_key": str(uuid.uuid4()),
    }
    missing = owner.post("/subscription/payments", json=body)
    assert missing.status_code == 422 and missing.json()["code"] == "amount_required"
    assert owner.post("/subscription/payments", json=body | {"amount": "7500"}).status_code == 201


def test_tariff_formula_first_poste_plus_extra_postes() -> None:
    """Formule fixe : premier poste + (postes − 1) × poste supplémentaire (montants : exemples)."""
    tariff = Tariff(price=Decimal("5000"), currency="XOF", activation_price=Decimal("1500"))
    assert tariff.amount(1) == Decimal("5000")
    assert tariff.amount(2) == Decimal("6500")
    assert tariff.amount(5) == Decimal("11000")  # 5000 + 4 × 1500
    # Poste supplémentaire sans prix : sur devis au-delà du premier poste.
    on_quote = Tariff(price=Decimal("5000"), currency="XOF", activation_price=None)
    assert (on_quote.amount(1), on_quote.amount(2)) == (Decimal("5000"), None)
    assert NO_TARIFF.amount(1) is None


def _site_subscription(owner: Api, site_id: str) -> dict[str, Any]:
    return dict(next(s for s in owner.get("/subscriptions").json() if s["site"]["id"] == site_id))


def _set_prices(admin: TestClient, **prices: str) -> None:
    response = admin.patch(
        f"{CONSOLE_PREFIX}/plans/ENTREPRISE/commercial",
        json={"reason": "Révision tarifaire", **prices},
        headers=CONSOLE_HEADERS,
    )
    assert response.status_code == 200, response.text


def test_catalogue_prices_apply_to_new_subscriptions_only(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    """Prix du premier poste et du poste supplémentaire paramétrés par TechNova, figés à la
    souscription : une révision ne touche ni les abonnements existants, ni leurs paiements, ni
    leurs licences ; les nouveaux abonnements prennent le nouveau tarif."""
    from tests.conftest import add_site, published_plan

    def new_site(code: str) -> dict[str, Any]:
        with published_plan("ENTREPRISE"):
            site = add_site(owner, f"Site {code}", code, active=False, requested_activations=5)
        assert site.status_code == 201, site.text
        return _site_subscription(owner, site.json()["id"])

    with published_plan("ENTREPRISE"):
        _set_prices(
            admin,
            monthly_price="5000",
            monthly_price_enabled=True,
            currency="XOF",
            monthly_activation_price="1500",
        )
        old = new_site("OLD")
    assert old["renewal"]["amount"] == "11000.00"  # 5000 + 4 × 1500
    assert _quote(owner, old["id"], requested_activations=1)["amount"] == "5000.00"
    payment = _confirmed_payment(owner, admin, old["id"], "OLD-1")
    licence = _generate(admin, payment, 5).json()

    # 1. Nouveau prix du premier poste : nouvel abonnement au nouveau tarif.
    with published_plan("ENTREPRISE"):
        _set_prices(admin, monthly_price="6000")
        first = new_site("NEW1")
    assert first["renewal"]["amount"] == "12000.00"  # 6000 + 4 × 1500
    assert _quote(owner, first["id"], requested_activations=1)["amount"] == "6000.00"

    # 2. Nouveau prix du poste supplémentaire : idem.
    with published_plan("ENTREPRISE"):
        _set_prices(admin, monthly_price="6000", monthly_activation_price="2000")
        second = new_site("NEW2")
    assert second["renewal"]["amount"] == "14000.00"  # 6000 + 4 × 2000

    # L'ancien abonnement garde son tarif (devis de renouvellement inclus), son paiement et sa
    # licence sont inchangés.
    assert _quote(owner, old["id"])["amount"] == "11000.00"
    assert _quote(owner, old["id"], requested_activations=1)["amount"] == "5000.00"
    stored = owner.get(f"/subscription/payments/{payment}").json()
    assert (stored["amount"], stored["status"]) == ("11000.00", "CONFIRMED")
    after = admin.get(f"{CONSOLE_PREFIX}/licenses/{licence['id']}").json()
    assert (after["max_activations"], after["valid_until"], after["state"]) == (
        licence["max_activations"],
        licence["valid_until"],
        "ACTIVE",
    )


def test_formula_shape_is_not_configurable(admin: TestClient, alpha: Any) -> None:
    """Seuls les paramètres de la formule sont paramétrables, pas sa forme : aucun « nombre de
    postes compris » (champ inconnu refusé)."""
    refused = admin.patch(
        f"{CONSOLE_PREFIX}/plans/STANDARD/commercial",
        json={"reason": "x", "included_activations": 3},
        headers=CONSOLE_HEADERS,
    )
    assert refused.status_code == 422
    assert "included_activations" not in admin.get(f"{CONSOLE_PREFIX}/plans/STANDARD").json()


def test_activation_price_requires_a_currency(admin: TestClient, alpha: Any) -> None:
    refused = admin.patch(
        f"{CONSOLE_PREFIX}/plans/STANDARD/commercial",
        json={"reason": "x", "annual_activation_price": "100", "currency": None},
        headers=CONSOLE_HEADERS,
    )
    assert refused.status_code == 422


# --- R1 : postes ---------------------------------------------------------------------------


def test_renewal_keeps_the_current_quota(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, owner_db: Session
) -> None:
    _tariff(alpha.subscription_id)
    first = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id), 5).json()
    quote = _quote(owner, alpha.subscription_id)
    assert quote["kind"] == "renewal" and quote["renewal_due"] is True
    # 5 postes (licence en vigueur), pas les postes demandés à la souscription (1).
    assert (quote["activations"], quote["current_activations"]) == (5, 5)
    assert quote["activations_explicit"] is False
    assert quote["amount"] == "22000.00"  # 10000 + 4 × 3000
    assert _day(quote["valid_from"]) == _day(first["valid_until"]) + timedelta(days=1)

    payment = _confirmed_payment(owner, admin, alpha.subscription_id, "V2")
    stored = owner_db.scalar(
        text("SELECT requested_activations FROM subscription_payments WHERE id = :p"),
        {"p": payment},
    )
    assert stored is None  # reconduction : aucune demande de changement
    proposal = _proposal(admin, payment)
    assert (proposal["max_activations"], proposal["current_activations"]) == (5, 5)
    renewed = _generate(admin, payment, proposal["max_activations"]).json()
    assert renewed["max_activations"] == 5
    assert _day(renewed["valid_from"]) == _day(first["valid_until"]) + timedelta(days=1)


def test_quota_change_must_be_explicit_and_confirmed(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    _tariff(alpha.subscription_id)
    _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id), 5)
    # Même valeur que l'actuelle : pas un changement.
    same = _confirmed_payment(
        owner, admin, alpha.subscription_id, "V-SAME", requested_activations=5
    )
    assert _proposal(admin, same)["requested_activations"] is None
    _generate(admin, same, 5)

    wanted = _quote(owner, alpha.subscription_id, requested_activations=10)
    assert wanted["activations_explicit"] is True and wanted["amount"] == "37000.00"
    payment = _confirmed_payment(
        owner, admin, alpha.subscription_id, "V10", requested_activations=10
    )
    proposal = _proposal(admin, payment)
    assert (proposal["requested_activations"], proposal["max_activations"]) == (10, 10)
    # TechNova confirme ou ajuste explicitement : ici 8.
    licence = _generate(admin, payment, 8).json()
    assert licence["max_activations"] == 8


# --- R2 : grâce et expiration -----------------------------------------------------------------


def test_renewal_during_grace_keeps_continuity(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    first = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    length = (_day(first["valid_until"]) - _day(first["valid_from"])).days
    # Licence échue depuis 3 jours (grâce STANDARD : 7 jours).
    _shift(alpha.subscription_id, length + 4)
    assert _subscription(owner)["effective_status"] == "past_due"
    ended = _day(first["valid_until"]) - timedelta(days=length + 4)
    quote = _quote(owner, alpha.subscription_id)
    assert _day(quote["valid_from"]) == ended + timedelta(days=1)
    assert quote["grace_continuity"] is True and quote["renewal_due"] is True

    renewed = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id, "V2")).json()
    assert _day(renewed["valid_from"]) == ended + timedelta(days=1)
    assert renewed["state"] == "ACTIVE"
    after = _subscription(owner)
    assert after["effective_status"] == "active"
    assert after["license"]["id"] == renewed["id"]


def test_renewal_after_expiry_starts_today(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    first = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    length = (_day(first["valid_until"]) - _day(first["valid_from"])).days
    _shift(alpha.subscription_id, length + 30)
    assert _subscription(owner)["effective_status"] == "expired"
    assert _quote(owner, alpha.subscription_id)["valid_from"] == _today().isoformat()
    renewed = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id, "V2")).json()
    assert renewed["valid_from"] == _today().isoformat()  # jamais rétroactif


# --- R4 : offre en vigueur / prochaine offre ---------------------------------------------------


def test_plan_change_applies_at_the_next_licence(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    first = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    changed = _cpost(
        admin,
        f"/tenants/{alpha.tenant_id}/subscriptions/{alpha.subscription_id}/change-plan",
        {"reason": "Passage à Entreprise", "plan_code": "ENTREPRISE"},
    )
    assert changed.status_code == 200, changed.text
    subscription = _subscription(owner)
    assert subscription["effective_plan"]["code"] == "STANDARD"
    assert subscription["next_plan"]["code"] == "ENTREPRISE"
    assert subscription["renewal"]["plan"]["code"] == "ENTREPRISE"
    site_api = Api(owner.client, owner.token, site_id=alpha.site_id)
    capabilities = site_api.get("/me/capabilities").json()
    assert capabilities["plan"]["code"] == "STANDARD"
    assert "stock.transfers" not in capabilities["features"]

    renewal = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id, "V2")).json()
    assert renewal["plan_code"] == "ENTREPRISE" and renewal["state"] == "NOT_YET_VALID"
    # À la fin de la licence en cours, la suivante est en vigueur (aucun job).
    length = (_day(first["valid_until"]) - _day(first["valid_from"])).days
    _shift(alpha.subscription_id, length + 1)
    after = _subscription(owner)
    assert after["effective_plan"]["code"] == "ENTREPRISE" and after["next_plan"] is None
    capabilities = site_api.get("/me/capabilities").json()
    assert capabilities["plan"]["code"] == "ENTREPRISE"
    assert "stock.transfers" in capabilities["features"]


# --- Postes au renouvellement ------------------------------------------------------------------


def _activate(api: Api, label: str) -> Any:
    return api.post(
        "/license-activations",
        json={"installation_id": str(uuid.uuid4()), "label": label},
    )


def test_postes_survive_renewal_and_expiry(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    site_api = Api(owner.client, owner.token, site_id=alpha.site_id)
    first = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id), 5).json()
    for n in range(3):
        assert _activate(site_api, f"Poste {n}").status_code == 201
    renewal_payment = _confirmed_payment(
        owner, admin, alpha.subscription_id, "V10", requested_activations=10
    )
    _generate(admin, renewal_payment, 10)
    length = (_day(first["valid_until"]) - _day(first["valid_from"])).days
    _shift(alpha.subscription_id, length + 1)
    licence = _subscription(owner)["license"]
    # Les 3 postes restent actifs ; quota augmenté : 7 disponibles.
    assert (licence["max_activations"], licence["activations_used"]) == (10, 3)
    assert licence["activations_available"] == 7

    # Expiration : aucun poste libéré.
    _shift(alpha.subscription_id, 400)
    assert _subscription(owner)["effective_status"] == "expired"
    active = site_api.get("/license-activations", params={"status": "ACTIVE"}).json()
    assert active["total"] == 3


def test_reduced_quota_at_renewal_tolerates_existing_postes(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    site_api = Api(owner.client, owner.token, site_id=alpha.site_id)
    first = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id), 3).json()
    for n in range(3):
        _activate(site_api, f"Poste {n}")
    payment = _confirmed_payment(owner, admin, alpha.subscription_id, "V1", requested_activations=1)
    _generate(admin, payment, 1)
    length = (_day(first["valid_until"]) - _day(first["valid_from"])).days
    _shift(alpha.subscription_id, length + 1)
    licence = _subscription(owner)["license"]
    assert (licence["max_activations"], licence["activations_used"]) == (1, 3)
    refused = _activate(site_api, "Poste 4")
    assert refused.status_code == 409 and refused.json()["code"] == "activation_quota_reached"


# --- Révocation et renouvellement déjà payé ------------------------------------------------------


def test_revocation_with_paid_renewal_has_no_automatic_fallback(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    current = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    future = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id, "V2")).json()
    assert future["state"] == "NOT_YET_VALID"
    _cpost(admin, f"/licenses/{current['id']}/revoke", {"reason": "Fraude"})
    assert _subscription(owner)["effective_status"] == "suspended"

    # Le temps passe : la licence déjà payée entre dans sa période, mais rien n'est réactivé
    # par effet de bord ; l'ancienne licence reste révoquée.
    length = (_day(current["valid_until"]) - _day(current["valid_from"])).days
    _shift(alpha.subscription_id, length + 1)
    after = _subscription(owner)
    assert after["status"] == "suspended" and after["effective_status"] == "suspended"
    assert admin.get(f"{CONSOLE_PREFIX}/licenses/{current['id']}").json()["state"] == "REVOKED"

    # Réémission explicite par TechNova : nouveau cycle, site de nouveau actif.
    reissued = _cpost(admin, f"/licenses/{future['id']}/reissue", {"reason": "Régularisation"})
    assert reissued.status_code == 201, reissued.text
    assert _subscription(owner)["effective_status"] == "active"
