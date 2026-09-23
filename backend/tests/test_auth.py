from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD, Api, login


def test_login_single_tenant_binds_token_to_tenant(client: TestClient, provision: Any) -> None:
    a = provision("alpha")
    response = login(client, "owner@alpha.example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == str(a.tenant_id)
    assert body["user"]["email"] == "owner@alpha.example.com"
    assert "password" not in response.text.lower().replace("must_change_password", "")
    cookie = response.headers["set-cookie"]
    assert "sm_refresh=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie


def test_email_is_case_insensitive(client: TestClient, provision: Any) -> None:
    provision("alpha")
    assert login(client, "  OWNER@Alpha.Example.COM ").status_code == 200


def test_multi_tenant_user_must_choose_tenant(client: TestClient, provision: Any) -> None:
    a = provision("alpha", owner_email="multi@example.com")
    b = provision("beta", owner_email="multi@example.com")
    body = login(client, "multi@example.com").json()
    assert body["tenant_id"] is None
    assert {m["tenant_id"] for m in body["memberships"]} == {str(a.tenant_id), str(b.tenant_id)}

    api = Api(client, body["access_token"])
    response = api.get("/me/capabilities")
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_not_selected"

    # Choix explicite via le rafraîchissement.
    refreshed = client.post("/api/v1/auth/refresh", json={"tenant_id": str(b.tenant_id)})
    assert refreshed.status_code == 200
    api = Api(client, refreshed.json()["access_token"])
    assert api.get("/me/capabilities").json()["tenant"]["id"] == str(b.tenant_id)


def test_login_rejects_tenant_without_membership(client: TestClient, provision: Any) -> None:
    provision("alpha")
    b = provision("beta")
    response = login(client, "owner@alpha.example.com", tenant_id=b.tenant_id)
    assert response.status_code == 403
    assert response.json()["code"] == "tenant_access_denied"


def test_wrong_password_and_lockout(client: TestClient, provision: Any, owner_db: Session) -> None:
    provision("alpha")
    for _ in range(4):
        assert (
            login(client, "owner@alpha.example.com", "mauvais").json()["code"]
            == "invalid_credentials"
        )
    # 5e échec : verrouillage
    assert login(client, "owner@alpha.example.com", "mauvais").status_code == 401
    locked = login(client, "owner@alpha.example.com")  # même le bon mot de passe est refusé
    assert locked.status_code == 401
    assert locked.json()["code"] == "account_locked"
    actions = (
        owner_db.execute(text("SELECT action FROM audit_logs WHERE action LIKE 'auth.login.%'"))
        .scalars()
        .all()
    )
    assert actions.count("auth.login.failed") == 6


def test_unknown_user_gets_same_error(client: TestClient) -> None:
    response = login(client, "personne@nulle.part", "x")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_must_change_password_gate(client: TestClient, provision: Any) -> None:
    provision("alpha", activate_owner=False)
    body = login(client, "owner@alpha.example.com").json()
    assert body["user"]["must_change_password"] is True
    api = Api(client, body["access_token"])
    assert api.get("/me/capabilities").json()["code"] == "password_change_required"
    assert api.get("/me").status_code == 200

    too_short = api.post("/me/password", json={"current_password": PASSWORD, "new_password": "a"})
    assert too_short.json()["code"] == "password_too_short"
    wrong = api.post("/me/password", json={"current_password": "x", "new_password": "Nouveau-456"})
    assert wrong.json()["code"] == "invalid_current_password"
    ok = api.post(
        "/me/password", json={"current_password": PASSWORD, "new_password": "Nouveau-456"}
    )
    assert ok.status_code == 204
    assert api.get("/me/capabilities").status_code == 200
    assert login(client, "owner@alpha.example.com", PASSWORD).status_code == 401
    assert login(client, "owner@alpha.example.com", "Nouveau-456").status_code == 200


def test_refresh_rotates_cookie(client: TestClient, provision: Any) -> None:
    provision("alpha")
    login(client, "owner@alpha.example.com")
    first = client.cookies.get("sm_refresh")
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    second = client.cookies.get("sm_refresh")
    assert second and second != first


def test_refresh_reuse_outside_grace_revokes_session(
    client: TestClient, provision: Any, owner_db: Session
) -> None:
    provision("alpha")
    login(client, "owner@alpha.example.com")
    stolen = client.cookies.get("sm_refresh")
    assert client.post("/api/v1/auth/refresh").status_code == 200
    # Concurrence (autre onglet) dans la fenêtre de grâce : accepté, sans nouveau cookie.
    client.cookies.set("sm_refresh", stolen, path="/api/v1/auth")
    grace = client.post("/api/v1/auth/refresh")
    assert grace.status_code == 200
    assert "set-cookie" not in grace.headers

    # Hors fenêtre de grâce : réutilisation détectée, session révoquée.
    owner_db.execute(text("UPDATE auth_sessions SET rotated_at = rotated_at - interval '1 hour'"))
    owner_db.commit()
    client.cookies.set("sm_refresh", stolen, path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").json()["code"] == "session_expired"
    revoked = owner_db.execute(text("SELECT revoked_at FROM auth_sessions")).scalar_one()
    assert revoked is not None


def test_logout_revokes_session_and_access_token(client: TestClient, provision: Any) -> None:
    provision("alpha")
    body = login(client, "owner@alpha.example.com").json()
    api = Api(client, body["access_token"])
    assert api.get("/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert api.get("/me").json()["code"] == "session_expired"
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_missing_or_invalid_token(client: TestClient) -> None:
    assert client.get("/api/v1/me").json()["code"] == "unauthorized"
    response = client.get("/api/v1/me", headers={"Authorization": "Bearer abc"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"
    assert response.headers["content-type"] == "application/problem+json"


def test_inactive_user_is_rejected(client: TestClient, provision: Any, owner_db: Session) -> None:
    provision("alpha")
    token = login(client, "owner@alpha.example.com").json()["access_token"]
    owner_db.execute(text("UPDATE users SET is_active = false"))
    owner_db.commit()
    assert Api(client, token).get("/me").json()["code"] == "account_inactive"
    assert login(client, "owner@alpha.example.com").json()["code"] == "account_inactive"
