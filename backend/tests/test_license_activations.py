# Les fixtures partagées sont importées de test_licenses (paramètres homonymes attendus).
# ruff: noqa: F811
"""Phase 3.3-B3 — Postes (activations) des sites (ADR-0035).

Activation d'une installation cliente sous la licence en vigueur du site sélectionné, quota
``max_activations`` par abonnement de site (verrou : la dernière place n'est prise qu'une fois),
idempotence, refus distincts et journalisés, contrôle de présence, libération (une place,
rien d'autre), libération par TechNova (double audit), sécurité (permissions, RLS, droits SQL,
finalité).
"""

import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session

from app.platform.context import get_now
from app.shared.clock import utcnow
from tests.conftest import CONSOLE_HEADERS, CONSOLE_PREFIX, Api, _sites_engine, add_site
from tests.test_licenses import (  # noqa: F401  (fixtures)
    _confirmed_payment,
    _cpost,
    _generate,
    _subscription,
    admin,
    alpha,
    owner,
    signing,
)
from tests.test_subscription_payments import _member


def _site_api(api: Api, site_id: Any) -> Api:
    return Api(api.client, api.token, site_id=uuid.UUID(str(site_id)))


def _activate(api: Api, installation: uuid.UUID | None = None, **fields: Any) -> Any:
    return api.post(
        "/license-activations",
        json={
            "installation_id": str(installation or uuid.uuid4()),
            "label": fields.pop("label", "Caisse 1"),
            "client_version": fields.pop("client_version", "1.0.0"),
        }
        | fields,
    )


@pytest.fixture
def licensed(alpha: Any, owner: Api, admin: TestClient, signing: Any) -> dict[str, Any]:
    """Site d'alpha sous licence en vigueur (2 postes)."""
    licence = _generate(admin, _confirmed_payment(owner, admin, alpha.subscription_id), 2).json()
    return licence  # type: ignore[no-any-return]


def _age_licence(license_id: str, *, back: bool = False) -> None:
    shift = "- interval '400 days'" if not back else "+ interval '400 days'"
    days = "- 400" if not back else "+ 400"
    with _sites_engine().begin() as conn:
        conn.execute(text("ALTER TABLE licenses DISABLE TRIGGER licenses_final"))
        conn.execute(
            text(
                f"UPDATE licenses SET starts_at = starts_at {shift}, ends_at = ends_at {shift}, "
                f"valid_from = valid_from {days}, valid_until = valid_until {days} WHERE id = :id"
            ),
            {"id": license_id},
        )
        conn.execute(text("ALTER TABLE licenses ENABLE TRIGGER licenses_final"))


def _audit(owner_db: Session, action: str) -> list[Any]:
    return list(
        owner_db.execute(
            text("SELECT user_id, site_id, data FROM audit_logs WHERE action = :a"), {"a": action}
        ).all()
    )


# --- Activation -----------------------------------------------------------------------------


def test_activation_requires_a_selected_site(owner: Api, licensed: Any) -> None:
    response = _activate(owner)
    assert response.status_code == 422
    assert response.json()["code"] == "site_required"


def test_activation_without_licence_is_refused_and_logged(
    alpha: Any, owner: Api, owner_db: Session
) -> None:
    response = _activate(_site_api(owner, alpha.site_id))
    assert response.status_code == 409
    assert response.json()["code"] == "license_missing"
    failures = _audit(owner_db, "license.activation_failed")
    assert len(failures) == 1 and failures[0].data["code"] == "license_missing"
    assert owner_db.scalar(text("SELECT count(*) FROM license_activations")) == 0


def test_activation_consumes_a_slot_and_is_idempotent(
    alpha: Any, owner: Api, licensed: Any, owner_db: Session
) -> None:
    site = _site_api(owner, alpha.site_id)
    installation = uuid.uuid4()
    created = _activate(site, installation, license_id=licensed["id"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "ACTIVE" and body["license_id"] == licensed["id"]
    assert body["stale"] is False

    replay = _activate(site, installation, label="Caisse principale", client_version="1.1.0")
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]
    assert replay.json()["label"] == "Caisse principale"
    assert owner_db.scalar(text("SELECT count(*) FROM license_activations")) == 1

    summary = _subscription(owner)["license"]
    assert (summary["max_activations"], summary["activations_used"]) == (2, 1)
    assert summary["activations_available"] == 1
    created_logs = _audit(owner_db, "license.activation.created")
    assert len(created_logs) == 1 and created_logs[0].site_id == alpha.site_id


def test_quota_is_enforced_per_site(alpha: Any, owner: Api, licensed: Any) -> None:
    site = _site_api(owner, alpha.site_id)
    assert _activate(site).status_code == 201
    assert _activate(site).status_code == 201
    refused = _activate(site)
    assert refused.status_code == 409
    problem = refused.json()
    assert problem["code"] == "activation_quota_reached"
    assert problem["max_activations"] == 2 and problem["used"] == 2
    assert "nombre maximal de postes" in problem["detail"]
    assert _subscription(owner)["license"]["activations_available"] == 0


def test_concurrent_activations_never_exceed_the_quota(
    alpha: Any, owner: Api, licensed: Any, client: TestClient, owner_db: Session
) -> None:
    statuses: list[int] = []

    def activate() -> None:
        with TestClient(client.app) as c:
            api = Api(c, owner.token, site_id=alpha.site_id)
            statuses.append(_activate(api).status_code)

    threads = [threading.Thread(target=activate) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [201, 201, 409, 409, 409]
    active = owner_db.scalar(
        text("SELECT count(*) FROM license_activations WHERE status = 'ACTIVE'")
    )
    assert active == 2


def test_concurrent_identical_activations_create_one_poste(
    alpha: Any, owner: Api, licensed: Any, client: TestClient, owner_db: Session
) -> None:
    installation = uuid.uuid4()
    statuses: list[int] = []

    def activate() -> None:
        with TestClient(client.app) as c:
            statuses.append(
                _activate(Api(c, owner.token, site_id=alpha.site_id), installation).status_code
            )

    threads = [threading.Thread(target=activate) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(statuses) == [200, 200, 200, 201]
    assert owner_db.scalar(text("SELECT count(*) FROM license_activations")) == 1


# --- Libération -----------------------------------------------------------------------------


def test_release_frees_a_slot_and_nothing_else(
    alpha: Any, owner: Api, licensed: Any, owner_db: Session
) -> None:
    site = _site_api(owner, alpha.site_id)
    installation = uuid.uuid4()
    first = _activate(site, installation).json()
    _activate(site)
    before = _subscription(owner)

    released = site.post(
        f"/license-activations/{first['id']}/release", json={"reason": "Poste remplacé"}
    )
    assert released.status_code == 200, released.text
    assert released.json()["status"] == "RELEASED"
    assert released.json()["release_source"] == "TENANT"
    after = _subscription(owner)
    # Une place libérée ; licence, période et abonnement inchangés.
    assert after["license"]["activations_used"] == 1
    for key in ("status", "current_period_start", "current_period_end"):
        assert after[key] == before[key]
    for key in ("license_number", "valid_from", "valid_until", "state", "max_activations"):
        assert after["license"][key] == before["license"][key]

    again = site.post(f"/license-activations/{first['id']}/release", json={"reason": "Encore"})
    assert again.status_code == 409 and again.json()["code"] == "activation_already_released"
    # Réactivation : nouvelle activation, même licence.
    reactivated = _activate(site, installation)
    assert reactivated.status_code == 201
    assert reactivated.json()["id"] != first["id"]
    assert reactivated.json()["license_id"] == licensed["id"]
    assert len(_audit(owner_db, "license.activation.released")) == 1


def test_reduced_quota_tolerates_existing_postes_but_refuses_new_ones(
    alpha: Any, owner: Api, admin: TestClient, licensed: Any
) -> None:
    site = _site_api(owner, alpha.site_id)
    _activate(site)
    _activate(site)
    reissued = _cpost(
        admin,
        f"/licenses/{licensed['id']}/reissue",
        {"reason": "Réduction commerciale", "max_activations": 1},
    ).json()
    summary = _subscription(owner)["license"]
    assert summary["id"] == reissued["id"]
    assert (summary["activations_used"], summary["activations_available"]) == (2, 0)
    assert _activate(site).json()["code"] == "activation_quota_reached"


# --- Refus distincts --------------------------------------------------------------------------


def test_revoked_expired_and_superseded_licences_are_refused(
    app: Any, alpha: Any, owner: Api, admin: TestClient, licensed: Any
) -> None:
    site = _site_api(owner, alpha.site_id)
    new = _cpost(admin, f"/licenses/{licensed['id']}/reissue", {"reason": "Réémission"}).json()
    # Ancienne licence (révoquée par la réémission) présentée par le client.
    assert _activate(site, license_id=licensed["id"]).json()["code"] == "license_revoked"
    assert _activate(site, license_id=str(uuid.uuid4())).json()["code"] == "license_invalid"
    assert _activate(site, license_id=new["id"]).status_code == 201

    # Licence échue (période déplacée dans le passé, déclencheur suspendu le temps du test).
    _age_licence(new["id"])
    expired = _activate(site)
    assert expired.status_code == 409 and expired.json()["code"] == "license_expired"
    _age_licence(new["id"], back=True)

    _cpost(admin, f"/licenses/{new['id']}/revoke", {"reason": "Fraude"})
    assert _activate(site).json()["code"] == "license_revoked"


def test_wrong_site_and_installation_active_elsewhere(
    alpha: Any, owner: Api, admin: TestClient, licensed: Any
) -> None:
    second = add_site(owner, "Dépôt", "DEP", plan="STANDARD", active=False).json()
    subscription_b = next(
        s for s in owner.get("/subscriptions").json() if s["site"]["code"] == "DEP"
    )
    _generate(admin, _confirmed_payment(owner, admin, subscription_b["id"], "V-DEP"), 1)
    site_a = _site_api(owner, alpha.site_id)
    site_b = _site_api(owner, second["id"])

    wrong = _activate(site_b, license_id=licensed["id"])
    assert wrong.status_code == 409 and wrong.json()["code"] == "license_wrong_site"

    installation = uuid.uuid4()
    assert _activate(site_a, installation).status_code == 201
    elsewhere = _activate(site_b, installation)
    assert elsewhere.status_code == 409
    assert elsewhere.json()["code"] == "installation_active_elsewhere"
    # Quotas indépendants : le site B garde sa place.
    assert _activate(site_b).status_code == 201


# --- Contrôle de présence ----------------------------------------------------------------------


def test_check_in_reports_licence_and_offline_grace(
    app: Any, alpha: Any, owner: Api, licensed: Any
) -> None:
    site = _site_api(owner, alpha.site_id)
    installation = uuid.uuid4()
    _activate(site, installation)
    checked = site.post(
        "/license-activations/check-in", json={"installation_id": str(installation)}
    )
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["license"]["license_number"] == licensed["license_number"]
    assert body["license"]["activations_used"] == 1
    assert body["offline_grace_days"] == 7
    unknown = site.post(
        "/license-activations/check-in", json={"installation_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "activation_not_found"

    # Poste non vu depuis plus que la durée tolérée : signalé.
    app.dependency_overrides[get_now] = lambda: utcnow() + timedelta(days=10)
    try:
        listing = site.get("/license-activations").json()["items"]
    finally:
        app.dependency_overrides.pop(get_now, None)
    assert listing[0]["stale"] is True


# --- Sécurité ---------------------------------------------------------------------------------


def test_permissions_seller_checks_in_but_cannot_activate(
    alpha: Any, owner: Api, licensed: Any, client: TestClient
) -> None:
    installation = uuid.uuid4()
    _activate(_site_api(owner, alpha.site_id), installation)
    seller = _site_api(_member(owner, client, "vendeur@alpha.example.com", "seller"), alpha.site_id)
    assert _activate(seller).status_code == 403
    assert (
        seller.post(
            "/license-activations/check-in", json={"installation_id": str(installation)}
        ).status_code
        == 200
    )
    viewer = _site_api(_member(owner, client, "consult@alpha.example.com", "viewer"), alpha.site_id)
    assert (
        viewer.post(
            "/license-activations/check-in", json={"installation_id": str(installation)}
        ).status_code
        == 403
    )


def test_activations_are_isolated_between_tenants(
    provision: Any, api_for: Any, alpha: Any, owner: Api, licensed: Any, app_engine: Engine
) -> None:
    activation = _activate(_site_api(owner, alpha.site_id)).json()
    b = provision("beta", plan="STANDARD")
    owner_b = api_for("owner@beta.example.com")
    assert owner_b.get("/license-activations").json()["total"] == 0
    released = owner_b.post(
        f"/license-activations/{activation['id']}/release", json={"reason": "Intrusion"}
    )
    assert released.status_code == 404
    with Session(app_engine) as session:
        from app.core.db import set_db_context

        set_db_context(session, tenant_id=b.tenant_id, user_id=None)
        assert session.scalar(text("SELECT count(*) FROM license_activations")) == 0
        for statement in (
            "DELETE FROM license_activations",
            "UPDATE license_activations SET site_id = gen_random_uuid()",
        ):
            with pytest.raises(ProgrammingError):
                session.execute(text(statement))
            session.rollback()


def test_released_poste_is_final_even_for_the_owner(
    alpha: Any, owner: Api, licensed: Any, owner_db: Session
) -> None:
    site = _site_api(owner, alpha.site_id)
    activation = _activate(site).json()
    site.post(f"/license-activations/{activation['id']}/release", json={"reason": "Fin"})
    for statement in (
        "UPDATE license_activations SET status = 'ACTIVE', released_at = NULL, "
        "release_reason = NULL, release_source = NULL WHERE id = :id",
        "UPDATE license_activations SET label = 'x' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            owner_db.execute(text(statement), {"id": activation["id"]})
        owner_db.rollback()


# --- Console TechNova ----------------------------------------------------------------------------


def test_technova_releases_a_poste_with_double_audit(
    alpha: Any,
    owner: Api,
    admin: TestClient,
    licensed: Any,
    owner_db: Session,
    platform_engine: Engine,
) -> None:
    activation = _activate(_site_api(owner, alpha.site_id), label="Poste perdu").json()
    listing = admin.get(
        f"{CONSOLE_PREFIX}/activations", params={"subscription_id": str(alpha.subscription_id)}
    ).json()
    assert [a["id"] for a in listing["items"]] == [activation["id"]]
    assert listing["items"][0]["license_number"] == licensed["license_number"]
    assert "activated_by" not in listing["items"][0]
    assert admin.get(f"{CONSOLE_PREFIX}/licenses/{licensed['id']}").json()["activations_used"] == 1

    # Rôle de la console : ni autre colonne, ni suppression.
    for statement in (
        "UPDATE license_activations SET label = 'x'",
        "DELETE FROM license_activations",
    ):
        with platform_engine.connect() as conn, conn.begin(), pytest.raises(ProgrammingError):
            conn.execute(text(statement))

    released = admin.post(
        f"{CONSOLE_PREFIX}/activations/{activation['id']}/release",
        json={"reason": "Ordinateur volé"},
        headers=CONSOLE_HEADERS,
    )
    assert released.status_code == 200, released.text
    assert released.json()["release_source"] == "TECHNOVA"
    again = admin.post(
        f"{CONSOLE_PREFIX}/activations/{activation['id']}/release",
        json={"reason": "Encore"},
        headers=CONSOLE_HEADERS,
    )
    assert again.status_code == 409
    tenant_view = owner.get("/license-activations").json()["items"][0]
    assert tenant_view["release_source"] == "TECHNOVA"
    platform = owner_db.execute(
        text("SELECT reason FROM platform_audit_logs WHERE action = 'license.activation.released'")
    ).all()
    mirror = _audit(owner_db, "license.activation.released")
    assert len(platform) == 1 and platform[0].reason == "Ordinateur volé"
    assert len(mirror) == 1 and mirror[0].user_id is None
    assert mirror[0].data["actor"] == "technova"
