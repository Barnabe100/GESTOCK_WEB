"""Palier R7-B (ADR-0052) — accès public par QR Code : garde unique (jeton, point, entreprise,
site, abonnement, modules, table), menu public sans donnée interne, en-têtes de sécurité,
limitation de fréquence par adresse IP (empreinte HMAC), proxies de confiance."""

import hashlib
import hmac
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.core.client_ip import client_ip
from app.core.config import Settings
from app.core.db import create_session_factory
from app.main import create_app
from app.modules.restaurant_qr.tokens import new_secret
from app.platform.ratelimit.service import RateLimiter
from app.shared.clock import utcnow
from tests.conftest import add_site, set_site_module
from tests.test_restaurant_orders import _api, _code, _menu, _ok
from tests.test_restaurant_qr_points import POINTS, QR, _table, _token

MENU = "/api/v1/public/restaurant/qr/menu"
PUBLIC_ITEM_KEYS = {"id", "name", "packaging_name", "description", "price", "available"}


@pytest.fixture
def resto(provision: Any, qrclient: TestClient) -> SimpleNamespace:
    """Restaurant ENTREPRISE, QR activé sur le site A : table « T1 » avec son point, point de
    comptoir ; menu du site A : bière (bouteille 700, casier 8 000) ; site B avec son propre
    menu et son point de comptoir."""
    t = provision("rqp-main", profile="restaurant.restaurant", plan="ENTREPRISE")
    owner = _api(qrclient, "owner@rqp-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER", "restaurant"), 201)["id"]
    for site in (a, b):
        assert set_site_module(owner, site, QR, True).status_code == 204
    menu = _menu(owner, a, "PUB")
    menu_b = _menu(owner, b, "PUBB")
    t1 = _table(owner, a, "T1")
    table = _ok(owner.post(POINTS, json={"site_id": a, "kind": "TABLE", "table_id": t1}), 201)
    counter = _ok(
        owner.post(POINTS, json={"site_id": a, "kind": "COUNTER", "label": "Comptoir"}), 201
    )
    counter_b = _ok(
        owner.post(POINTS, json={"site_id": b, "kind": "COUNTER", "label": "Bar B"}), 201
    )
    return SimpleNamespace(
        tenant=t.tenant_id,
        owner=owner,
        client=qrclient,
        a=a,
        b=b,
        menu=menu,
        menu_b=menu_b,
        t1=t1,
        table=table,
        table_token=_token(table),
        counter=counter,
        counter_token=_token(counter),
        counter_b_token=_token(counter_b),
    )


def _menu_of(client: TestClient, token: str | None) -> Any:
    headers = {"X-QR-Token": token} if token is not None else {}
    return client.get(MENU, headers=headers)


def _unavailable(response: Any) -> None:
    assert _code(response) == (404, "qr_unavailable")
    body = response.json()
    # Message générique : aucune vérification n'est nommée.
    assert set(body) == {"type", "title", "status", "detail", "code"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


# --- Menu public --------------------------------------------------------------------------------


def test_public_menu_shows_only_what_the_customer_needs(resto: SimpleNamespace) -> None:
    r = resto
    response = _menu_of(r.client, r.table_token)
    menu = _ok(response)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert set(menu) == {"establishment", "site_name", "point", "currency", "sections"}
    assert menu["point"] == {"kind": "TABLE", "label": "T1"}
    assert menu["establishment"] == "Entreprise rqp-main" and menu["currency"] == "XOF"
    [section] = menu["sections"]
    assert section["name"] == "Boissons" and set(section) == {"id", "name", "items"}
    items = {i["id"]: i for i in section["items"]}
    assert set(items) == {r.menu.unit, r.menu.crate_item}
    for item in items.values():
        assert set(item) == PUBLIC_ITEM_KEYS
    assert (items[r.menu.unit]["price"], items[r.menu.crate_item]["price"]) == ("700.00", "8000.00")
    assert items[r.menu.crate_item]["packaging_name"] == "Casier 12"
    flat = str(menu)
    for internal in ("400", "BIERE-PUB", r.menu.article, "purchase", "stock", "cost"):
        assert internal not in flat
    # Le menu d'un AUTRE site de la même entreprise n'apparaît jamais.
    assert r.menu_b.unit not in flat


def test_menu_follows_availability_and_hides_internal_blockers(resto: SimpleNamespace) -> None:
    r = resto
    _ok(
        r.owner.put(
            f"/restaurant/menu/items/{r.menu.unit}/availability",
            json={"available": False, "reason": "Rupture"},
        )
    )
    items = {i["id"]: i for i in _ok(_menu_of(r.client, r.counter_token))["sections"][0]["items"]}
    # Épuisé : visible, non commandable ; motif interne jamais exposé.
    assert items[r.menu.unit]["available"] is False and "Rupture" not in str(items)
    # Section désactivée : absente ; réactivée : de retour.
    section = _ok(r.owner.get(f"/restaurant/menu/items/{r.menu.unit}"))["section_id"]
    _ok(r.owner.post(f"/restaurant/menu/sections/{section}/deactivate"))
    assert _ok(_menu_of(r.client, r.counter_token))["sections"] == []
    _ok(r.owner.post(f"/restaurant/menu/sections/{section}/activate"))
    # Article retiré de l'assortiment du site : absent du menu public (motif interne, jamais
    # exposé), sans erreur.
    _ok(
        r.owner.post(
            f"/catalog/sites/{r.a}/articles/remove", json={"article_ids": [r.menu.article]}
        )
    )
    menu = _ok(_menu_of(r.client, r.counter_token))
    assert menu["sections"] == [] and "assortment" not in str(menu)


def test_access_is_refused_identically_for_every_failure(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    for token in (None, "", "x" * 10, "a" * 42 + "!", new_secret()):
        _unavailable(_menu_of(r.client, token))
    # Régénération : l'ancien jeton cesse immédiatement de fonctionner, le nouveau fonctionne.
    regenerated = _ok(
        r.owner.post(
            f"{POINTS}/{r.counter['point']['id']}/regenerate", json={"confirm_invalidation": True}
        )
    )
    _unavailable(_menu_of(r.client, r.counter_token))
    _ok(_menu_of(r.client, _token(regenerated)))
    # Point désactivé, puis réactivé.
    _ok(r.owner.post(f"{POINTS}/{r.table['point']['id']}/deactivate"))
    _unavailable(_menu_of(r.client, r.table_token))
    _ok(r.owner.post(f"{POINTS}/{r.table['point']['id']}/activate"))
    _ok(_menu_of(r.client, r.table_token))
    # Table désactivée : le point de table refuse, le comptoir fonctionne.
    _ok(r.owner.post(f"/restaurant/tables/{r.t1}/deactivate"))
    _unavailable(_menu_of(r.client, r.table_token))
    _ok(r.owner.post(f"/restaurant/tables/{r.t1}/activate"))
    _ok(_menu_of(r.client, r.table_token))
    # Tables désactivées sur le site : seuls les comptoirs restent utilisables.
    assert set_site_module(r.owner, r.a, "restaurant.tables", False).status_code == 204
    _unavailable(_menu_of(r.client, r.table_token))
    _ok(_menu_of(r.client, _token(regenerated)))
    assert set_site_module(r.owner, r.a, "restaurant.tables", True).status_code == 204
    # Module QR désactivé sur le site.
    assert set_site_module(r.owner, r.a, QR, False).status_code == 204
    _unavailable(_menu_of(r.client, r.table_token))
    assert set_site_module(r.owner, r.a, QR, True).status_code == 204
    _ok(_menu_of(r.client, r.table_token))


def test_site_company_and_subscription_states_are_checked(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    states = (
        (
            "UPDATE sites SET is_active = false WHERE id = :site",
            "UPDATE sites SET is_active = true WHERE id = :site",
        ),
        (
            "UPDATE tenants SET status = 'suspended' WHERE id = :tenant",
            "UPDATE tenants SET status = 'active' WHERE id = :tenant",
        ),
        (
            "UPDATE subscriptions SET status = 'suspended' WHERE site_id = :site",
            "UPDATE subscriptions SET status = 'active' WHERE site_id = :site",
        ),
        (
            "UPDATE subscriptions SET status = 'pending_activation' WHERE site_id = :site",
            "UPDATE subscriptions SET status = 'active' WHERE site_id = :site",
        ),
    )
    for refuse, restore in states:
        owner_db.execute(text(refuse), {"site": r.a, "tenant": r.tenant})
        owner_db.commit()
        _unavailable(_menu_of(r.client, r.table_token))
        owner_db.execute(text(restore), {"site": r.a, "tenant": r.tenant})
        owner_db.commit()
        _ok(_menu_of(r.client, r.table_token))
    # Le site B n'est pas concerné par l'abonnement du site A.
    owner_db.execute(
        text("UPDATE subscriptions SET status = 'suspended' WHERE site_id = :site"), {"site": r.a}
    )
    owner_db.commit()
    _ok(_menu_of(r.client, r.counter_b_token))


def test_a_token_only_opens_its_own_site_and_company(
    resto: SimpleNamespace, provision: Any, qrclient: TestClient
) -> None:
    r = resto
    t2 = provision("rqp-other", profile="restaurant.restaurant", plan="ENTREPRISE")
    other = _api(qrclient, "owner@rqp-other.example.com")
    site2 = str(t2.site_id)
    assert set_site_module(other, site2, QR, True).status_code == 204
    menu2 = _menu(other, site2, "OTH")
    token2 = _token(
        _ok(other.post(POINTS, json={"site_id": site2, "kind": "COUNTER", "label": "C"}), 201)
    )
    mine = str(_ok(_menu_of(r.client, r.counter_token)))
    theirs = str(_ok(_menu_of(r.client, token2)))
    assert menu2.unit in theirs and menu2.unit not in mine
    assert r.menu.unit in mine and r.menu.unit not in theirs
    assert "Entreprise rqp-other" in theirs and "Entreprise rqp-main" not in theirs


def test_public_routes_are_not_mounted_while_qr_is_planned(
    settings: Settings, migrated: None
) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get(MENU, headers={"X-QR-Token": new_secret()}).status_code == 404
        assert client.get(MENU).json().get("code") != "qr_unavailable"


# --- Limitation de fréquence et adresse du client -----------------------------------------------


def test_public_reads_are_rate_limited(
    resto: SimpleNamespace, settings: Settings, owner_db: Session
) -> None:
    limited = settings.model_copy(update={"qr_read_ip_limit": 3})
    owner_db.execute(text("DELETE FROM rate_limit_hits"))
    owner_db.commit()
    with TestClient(create_app(limited)) as client:
        for _ in range(3):
            _ok(_menu_of(client, resto.counter_token))
        response = _menu_of(client, resto.counter_token)
        assert _code(response) == (429, "rate_limited")
        assert int(response.headers["retry-after"]) >= 1
        assert response.headers["cache-control"] == "no-store"
        # Un jeton inconnu est compté aussi (aucune recherche par force brute illimitée).
        assert _menu_of(client, new_secret()).status_code == 429
    keys = owner_db.execute(text("SELECT key_hash FROM rate_limit_hits")).scalars().all()
    expected = hmac.new(
        settings.rate_limit_hmac_key.get_secret_value().encode(),
        b"qr.read.ip:unknown",
        hashlib.sha256,
    ).hexdigest()
    assert set(keys) == {expected}


def test_rate_limit_keys_are_hmac_with_the_server_key(app_engine: Engine) -> None:
    with create_session_factory(app_engine)() as session:
        one = RateLimiter(session, utcnow(), "key-one")
        two = RateLimiter(session, utcnow(), "key-two")
        plain = hashlib.sha256(b"qr.order.ip:203.0.113.9").hexdigest()
        h1 = one.key_hash("qr.order.ip", "203.0.113.9")
        assert h1 != plain and h1 != two.key_hash("qr.order.ip", "203.0.113.9")
        assert h1 == one.key_hash("qr.order.ip", "203.0.113.9")
        one.hit("qr.order.ip", "203.0.113.9", limit=5, window=timedelta(minutes=1))
        session.rollback()


def _request(peer: str, forwarded: list[str] | None = None) -> Request:
    headers = [(b"x-forwarded-for", value.encode()) for value in (forwarded or [])]
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_client_ip_trusts_only_declared_proxies() -> None:
    open_ = Settings(environment="test")
    trusting = Settings(environment="test", trusted_proxies=["10.0.0.0/8", "192.0.2.10"])
    # Aucun proxy de confiance : l'adresse de la connexion seule, en-tête ignoré.
    assert client_ip(_request("198.51.100.4", ["203.0.113.1"]), open_) == "198.51.100.4"
    # Pair non fiable : en-tête ignoré même si des proxies sont déclarés.
    assert client_ip(_request("198.51.100.4", ["203.0.113.1"]), trusting) == "198.51.100.4"
    # Pair de confiance : dernière adresse non fiable de la chaîne (de droite à gauche).
    assert (
        client_ip(_request("10.1.2.3", ["6.6.6.6, 203.0.113.1, 192.0.2.10"]), trusting)
        == "203.0.113.1"
    )
    assert client_ip(_request("192.0.2.10", ["203.0.113.7"]), trusting) == "203.0.113.7"
    # En-tête altéré : on s'arrête au dernier proxy de confiance.
    assert client_ip(_request("10.1.2.3", ["not-an-ip"]), trusting) == "10.1.2.3"
    assert client_ip(_request("10.1.2.3"), trusting) == "10.1.2.3"
    assert client_ip(_request("testclient"), open_) == "unknown"
    with pytest.raises(ValueError, match="SM_TRUSTED_PROXIES"):
        Settings(environment="test", trusted_proxies=["not-a-network"])
