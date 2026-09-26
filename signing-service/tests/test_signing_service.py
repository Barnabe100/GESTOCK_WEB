"""Tests du Signing Service — clés **éphémères** générées pour chaque test, jamais versionnées."""

import base64
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi.testclient import TestClient

from signing_service.app import SIGN_PATH, create_app
from signing_service.auth import sign_request
from signing_service.canonical import CanonicalError, canonical_bytes
from signing_service.config import Settings
from signing_service.keygen import generate, inside_git_worktree
from signing_service.keys import KeyConfigurationError

SECRET = b"s" * 48
KEY_ID = "test-ed25519-ephemeral"

# Vecteur de référence partagé avec le backend (tests/test_licensing_crypto.py).
REFERENCE_DOCUMENT = {
    "version": 1,
    "format": "stockmanager-license",
    "key_id": "k1",
    "payload": {"b": [2, 1], "a": "é", "n": None, "t": True},
    "signature": "ignorée",
}
REFERENCE_BYTES = (
    '{"format":"stockmanager-license","key_id":"k1",'
    '"payload":{"a":"é","b":[2,1],"n":null,"t":true},"version":1}'
).encode()


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "license_id": str(uuid.uuid4()),
        "license_number": "LIC-2026-00001",
        "license_version": 1,
        "supersedes_id": None,
        "tenant_id": str(uuid.uuid4()),
        "site_id": str(uuid.uuid4()),
        "subscription_id": str(uuid.uuid4()),
        "payment_id": str(uuid.uuid4()),
        "plan": "STANDARD",
        "billing_period": "annual",
        "issued_at": "2026-09-26T10:00:00Z",
        "timezone": "Africa/Ouagadougou",
        "valid_from": "2026-10-01",
        "valid_until": "2027-09-30",
        "max_activations": 3,
        "modules": ["catalog", "sales", "stock"],
        "features": ["stock.transfers"],
        "limits": {"max_sites": 1, "max_users": None},
        "compatibility": {"products": ["stockmanager-desktop", "stockmanager-web"]},
    }
    return base | overrides


@pytest.fixture
def keypair(tmp_path: Path) -> tuple[Ed25519PublicKey, Settings]:
    key = Ed25519PrivateKey.generate()
    key_file = tmp_path / "private.pem"
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    secret_file = tmp_path / "secret"
    secret_file.write_bytes(SECRET)
    secret_file.chmod(0o600)
    settings = Settings(key_id=KEY_ID, private_key_file=key_file, client_secret_file=secret_file)
    return key.public_key(), settings


@pytest.fixture
def client(keypair: tuple[Ed25519PublicKey, Settings]) -> TestClient:
    return TestClient(create_app(keypair[1]))


def signed_post(
    client: TestClient,
    body: dict[str, Any] | bytes,
    *,
    secret: bytes = SECRET,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> Any:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    ts = str(int(time.time()) if timestamp is None else timestamp)
    nonce = nonce or uuid.uuid4().hex
    return client.post(
        SIGN_PATH,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signing-Timestamp": ts,
            "X-Signing-Nonce": nonce,
            "X-Signing-Signature": sign_request(secret, "POST", SIGN_PATH, ts, nonce, raw),
        },
    )


# --- Forme canonique ---------------------------------------------------------------------------


def test_canonical_reference_vector() -> None:
    assert canonical_bytes(REFERENCE_DOCUMENT) == REFERENCE_BYTES


def test_canonical_rejects_floats() -> None:
    with pytest.raises(CanonicalError):
        canonical_bytes({"payload": {"amount": 1.5}})


# --- Signature ---------------------------------------------------------------------------------


def test_health_exposes_only_key_id(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "key_id": KEY_ID}


def test_sign_returns_verifiable_document(
    client: TestClient, keypair: tuple[Ed25519PublicKey, Settings]
) -> None:
    data = payload()
    response = signed_post(client, {"payload": data})
    assert response.status_code == 200, response.text
    document = response.json()
    assert document["format"] == "stockmanager-license"
    assert document["version"] == 1
    assert document["key_id"] == KEY_ID
    assert document["payload"] == data
    signature = base64.b64decode(document["signature"])
    keypair[0].verify(signature, canonical_bytes(document))
    tampered = document | {"payload": data | {"max_activations": 99}}
    with pytest.raises(InvalidSignature):
        keypair[0].verify(signature, canonical_bytes(tampered))


@pytest.mark.parametrize(
    "change",
    [
        {"max_activations": 0},
        {"max_activations": 2.0},
        {"max_activations": True},
        {"valid_until": "2026-09-30"},
        {"valid_from": "2026-10-1"},
        {"issued_at": "2026-09-26T10:00:00+02:00"},
        {"tenant_id": "not-a-uuid"},
        {"modules": ["stock", "catalog"]},
        {"limits": {"max_users": -1}},
        {"limits": {"max_users": 1.5}},
        {"license_number": "LIC-1"},
        {"plan": "standard; drop"},
        {"unexpected": "field"},
    ],
)
def test_sign_rejects_unauthorized_payloads(client: TestClient, change: dict[str, Any]) -> None:
    response = signed_post(client, {"payload": payload(**change)})
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_payload"


def test_sign_rejects_missing_field(client: TestClient) -> None:
    data = payload()
    del data["max_activations"]
    assert signed_post(client, {"payload": data}).status_code == 422


# --- Authentification ---------------------------------------------------------------------------


def test_unauthenticated_request_is_refused(client: TestClient) -> None:
    assert client.post(SIGN_PATH, json={"payload": payload()}).status_code == 401


def test_wrong_secret_is_refused(client: TestClient) -> None:
    response = signed_post(client, {"payload": payload()}, secret=b"x" * 48)
    assert response.status_code == 401


def test_stale_timestamp_is_refused(client: TestClient) -> None:
    response = signed_post(client, {"payload": payload()}, timestamp=int(time.time()) - 3600)
    assert response.status_code == 401


def test_replayed_request_is_refused(client: TestClient) -> None:
    raw = json.dumps({"payload": payload()}).encode()
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    headers = {
        "Content-Type": "application/json",
        "X-Signing-Timestamp": ts,
        "X-Signing-Nonce": nonce,
        "X-Signing-Signature": sign_request(SECRET, "POST", SIGN_PATH, ts, nonce, raw),
    }
    assert client.post(SIGN_PATH, content=raw, headers=headers).status_code == 200
    assert client.post(SIGN_PATH, content=raw, headers=headers).status_code == 401


def test_body_tampering_is_refused(client: TestClient) -> None:
    raw = json.dumps({"payload": payload()}).encode()
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = sign_request(SECRET, "POST", SIGN_PATH, ts, nonce, raw)
    other = json.dumps({"payload": payload(max_activations=50)}).encode()
    response = client.post(
        SIGN_PATH,
        content=other,
        headers={
            "Content-Type": "application/json",
            "X-Signing-Timestamp": ts,
            "X-Signing-Nonce": nonce,
            "X-Signing-Signature": signature,
        },
    )
    assert response.status_code == 401


# --- Configuration et journal ----------------------------------------------------------------


def test_missing_or_invalid_key_prevents_startup(
    tmp_path: Path, keypair: tuple[Ed25519PublicKey, Settings]
) -> None:
    settings = keypair[1]
    with pytest.raises(KeyConfigurationError):
        create_app(settings.model_copy(update={"private_key_file": tmp_path / "absent.pem"}))
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("not a key")
    garbage.chmod(0o600)
    with pytest.raises(KeyConfigurationError) as info:
        create_app(settings.model_copy(update={"private_key_file": garbage}))
    assert "not a key" not in str(info.value)
    short = tmp_path / "short"
    short.write_bytes(b"short")
    short.chmod(0o600)
    with pytest.raises(KeyConfigurationError):
        create_app(settings.model_copy(update={"client_secret_file": short}))
    writable = tmp_path / "writable.pem"
    writable.write_bytes(settings.private_key_file.read_bytes())
    writable.chmod(0o666)
    with pytest.raises(KeyConfigurationError):
        create_app(settings.model_copy(update={"private_key_file": writable}))


def test_logs_never_contain_secrets(
    client: TestClient,
    keypair: tuple[Ed25519PublicKey, Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    data = payload()
    document = signed_post(client, {"payload": data}).json()
    signed_post(client, {"payload": data}, secret=b"y" * 48)
    text = caplog.text
    assert data["license_id"] in text
    pem = keypair[1].private_key_file.read_text()
    for secret in (
        pem.splitlines()[1],
        SECRET.decode(),
        document["signature"],
        data["tenant_id"],
        json.dumps(data),
    ):
        assert secret not in text


def test_keygen_writes_private_key_outside_git_only(tmp_path: Path) -> None:
    out = tmp_path / "keys" / "private.pem"
    entry = generate("dev-ed25519-test", out)
    assert out.stat().st_mode & 0o777 == 0o600
    assert "PRIVATE" not in entry and 'key_id = "dev-ed25519-test"' in entry
    with pytest.raises(FileExistsError):
        generate("dev-ed25519-test", out)
    repo_path = Path(__file__).resolve().parent / "never.pem"
    assert inside_git_worktree(repo_path)
    with pytest.raises(ValueError):
        generate("dev-ed25519-test", repo_path)
    assert not repo_path.exists()
