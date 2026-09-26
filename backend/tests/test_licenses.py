"""Phase 3.3-B2 — Licences des sites (ADR-0034).

Chaîne PLAN → SUBSCRIPTION (du site) → PAYMENT CONFIRMED → LICENCE : génération par TechNova
depuis un paiement confirmé seulement, signature par le Signing Service (émulé ici avec une
clé **éphémère**), vérification, abonnement du site aligné, double audit ; révocation
définitive ; réémission = nouveau cycle ; renouvellement contigu ; conditions figées par la
licence ; sécurité (RLS, droits SQL, immutabilité, aucune route de licence côté entreprise).
"""

import json
import threading
import uuid
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor
from app.console.licenses import LicenseAdminService
from app.core.db import create_session_factory, set_db_context
from app.core.errors import ConflictError
from app.platform.audit.service import RequestMeta
from app.platform.licensing.keyring import verify_document
from app.platform.registry import get_registry
from app.shared.clock import utcnow
from tests.conftest import CONSOLE_HEADERS, CONSOLE_PREFIX, Api, console_login
from tests.test_licensing_crypto import FakeSigningService, client_for, keyring_for

REASON = "Paiement confirmé : émission de la licence"


# --- Aides --------------------------------------------------------------------------------


@pytest.fixture
def signing(console_app: Any) -> Any:
    """Signing Service émulé (clé éphémère) branché sur la console le temps du test."""
    key = Ed25519PrivateKey.generate()
    keyring = keyring_for(("k-active", key, "active"))
    fake = FakeSigningService(key)
    previous = getattr(console_app.state, "signing_client", None)
    console_app.state.signing_client = client_for(fake, keyring)
    yield fake, keyring
    console_app.state.signing_client = previous


@pytest.fixture
def admin(console: TestClient, platform_admin: Any) -> TestClient:
    platform_admin()
    assert console_login(console).status_code == 200
    return console


@pytest.fixture
def alpha(provision: Any) -> Any:
    return provision("alpha", plan="STANDARD")


@pytest.fixture
def owner(alpha: Any, api_for: Any) -> Api:
    api: Api = api_for("owner@alpha.example.com")
    return api


def _cpost(c: TestClient, path: str, body: dict[str, Any] | None = None) -> Any:
    return c.post(f"{CONSOLE_PREFIX}{path}", json=body or {}, headers=CONSOLE_HEADERS)


def _cget(c: TestClient, path: str, **kw: Any) -> Any:
    return c.get(f"{CONSOLE_PREFIX}{path}", **kw)


def _declare(owner: Api, subscription_id: uuid.UUID, reference: str = "VIR-1") -> str:
    response = owner.post(
        "/subscription/payments",
        json={
            "subscription_id": str(subscription_id),
            "amount": "10000.00",
            "period_start": "2026-10-01",
            "period_end": "2026-11-01",
            "payment_method": "BANK_TRANSFER",
            "declared_reference": reference,
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _confirmed_payment(
    owner: Api, admin: TestClient, subscription_id: uuid.UUID, ref: str = "V1"
) -> str:
    payment_id = _declare(owner, subscription_id, ref)
    confirmed = _cpost(admin, f"/payments/{payment_id}/confirm", {"reason": "Virement reçu"})
    assert confirmed.status_code == 200, confirmed.text
    return payment_id


def _generate(admin: TestClient, payment_id: str, max_activations: int = 3) -> Any:
    return _cpost(
        admin,
        f"/payments/{payment_id}/license",
        {"reason": REASON, "max_activations": max_activations},
    )


def _subscription(owner: Api) -> dict[str, Any]:
    subscriptions = owner.get("/subscriptions").json()
    assert len(subscriptions) == 1
    return dict(subscriptions[0])


def _today(timezone: str = "Africa/Ouagadougou") -> date:
    return utcnow().astimezone(ZoneInfo(timezone)).date()


def _sql(owner_db: Session, sql: str, **params: Any) -> Any:
    result = owner_db.execute(text(sql), params)
    owner_db.commit()
    return result


def _count(owner_db: Session, table: str = "licenses") -> int:
    return int(owner_db.scalar(text(f"SELECT count(*) FROM {table}")) or 0)


# --- Génération ---------------------------------------------------------------------------


def test_generation_requires_a_confirmed_payment(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, owner_db: Session
) -> None:
    pending = _declare(owner, alpha.subscription_id, "VIR-PENDING")
    response = _generate(admin, pending)
    assert response.status_code == 409
    assert response.json()["code"] == "payment_not_confirmed"

    rejected = _declare(owner, alpha.subscription_id, "VIR-REJ")
    _cpost(admin, f"/payments/{rejected}/reject", {"reason": "Introuvable"})
    assert _generate(admin, rejected).json()["code"] == "payment_not_confirmed"
    proposal = _cget(admin, f"/payments/{rejected}/license-proposal").json()
    assert proposal["blocking"] == "payment_not_confirmed"
    assert _count(owner_db) == 0
    assert signing[0].calls == []


def test_generation_issues_signed_licence_and_activates_the_site(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, owner_db: Session
) -> None:
    _sql(
        owner_db,
        "UPDATE subscriptions SET status = 'pending_activation', requested_activations = 2 "
        "WHERE id = :id",
        id=alpha.subscription_id,
    )
    payment = _confirmed_payment(owner, admin, alpha.subscription_id)
    proposal = _cget(admin, f"/payments/{payment}/license-proposal").json()
    today = _today()
    assert proposal["blocking"] is None
    assert proposal["requested_activations"] == 2 and proposal["max_activations"] == 2
    assert proposal["valid_from"] == today.isoformat()

    response = _generate(admin, payment, max_activations=3)
    assert response.status_code == 201, response.text
    licence = response.json()
    assert licence["license_number"].startswith(f"LIC-{utcnow().year}-")
    assert licence["state"] == "ACTIVE" and licence["status"] == "ISSUED"
    assert licence["license_version"] == 1 and licence["supersedes_id"] is None
    assert licence["max_activations"] == 3  # ajusté par TechNova (Q3), figé
    assert licence["valid_from"] == today.isoformat()
    assert licence["valid_until"] == proposal["valid_until"]
    assert licence["plan_code"] == "STANDARD"
    assert "sales" in licence["modules"] and licence["features"] == []
    assert licence["limits"]["max_users"] == 5
    assert licence["issued_by_email"] == "admin@technova.example"

    # Abonnement du site actif sur la période de la licence (paiement ≠ activation : c'est la
    # licence qui active).
    subscription = _subscription(owner)
    assert subscription["status"] == "active"
    assert subscription["effective_status"] == "active"
    assert subscription["license"]["license_number"] == licence["license_number"]
    assert subscription["license"]["state"] == "ACTIVE"
    assert subscription["license"]["max_activations"] == 3
    end = date.fromisoformat(licence["valid_until"]) + timedelta(days=1)
    assert subscription["current_period_end"].startswith(end.isoformat())

    # Fichier .lic : document v1 signé, vérifiable avec le trousseau public ; aucun prix.
    file = _cget(admin, f"/licenses/{licence['id']}/file")
    assert file.status_code == 200
    assert f"{licence['license_number']}.lic" in file.headers["content-disposition"]
    document = json.loads(file.content)
    payload = verify_document(document, signing[1], require_active_key=True)
    assert payload["license_id"] == licence["id"]
    assert payload["site_id"] == str(alpha.site_id)
    assert payload["payment_id"] == payment
    assert payload["max_activations"] == 3
    assert not any("price" in key or "amount" in key for key in payload)

    # Double audit dans la même transaction.
    platform = owner_db.execute(
        text("SELECT reason, data FROM platform_audit_logs WHERE action = 'license.generated'")
    ).all()
    mirror = owner_db.execute(
        text(
            "SELECT user_id, data FROM audit_logs WHERE action = 'license.generated' "
            "AND tenant_id = :t"
        ),
        {"t": alpha.tenant_id},
    ).all()
    assert len(platform) == 1 and platform[0].reason == REASON
    assert platform[0].data["license_number"] == licence["license_number"]
    assert platform[0].data["requested_activations"] == 2
    assert len(mirror) == 1 and mirror[0].user_id is None
    assert mirror[0].data["actor"] == "technova"


def test_one_licence_per_payment(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, owner_db: Session
) -> None:
    payment = _confirmed_payment(owner, admin, alpha.subscription_id)
    first = _generate(admin, payment).json()
    again = _generate(admin, payment)
    assert again.status_code == 409
    assert again.json()["code"] == "license_already_issued"
    assert again.json()["license_id"] == first["id"]
    assert _cget(admin, f"/payments/{payment}/license-proposal").json()["license_id"] == first["id"]
    assert _count(owner_db) == 1


def test_concurrent_generations_issue_a_single_licence(
    alpha: Any,
    owner: Api,
    admin: TestClient,
    signing: Any,
    platform_engine: Engine,
    platform_admin: Any,
    owner_db: Session,
) -> None:
    payment = uuid.UUID(_confirmed_payment(owner, admin, alpha.subscription_id))
    admin_id = owner_db.scalar(text("SELECT id FROM users WHERE email = 'admin@technova.example'"))
    actor = PlatformActor(user_id=admin_id, label="admin@technova.example")
    outcomes: list[str] = []
    factory = create_session_factory(platform_engine)
    signer = client_for(signing[0], signing[1])

    def generate() -> None:
        with factory() as db:
            service = LicenseAdminService(db, get_registry(), utcnow(), signer)
            try:
                service.generate(
                    payment, max_activations=2, reason=REASON, actor=actor, meta=RequestMeta()
                )
                db.commit()
                outcomes.append("issued")
            except ConflictError as exc:
                db.rollback()
                outcomes.append(exc.code)

    threads = [threading.Thread(target=generate) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["issued"] + ["license_already_issued"] * 3
    assert _count(owner_db) == 1


def test_signing_failures_leave_no_trace(
    alpha: Any, owner: Api, admin: TestClient, console_app: Any, signing: Any, owner_db: Session
) -> None:
    payment = _confirmed_payment(owner, admin, alpha.subscription_id)
    before = _subscription(owner)

    signing[0].override = (503, b"")
    assert _generate(admin, payment).json()["code"] == "signing_service_unavailable"
    signing[0].override = (200, b'{"format":"stockmanager-license"}')
    assert _generate(admin, payment).status_code == 502

    console_app.state.signing_client = None  # service non configuré
    response = _generate(admin, payment)
    assert response.status_code == 503
    assert response.json()["code"] == "signing_service_unavailable"

    assert _count(owner_db) == 0
    after = _subscription(owner)
    assert (after["status"], after["current_period_end"]) == (
        before["status"],
        before["current_period_end"],
    )
    assert (
        owner_db.scalar(
            text("SELECT count(*) FROM platform_audit_logs WHERE action = 'license.generated'")
        )
        == 0
    )


def test_renewal_starts_the_day_after_current_coverage(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    first = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    second_payment = _confirmed_payment(owner, admin, alpha.subscription_id, "V2")
    proposal = _cget(admin, f"/payments/{second_payment}/license-proposal").json()
    expected_start = date.fromisoformat(first["valid_until"]) + timedelta(days=1)
    assert proposal["valid_from"] == expected_start.isoformat()

    second = _generate(admin, second_payment, max_activations=3).json()
    assert second["valid_from"] == expected_start.isoformat()
    assert second["state"] == "NOT_YET_VALID"
    subscription = _subscription(owner)
    # La période de l'abonnement couvre les deux licences, sans jour perdu ni offert.
    end = date.fromisoformat(second["valid_until"]) + timedelta(days=1)
    assert subscription["current_period_end"].startswith(end.isoformat())
    # La licence en vigueur reste la première.
    assert subscription["license"]["id"] == first["id"]


def test_licence_freezes_modules_features_and_limits(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id))
    site_api = Api(owner.client, owner.token, site_id=alpha.site_id)
    assert "stock.transfers" not in site_api.get("/me/capabilities").json()["features"]
    # Changement de plan (TechNova) : il vaudra à la licence suivante, pas à celle en vigueur.
    changed = _cpost(
        admin,
        f"/tenants/{alpha.tenant_id}/subscriptions/{alpha.subscription_id}/change-plan",
        {"reason": "Passage à Entreprise", "plan_code": "ENTREPRISE"},
    )
    assert changed.status_code == 200, changed.text
    capabilities = site_api.get("/me/capabilities").json()
    assert "stock.transfers" not in capabilities["features"]
    assert capabilities["limits"]["max_users"]["limit"] == 5
    assert _subscription(owner)["limits"]["max_users"]["limit"] == 5


def test_manual_activation_no_longer_applies_to_a_licensed_subscription(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id))
    detail = _cget(admin, f"/tenants/{alpha.tenant_id}").json()
    block = detail["subscriptions"][0]
    assert block["license"]["state"] == "ACTIVE"
    assert block["actions"]["can_activate"] is False
    assert block["actions"]["can_extend"] is False
    extended = _cpost(
        admin,
        f"/tenants/{alpha.tenant_id}/subscriptions/{alpha.subscription_id}/extend",
        {"reason": "Geste commercial", "period_end": "2099-01-01"},
    )
    assert extended.status_code == 409
    assert extended.json()["code"] == "subscription_license_controlled"


# --- Révocation et réémission ---------------------------------------------------------------


def test_revocation_is_final_and_suspends_the_site(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, owner_db: Session
) -> None:
    licence = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    revoked = _cpost(admin, f"/licenses/{licence['id']}/revoke", {"reason": "Fraude avérée"})
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["state"] == "REVOKED" and body["revocation_reason"] == "Fraude avérée"

    subscription = _subscription(owner)
    assert subscription["status"] == "suspended"
    assert subscription["allowed_access"] == ["billing"]
    assert subscription["license"]["state"] == "REVOKED"
    # Les données sont conservées ; seul l'accès suit la politique « suspendu ».
    site_api = Api(owner.client, owner.token, site_id=alpha.site_id)
    assert site_api.get("/me/capabilities").json()["subscription"]["status"] == "suspended"

    again = _cpost(admin, f"/licenses/{licence['id']}/revoke", {"reason": "Encore"})
    assert again.status_code == 409 and again.json()["code"] == "license_already_revoked"
    download = _cget(admin, f"/licenses/{licence['id']}/file")
    assert download.status_code == 409 and download.json()["code"] == "license_revoked"
    # Jamais restaurée, même par le rôle de la console directement en SQL.
    assert (
        owner_db.scalar(text("SELECT status FROM licenses WHERE id = :id"), {"id": licence["id"]})
        == "REVOKED"
    )
    assert (
        len(
            owner_db.execute(
                text("SELECT 1 FROM audit_logs WHERE action = 'license.revoked'")
            ).all()
        )
        == 1
    )


def test_reissue_is_a_new_explicit_cycle(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, owner_db: Session
) -> None:
    old = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id), 2).json()
    response = _cpost(
        admin,
        f"/licenses/{old['id']}/reissue",
        {"reason": "Fichier compromis", "max_activations": 4},
    )
    assert response.status_code == 201, response.text
    new = response.json()
    assert new["license_number"] != old["license_number"]
    assert new["license_version"] == 2 and new["supersedes_id"] == old["id"]
    assert (new["valid_from"], new["valid_until"]) == (old["valid_from"], old["valid_until"])
    assert new["payment_id"] == old["payment_id"]
    assert new["max_activations"] == 4
    previous = _cget(admin, f"/licenses/{old['id']}").json()
    assert previous["state"] == "REVOKED" and previous["superseded_by_id"] == new["id"]
    assert _subscription(owner)["status"] == "active"

    twice = _cpost(admin, f"/licenses/{old['id']}/reissue", {"reason": "Encore"})
    assert twice.status_code == 409 and twice.json()["code"] == "license_already_reissued"

    # Réémission sans changement explicite : postes conservés.
    third = _cpost(admin, f"/licenses/{new['id']}/reissue", {"reason": "Nouveau poste"}).json()
    assert third["max_activations"] == 4 and third["license_version"] == 3


def test_revoked_licence_is_not_restored_but_can_be_reissued(
    alpha: Any, owner: Api, admin: TestClient, signing: Any
) -> None:
    old = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    _cpost(admin, f"/licenses/{old['id']}/revoke", {"reason": "Erreur de site"})
    assert _subscription(owner)["status"] == "suspended"
    new = _cpost(admin, f"/licenses/{old['id']}/reissue", {"reason": "Nouveau cycle"}).json()
    assert new["state"] == "ACTIVE" and new["supersedes_id"] == old["id"]
    assert _cget(admin, f"/licenses/{old['id']}").json()["state"] == "REVOKED"
    assert _subscription(owner)["status"] == "active"


def test_console_licence_list_filters(
    provision: Any, api_for: Any, admin: TestClient, signing: Any
) -> None:
    a = provision("alpha", plan="STANDARD")
    b = provision("beta", plan="ENTREPRISE")
    owner_a = api_for("owner@alpha.example.com")
    owner_b = api_for("owner@beta.example.com")
    la = _generate(admin, _confirmed_payment(owner_a, admin, a.subscription_id)).json()
    lb = _generate(admin, _confirmed_payment(owner_b, admin, b.subscription_id)).json()
    _cpost(admin, f"/licenses/{lb['id']}/revoke", {"reason": "Test"})

    def ids(**params: Any) -> list[str]:
        page = _cget(admin, "/licenses", params=params).json()
        return [item["id"] for item in page["items"]]

    assert set(ids()) == {la["id"], lb["id"]}
    assert ids(tenant_id=str(a.tenant_id)) == [la["id"]]
    assert ids(site_id=str(b.site_id)) == [lb["id"]]
    assert ids(plan_code="ENTREPRISE") == [lb["id"]]
    assert ids(state="ACTIVE") == [la["id"]]
    assert ids(state="REVOKED") == [lb["id"]]
    assert ids(search=la["license_number"]) == [la["id"]]
    assert ids(search="Entreprise beta") == [lb["id"]]


# --- Sécurité -----------------------------------------------------------------------------


def test_tenant_cannot_create_or_modify_licences(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, app_engine: Engine
) -> None:
    licence = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    for method, path in (
        ("post", "/licenses"),
        ("post", f"/licenses/{licence['id']}/revoke"),
        ("post", "/subscription/licenses"),
    ):
        assert getattr(owner, method)(path, json={}).status_code in (404, 405)
    # Rôle applicatif : lecture seule, même dans son propre tenant.
    for statement in (
        "UPDATE licenses SET max_activations = 999",
        "UPDATE licenses SET valid_until = '2099-12-31'",
        "DELETE FROM licenses",
        "INSERT INTO licenses (id) VALUES (gen_random_uuid())",
    ):
        with _app_session(app_engine, alpha.tenant_id) as session, pytest.raises(ProgrammingError):
            session.execute(text(statement))


def _app_session(app_engine: Engine, tenant_id: uuid.UUID) -> Session:
    session = Session(app_engine)
    set_db_context(session, tenant_id=tenant_id, user_id=None)
    return session


def test_licences_are_isolated_between_tenants(
    provision: Any, api_for: Any, admin: TestClient, signing: Any, app_engine: Engine
) -> None:
    a = provision("alpha", plan="STANDARD")
    b = provision("beta", plan="STANDARD")
    owner_a = api_for("owner@alpha.example.com")
    owner_b = api_for("owner@beta.example.com")
    _generate(admin, _confirmed_payment(owner_a, admin, a.subscription_id))
    assert owner_b.get("/subscriptions").json()[0]["license"] is None
    with _app_session(app_engine, b.tenant_id) as session:
        assert session.scalar(text("SELECT count(*) FROM licenses")) == 0
    with _app_session(app_engine, a.tenant_id) as session:
        assert session.scalar(text("SELECT count(*) FROM licenses")) == 1


def test_console_role_cannot_alter_a_licence(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, platform_engine: Engine
) -> None:
    licence = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    for statement in (
        "UPDATE licenses SET max_activations = 999",
        "UPDATE licenses SET payload = '{}'::jsonb",
        "DELETE FROM licenses",
    ):
        with platform_engine.connect() as conn, conn.begin(), pytest.raises(ProgrammingError):
            conn.execute(text(statement))
    # Révocation seulement ; un retour à ISSUED est refusé (politique RLS + déclencheur).
    with platform_engine.connect() as conn, conn.begin():
        conn.execute(
            text(
                "UPDATE licenses SET status = 'REVOKED', revoked_at = now(), "
                "revocation_reason = 'SQL' WHERE id = :id"
            ),
            {"id": licence["id"]},
        )
    with platform_engine.connect() as conn, conn.begin():
        result = conn.execute(
            text(
                "UPDATE licenses SET status = 'ISSUED', revoked_at = NULL, "
                "revocation_reason = NULL WHERE id = :id"
            ),
            {"id": licence["id"]},
        )
        assert result.rowcount == 0


def test_licence_content_is_immutable_even_for_the_owner(
    alpha: Any, owner: Api, admin: TestClient, signing: Any, owner_db: Session
) -> None:
    licence = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id)).json()
    for statement in (
        "UPDATE licenses SET max_activations = 999 WHERE id = :id",
        "UPDATE licenses SET valid_until = valid_until + 30 WHERE id = :id",
        "UPDATE licenses SET status = 'REVOKED', revoked_at = now(), revocation_reason = 'x', "
        "max_activations = 50 WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            owner_db.execute(text(statement), {"id": licence["id"]})
        owner_db.rollback()
