"""Palier R7-A (ADR-0052) — points QR d'un site : jetons (empreinte seule), Segno, adresse
publique configurée, gestion, permissions, audit, isolation SQL et API, migration 0056.

Exercé par l'API (fixture ``qrclient`` : registre de test où ``restaurant.qr`` est disponible ;
le registre de production le garde planifié pendant le développement de R7).
"""

import hashlib
import re
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import segno
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings, public_url_errors
from app.core.db import create_session_factory, set_db_context
from app.core.public_urls import PublicUrlNotConfiguredError, PublicUrls
from app.main import create_app
from app.modules.restaurant_qr.qrcode import QR_BORDER, qr_svg
from app.modules.restaurant_qr.tokens import new_secret, secret_hash, well_formed
from tests.conftest import Api, add_site, set_site_module
from tests.test_restaurant_kitchen import _member
from tests.test_restaurant_menu_migration import _alembic, at_head  # noqa: F401
from tests.test_restaurant_orders import _api, _code, _ok

QR = "restaurant.qr"
POINTS = "/restaurant/qr/points"
BASE = "https://app.stockmanager.test"
URL_RE = re.compile(rf"^{re.escape(BASE)}/q#([A-Za-z0-9_-]{{43}})$")


@pytest.fixture
def resto(provision: Any, qrclient: TestClient) -> SimpleNamespace:
    """Restaurant ENTREPRISE (tables activées par défaut), QR activé sur le site A ; tables
    « T1 » et « T2 » du site A ; site B (QR activé) avec sa table « B1 »."""
    t = provision("rq-main", profile="restaurant.restaurant", plan="ENTREPRISE")
    owner = _api(qrclient, "owner@rq-main.example.com")
    a = str(t.site_id)
    b = _ok(add_site(owner, "Terrasse", "TER", "restaurant"), 201)["id"]
    for site in (a, b):
        assert set_site_module(owner, site, QR, True).status_code == 204
    t1 = _table(owner, a, "T1")
    t2 = _table(owner, a, "T2")
    b1 = _table(owner, b, "B1")
    return SimpleNamespace(
        tenant=t.tenant_id, owner=owner, client=qrclient, a=a, b=b, t1=t1, t2=t2, b1=b1
    )


def _table(owner: Api, site: str, label: str) -> str:
    return str(
        _ok(owner.post("/restaurant/tables", json={"site_id": site, "label": label}), 201)["id"]
    )


def _point(api: Api, site: str, **body: Any) -> Any:
    return api.post(POINTS, json={"site_id": site, **body})


def _table_point(r: SimpleNamespace, table: str, site: str | None = None) -> dict[str, Any]:
    return dict(_ok(_point(r.owner, site or r.a, kind="TABLE", table_id=table), 201))


def _token(issued: dict[str, Any]) -> str:
    match = URL_RE.match(issued["url"])
    assert match is not None, issued["url"]
    return match.group(1)


def _hash_in_db(owner_db: Session, point_id: str) -> str:
    return str(
        owner_db.execute(
            text("SELECT token_hash FROM restaurant_qr_points WHERE id = :id"), {"id": point_id}
        ).scalar_one()
    )


# --- Jetons, image et adresse publique ----------------------------------------------------------


def test_secrets_are_256_bits_and_only_their_hash_is_kept() -> None:
    tokens = {new_secret() for _ in range(200)}
    assert len(tokens) == 200
    for token in tokens:
        assert well_formed(token) and len(token) == 43  # 32 octets en base64url
        assert secret_hash(token) == hashlib.sha256(token.encode()).hexdigest()
    for bad in (None, "", "a" * 42, "a" * 44, "a" * 42 + "!", "../" + "a" * 40):
        assert not well_formed(bad)


def test_qr_code_is_a_scannable_svg() -> None:
    url = f"{BASE}/q#{new_secret()}"
    svg = qr_svg(url)
    assert svg.startswith("<svg") and 'viewBox="0 0' in svg and "<script" not in svg
    # Correction d'erreur Q, sans augmentation automatique (symbole reproductible).
    code = segno.make_qr(url, error="q", boost_error=False)
    assert code.error == "Q"
    side = 2 * QR_BORDER + code.symbol_size(scale=1, border=0)[0]
    assert f'viewBox="0 0 {side} {side}"' in svg


def test_public_url_rules_by_environment() -> None:
    allowed = ["app.stockmanager.test"]
    assert public_url_errors("https://app.stockmanager.test", allowed, "production") == []
    assert public_url_errors("https://APP.stockmanager.test/", allowed, "test") == []
    assert public_url_errors("http://localhost:5173", ["localhost"], "development") == []
    assert public_url_errors("http://app.stockmanager.test", allowed, "production")
    assert public_url_errors("https://evil.example", allowed, "production")
    assert public_url_errors("https://app.stockmanager.test/menu", allowed, "production")
    assert public_url_errors("https://app.stockmanager.test?x=1", allowed, "production")
    assert public_url_errors("https://u:p@app.stockmanager.test", allowed, "production")
    assert public_url_errors("ftp://app.stockmanager.test", allowed, "production")
    assert public_url_errors("https://app.stockmanager.test", [], "production")
    # Refus au démarrage (configuration invalide).
    with pytest.raises(ValueError, match="SM_PUBLIC_APP_URL"):
        Settings(
            environment="production",
            jwt_secret="x" * 40,
            rate_limit_hmac_key="y" * 40,
            public_app_url="https://evil.example",
            public_allowed_hosts=allowed,
        )
    with pytest.raises(ValueError, match="SM_RATE_LIMIT_HMAC_KEY"):
        Settings(environment="production", jwt_secret="x" * 40)
    # Développement : serveur Vite local par défaut ; test / production : aucune adresse
    # par défaut, génération refusée.
    assert PublicUrls(Settings(environment="development")).qr_point("t") == (
        "http://localhost:5173/q#t"
    )
    for environment in ("test", "production"):
        urls = PublicUrls(
            Settings(environment=environment, jwt_secret="x" * 40, rate_limit_hmac_key="y" * 40)
        )
        assert not urls.configured
        with pytest.raises(PublicUrlNotConfiguredError):
            urls.qr_point("t")
    configured = Settings(
        environment="production",
        jwt_secret="x" * 40,
        rate_limit_hmac_key="y" * 40,
        public_app_url="https://app.example.org/",
        public_allowed_hosts=["app.example.org"],
    )
    assert PublicUrls(configured).qr_tracking("s") == "https://app.example.org/q/track#s"


# --- Gestion des points -------------------------------------------------------------------------


def test_table_point_is_issued_once_and_only_its_hash_is_stored(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    response = _point(r.owner, r.a, kind="TABLE", table_id=r.t1)
    issued = _ok(response, 201)
    assert response.headers["cache-control"] == "no-store"
    token = _token(issued)
    assert issued["svg"].startswith("<svg")
    point = issued["point"]
    assert (point["kind"], point["display_name"], point["table_label"]) == ("TABLE", "T1", "T1")
    assert point["is_active"] and point["token_generated_by_name"]
    # En base : l'empreinte seule ; le jeton n'apparaît dans aucune colonne.
    assert _hash_in_db(owner_db, point["id"]) == secret_hash(token)
    row = owner_db.execute(
        text("SELECT row_to_json(p)::text FROM restaurant_qr_points p WHERE id = :id"),
        {"id": point["id"]},
    ).scalar_one()
    assert token not in row
    # Relecture : ni jeton, ni adresse, ni image, ni empreinte.
    for body in (
        _ok(r.owner.get(f"{POINTS}/{point['id']}")),
        _ok(r.owner.get(POINTS))["items"][0],
    ):
        flat = str(body)
        assert token not in flat and "url" not in body and "svg" not in body
        assert "token_hash" not in body
    # Audit : jamais le jeton ni son empreinte.
    logs = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_qr_point.created"}))
    assert logs["total"] == 1
    assert token not in str(logs) and secret_hash(token) not in str(logs)


def test_counter_point_label_is_unique_per_site(resto: SimpleNamespace) -> None:
    r = resto
    first = _ok(_point(r.owner, r.a, kind="COUNTER", label="Comptoir"), 201)
    assert first["point"]["display_name"] == "Comptoir" and first["point"]["table_id"] is None
    assert _code(_point(r.owner, r.a, kind="COUNTER", label="COMPTOIR")) == (
        409,
        "qr_point_label_taken",
    )
    # Autre site : même libellé accepté.
    _ok(_point(r.owner, r.b, kind="COUNTER", label="Comptoir"), 201)
    # Forme : table sans libellé, comptoir sans table.
    assert _point(r.owner, r.a, kind="COUNTER").status_code == 422
    assert _point(r.owner, r.a, kind="TABLE").status_code == 422
    assert _point(r.owner, r.a, kind="COUNTER", label="X", table_id=r.t1).status_code == 422
    # Libellé d'un comptoir modifiable ; celui d'une table, non.
    renamed = _ok(r.owner.put(f"{POINTS}/{first['point']['id']}", json={"label": "Bar"}))
    assert renamed["display_name"] == "Bar"
    table = _table_point(r, r.t1)
    assert _code(r.owner.put(f"{POINTS}/{table['point']['id']}", json={"label": "Z"})) == (
        422,
        "qr_point_label_not_editable",
    )


def test_table_point_requires_an_active_table_of_the_same_site(resto: SimpleNamespace) -> None:
    r = resto
    # Table d'un autre site : jamais révélée.
    assert _code(_point(r.owner, r.a, kind="TABLE", table_id=r.b1)) == (404, "table_not_found")
    assert _code(_point(r.owner, r.a, kind="TABLE", table_id=str(uuid.uuid4()))) == (
        404,
        "table_not_found",
    )
    _ok(r.owner.post(f"/restaurant/tables/{r.t2}/deactivate"))
    assert _code(_point(r.owner, r.a, kind="TABLE", table_id=r.t2)) == (422, "table_inactive")
    # Un seul point ACTIF par table.
    first = _table_point(r, r.t1)
    assert _code(_point(r.owner, r.a, kind="TABLE", table_id=r.t1)) == (
        409,
        "qr_point_table_taken",
    )
    _ok(r.owner.post(f"{POINTS}/{first['point']['id']}/deactivate"))
    second = _table_point(r, r.t1)
    assert _code(r.owner.post(f"{POINTS}/{first['point']['id']}/activate")) == (
        409,
        "qr_point_table_taken",
    )
    assert second["point"]["is_active"]
    # Sans le module des tables sur le site : seul un comptoir est possible.
    assert set_site_module(r.owner, r.b, "restaurant.tables", False).status_code == 204
    assert _code(_point(r.owner, r.b, kind="TABLE", table_id=r.b1)) == (
        422,
        "qr_tables_unavailable",
    )
    _ok(_point(r.owner, r.b, kind="COUNTER", label="Caisse"), 201)


def test_regeneration_needs_confirmation_and_replaces_the_hash(
    resto: SimpleNamespace, owner_db: Session
) -> None:
    r = resto
    issued = _table_point(r, r.t1)
    point = issued["point"]["id"]
    old = _token(issued)
    assert r.owner.post(f"{POINTS}/{point}/regenerate", json={}).status_code == 422
    assert (
        r.owner.post(
            f"{POINTS}/{point}/regenerate", json={"confirm_invalidation": False}
        ).status_code
        == 422
    )
    assert _hash_in_db(owner_db, point) == secret_hash(old)
    response = r.owner.post(f"{POINTS}/{point}/regenerate", json={"confirm_invalidation": True})
    again = _ok(response)
    assert response.headers["cache-control"] == "no-store"
    new = _token(again)
    assert new != old
    owner_db.expire_all()
    assert _hash_in_db(owner_db, point) == secret_hash(new)
    assert again["point"]["token_generated_at"] >= issued["point"]["token_generated_at"]
    logs = _ok(r.owner.get("/audit-logs", params={"action": "restaurant_qr_point.regenerated"}))
    assert logs["total"] == 1 and new not in str(logs) and old not in str(logs)


def test_url_comes_from_configuration_never_from_the_request(resto: SimpleNamespace) -> None:
    r = resto
    issued = _ok(
        r.owner.post(
            POINTS,
            json={"site_id": r.a, "kind": "COUNTER", "label": "Comptoir"},
            headers={
                "Host": "evil.example",
                "X-Forwarded-Host": "evil.example",
                "X-Forwarded-Proto": "http",
                "Origin": "https://evil.example",
            },
        ),
        201,
    )
    assert URL_RE.match(issued["url"]) and "evil" not in issued["url"] + issued["svg"]


def test_no_point_is_written_without_a_public_url(
    provision: Any, settings: Settings, migrated: None, qr_available: None, owner_db: Session
) -> None:
    unconfigured = settings.model_copy(update={"public_app_url": None})
    with TestClient(create_app(unconfigured)) as client:
        t = provision("rq-nourl", profile="restaurant.restaurant", plan="ENTREPRISE")
        owner = _api(client, "owner@rq-nourl.example.com")
        site = str(t.site_id)
        assert set_site_module(owner, site, QR, True).status_code == 204
        response = _point(owner, site, kind="COUNTER", label="Comptoir")
        assert _code(response) == (503, "public_url_not_configured")
    count = owner_db.execute(
        text("SELECT count(*) FROM restaurant_qr_points WHERE tenant_id = :t"),
        {"t": t.tenant_id},
    ).scalar_one()
    assert count == 0


# --- Permissions et portée ----------------------------------------------------------------------


def test_permissions_and_site_scope(resto: SimpleNamespace) -> None:
    r = resto
    _table_point(r, r.t1)
    _table_point(r, r.b1, site=r.b)
    seller = _member(r, "seller@rq-main.example.com", "seller")
    assert _code(seller.get(POINTS)) == (403, "permission_denied")
    assert _code(_point(seller, r.a, kind="COUNTER", label="X")) == (403, "permission_denied")
    viewer = _member(r, "viewer@rq-main.example.com", "viewer")
    assert _ok(viewer.get(POINTS))["total"] == 2
    assert _code(_point(viewer, r.a, kind="COUNTER", label="X")) == (403, "permission_denied")
    manager = _member(r, "manager@rq-main.example.com", "manager", all_sites=False, site_ids=[r.b])
    listed = _ok(manager.get(POINTS))["items"]
    assert [p["site_id"] for p in listed] == [r.b]
    a_point = _ok(r.owner.get(POINTS, params={"site_id": r.a}))["items"][0]["id"]
    assert _code(manager.get(f"{POINTS}/{a_point}")) == (404, "qr_point_not_found")
    assert _code(
        manager.post(f"{POINTS}/{a_point}/regenerate", json={"confirm_invalidation": True})
    ) == (404, "qr_point_not_found")
    assert _code(_point(manager, r.a, kind="COUNTER", label="X")) == (403, "site_access_denied")
    _ok(_point(manager, r.b, kind="COUNTER", label="Terrasse"), 201)
    # Site sélectionné sans le module : refus du module.
    assert set_site_module(r.owner, r.b, QR, False).status_code == 204
    selected = Api(r.owner.client, r.owner.token, uuid.UUID(r.b))
    assert _code(selected.get(POINTS)) == (403, "module_unavailable")


def test_isolation_between_companies_sql_and_api(
    resto: SimpleNamespace, provision: Any, qrclient: TestClient, app_engine: Engine
) -> None:
    r = resto
    issued = _table_point(r, r.t1)
    token_hash = secret_hash(_token(issued))
    t2 = provision("rq-other", profile="restaurant.restaurant", plan="ENTREPRISE")
    stranger = _api(qrclient, "owner@rq-other.example.com")
    assert set_site_module(stranger, str(t2.site_id), QR, True).status_code == 204
    assert _code(stranger.get(f"{POINTS}/{issued['point']['id']}")) == (404, "qr_point_not_found")
    assert _ok(stranger.get(POINTS))["total"] == 0
    factory = create_session_factory(app_engine)
    count = "SELECT count(*) FROM restaurant_qr_points"
    with factory() as db:
        # Aucun contexte : aucune ligne.
        assert db.execute(text(count)).scalar_one() == 0
        db.rollback()
        # Autre entreprise : aucune ligne.
        set_db_context(db, tenant_id=t2.tenant_id)
        assert db.execute(text(count)).scalar_one() == 0
        db.rollback()
        # Empreinte d'un AUTRE jeton : aucune ligne ; empreinte exacte : cette seule ligne,
        # en lecture seule.
        set_db_context(db, tenant_id=None, qr_token_hash=secret_hash(new_secret()))
        assert db.execute(text(count)).scalar_one() == 0
        db.rollback()
        set_db_context(db, tenant_id=None, qr_token_hash=token_hash)
        rows = db.execute(text("SELECT id, tenant_id FROM restaurant_qr_points")).all()
        assert [(str(i), t) for i, t in rows] == [(issued["point"]["id"], r.tenant)]
        updated = db.execute(text("UPDATE restaurant_qr_points SET is_active = false")).rowcount
        assert updated == 0
        db.rollback()


def test_app_role_privileges_and_forced_rls(owner_db: Session, settings: Settings) -> None:
    role = settings.db_app_role
    privileges = set(
        owner_db.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :r AND table_name = 'restaurant_qr_points'"
            ),
            {"r": role},
        ).scalars()
    )
    assert privileges == {"SELECT", "INSERT"}
    updatable = set(
        owner_db.execute(
            text(
                "SELECT column_name FROM information_schema.column_privileges WHERE grantee = :r "
                "AND table_name = 'restaurant_qr_points' AND privilege_type = 'UPDATE'"
            ),
            {"r": role},
        ).scalars()
    )
    assert updatable == {
        "label",
        "token_hash",
        "token_generated_at",
        "token_generated_by",
        "is_active",
        "updated_at",
    }
    flags = owner_db.execute(
        text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'restaurant_qr_points'"
        )
    ).one()
    assert tuple(flags) == (True, True)
    policies = dict(
        owner_db.execute(
            text("SELECT policyname, cmd FROM pg_policies WHERE tablename = 'restaurant_qr_points'")
        ).all()
    )
    assert policies == {"tenant_isolation": "ALL", "qr_token_lookup": "SELECT"}
    for other in ("stockmanager_platform", "stockmanager_purge"):
        assert (
            owner_db.execute(
                text(
                    "SELECT count(*) FROM information_schema.table_privileges "
                    "WHERE grantee = :r AND table_name = 'restaurant_qr_points'"
                ),
                {"r": other},
            ).scalar_one()
            == 0
        )


def test_production_registry_keeps_qr_planned(
    provision: Any, settings: Settings, migrated: None
) -> None:
    with TestClient(create_app(settings)) as client:
        provision("rq-prod", profile="restaurant.restaurant", plan="ENTREPRISE")
        owner = _api(client, "owner@rq-prod.example.com")
        assert owner.get(POINTS).status_code == 404  # routes non montées


# --- Migration 0056 -----------------------------------------------------------------------------


def test_migration_0056_downgrade_refuses_to_lose_points(
    resto: SimpleNamespace,
    owner_db: Session,
    owner_engine: Engine,
    at_head: None,  # noqa: F811
) -> None:
    _table_point(resto, resto.t1)
    # Réglages QR créés à l'activation (0057, descendue d'abord) : retirés pour atteindre 0056.
    owner_db.execute(text("DELETE FROM restaurant_qr_site_settings"))
    owner_db.commit()
    owner_db.close()
    owner_engine.dispose()
    with pytest.raises(RuntimeError, match="point"):
        command.downgrade(_alembic(), "0055")
    with owner_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM restaurant_qr_points")).scalar_one() == 1
        flags = conn.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'restaurant_qr_points'")
        ).scalar_one()
        assert flags is True
