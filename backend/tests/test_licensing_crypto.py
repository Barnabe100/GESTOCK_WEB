"""Phase 3.3-B2 — Cryptographie des licences (ADR-0034), sans base de données.

Clés **éphémères** générées par les tests : aucune clé privée n'est versionnée, ni dans les
tests, ni ailleurs dans le dépôt (vérifié ci-dessous).
"""

import base64
import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.console.signing import SIGN_PATH, SigningClient
from app.core.errors import BadGatewayError, ServiceUnavailableError
from app.platform.licensing.canonical import CanonicalError, canonical_bytes
from app.platform.licensing.keyring import (
    DEFAULT_KEYRING,
    KeyringError,
    LicenseVerificationError,
    load_keyring,
    parse_keyring,
    verify_document,
)

# Vecteur de référence partagé avec le Signing Service (signing-service/tests).
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
SECRET = b"c" * 48
REPO = Path(__file__).resolve().parents[2]


def public_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def keyring_for(*entries: tuple[str, Ed25519PrivateKey, str]) -> Any:
    return parse_keyring(
        {
            "keys": [
                {
                    "key_id": key_id,
                    "algorithm": "Ed25519",
                    "public_key": public_b64(key),
                    "status": status,
                }
                for key_id, key, status in entries
            ]
        }
    )


def sign(key: Ed25519PrivateKey, key_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": "stockmanager-license",
        "version": 1,
        "key_id": key_id,
        "payload": payload,
    }
    document["signature"] = base64.b64encode(key.sign(canonical_bytes(document))).decode()
    return document


PAYLOAD = {"license_id": "x", "max_activations": 3, "modules": ["sales"], "limits": {"a": None}}


# --- Forme canonique et vérification ----------------------------------------------------------


def test_canonical_reference_vector_matches_signing_service() -> None:
    assert canonical_bytes(REFERENCE_DOCUMENT) == REFERENCE_BYTES
    service_copy = REPO / "signing-service" / "signing_service" / "canonical.py"
    backend_copy = REPO / "backend" / "app" / "platform" / "licensing" / "canonical.py"

    def code(path: Path) -> str:
        text = path.read_text()
        return text[text.index('"""', 3) :]  # sans la docstring (qui cite l'autre copie)

    assert code(service_copy) == code(backend_copy)


def test_canonical_rejects_floats_and_unknown_types() -> None:
    with pytest.raises(CanonicalError):
        canonical_bytes({"payload": {"price": 10.5}})
    with pytest.raises(CanonicalError):
        canonical_bytes({"payload": {"when": object()}})


def test_verify_valid_and_tampered_documents() -> None:
    key = Ed25519PrivateKey.generate()
    keyring = keyring_for(("k-active", key, "active"))
    document = sign(key, "k-active", PAYLOAD)
    assert verify_document(document, keyring, require_active_key=True) == PAYLOAD
    # Ordre des clés et espaces indifférents (fichier .lic indenté).
    assert verify_document(json.loads(json.dumps(document, indent=2)), keyring) == PAYLOAD

    for tampered in (
        document | {"payload": PAYLOAD | {"max_activations": 99}},
        document | {"payload": PAYLOAD | {"valid_until": "2099-12-31"}},
        document | {"signature": base64.b64encode(b"x" * 64).decode()},
        document | {"signature": "pas du base64 !"},
    ):
        with pytest.raises(LicenseVerificationError) as info:
            verify_document(tampered, keyring)
        assert info.value.code == "license_signature_invalid"


def test_verify_rejects_unknown_keys_and_formats() -> None:
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    keyring = keyring_for(("k-active", key, "active"))
    with pytest.raises(LicenseVerificationError) as info:
        verify_document(sign(other, "k-unknown", PAYLOAD), keyring)
    assert info.value.code == "license_key_unknown"
    # Même key_id, autre clé privée : signature invalide.
    with pytest.raises(LicenseVerificationError) as info:
        verify_document(sign(other, "k-active", PAYLOAD), keyring)
    assert info.value.code == "license_signature_invalid"
    document = sign(key, "k-active", PAYLOAD)
    for bad in (
        document | {"format": "autre"},
        document | {"version": 2},
        document | {"extra": 1},
        {k: v for k, v in document.items() if k != "key_id"},
        [document],
    ):
        with pytest.raises(LicenseVerificationError) as info:
            verify_document(bad, keyring)
        assert info.value.code == "license_format_invalid"


def test_retired_key_still_verifies_but_cannot_sign_new_licences() -> None:
    old = Ed25519PrivateKey.generate()
    keyring = keyring_for(("k-2025", old, "retired"))
    document = sign(old, "k-2025", PAYLOAD)
    assert verify_document(document, keyring) == PAYLOAD
    with pytest.raises(LicenseVerificationError) as info:
        verify_document(document, keyring, require_active_key=True)
    assert info.value.code == "license_key_retired"


def test_keyring_validation() -> None:
    key = Ed25519PrivateKey.generate()
    good = {"key_id": "k-1", "algorithm": "Ed25519", "public_key": public_b64(key)}
    for bad in (
        good | {"status": "active", "key_id": "K 1"},
        good | {"status": "active", "algorithm": "RSA"},
        good | {"status": "unknown"},
        good | {"status": "active", "public_key": "AAAA"},
        good | {"status": "active", "private_key": "…"},
    ):
        with pytest.raises(KeyringError):
            parse_keyring({"keys": [bad]})
    with pytest.raises(KeyringError):
        parse_keyring({"keys": [good | {"status": "active"}, good | {"status": "retired"}]})


# --- Aucune clé privée dans le dépôt ----------------------------------------------------------


def test_versioned_keyring_contains_public_keys_only() -> None:
    keyring = load_keyring(DEFAULT_KEYRING)
    assert all(len(k.key_id) > 2 for k in keyring.keys.values())
    assert "PRIVATE" not in DEFAULT_KEYRING.read_text()


def test_no_private_key_is_versioned() -> None:
    files = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    needle = b"PRIVATE" + b" KEY-----"
    offenders = []
    for name in filter(None, files):
        path = REPO / name.decode()
        if not path.is_file():
            continue
        if path.suffix in {".pem", ".key", ".p8"} or needle in path.read_bytes():
            offenders.append(name.decode())
    assert offenders == []


# --- Client du Signing Service ------------------------------------------------------------------


class FakeSigningService:
    """Émule le Signing Service (HMAC, horodatage, signature Ed25519) avec une clé éphémère."""

    def __init__(self, key: Ed25519PrivateKey, key_id: str = "k-active") -> None:
        self.key = key
        self.key_id = key_id
        self.calls: list[dict[str, str]] = []
        self.override: tuple[int, bytes] | None = None

    def __call__(
        self, url: str, body: bytes, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append(headers)
        assert url.endswith(SIGN_PATH)
        digest = hashlib.sha256(body).hexdigest()
        message = (
            f"POST\n{SIGN_PATH}\n{headers['X-Signing-Timestamp']}\n"
            f"{headers['X-Signing-Nonce']}\n{digest}"
        ).encode()
        expected = hmac.new(SECRET, message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers["X-Signing-Signature"]):
            return 401, b'{"detail":"unauthorized"}'
        assert abs(int(headers["X-Signing-Timestamp"]) - time.time()) < 60
        if self.override:
            return self.override
        payload = json.loads(body)["payload"]
        return 200, json.dumps(sign(self.key, self.key_id, payload)).encode()


def client_for(fake: FakeSigningService, keyring: Any, secret: bytes = SECRET) -> SigningClient:
    return SigningClient(
        base_url="http://signing.internal", secret=secret, keyring=keyring, transport=fake
    )


def test_signing_client_authenticates_and_verifies() -> None:
    key = Ed25519PrivateKey.generate()
    fake = FakeSigningService(key)
    keyring = keyring_for(("k-active", key, "active"))
    document = client_for(fake, keyring).sign(PAYLOAD)
    assert document["payload"] == PAYLOAD
    assert verify_document(document, keyring) == PAYLOAD
    # Nonce unique par demande.
    client_for(fake, keyring).sign(PAYLOAD)
    assert fake.calls[0]["X-Signing-Nonce"] != fake.calls[1]["X-Signing-Nonce"]


def test_signing_client_rejects_invalid_responses() -> None:
    key = Ed25519PrivateKey.generate()
    keyring = keyring_for(("k-active", key, "active"))

    fake = FakeSigningService(key)
    with pytest.raises(BadGatewayError) as bad:
        client_for(fake, keyring, secret=b"z" * 48).sign(PAYLOAD)
    assert bad.value.code == "signing_failed"

    # Document signé par une clé inconnue du trousseau.
    rogue = FakeSigningService(Ed25519PrivateKey.generate(), key_id="k-rogue")
    with pytest.raises(BadGatewayError) as bad:
        client_for(rogue, keyring).sign(PAYLOAD)
    assert bad.value.code == "license_key_unknown"

    # Signature valide d'un AUTRE payload (substitution).
    fake.override = (200, json.dumps(sign(key, "k-active", PAYLOAD | {"x": 1})).encode())
    with pytest.raises(BadGatewayError) as bad:
        client_for(fake, keyring).sign(PAYLOAD)
    assert bad.value.code == "license_signature_invalid"

    fake.override = (200, b"not json")
    with pytest.raises(BadGatewayError):
        client_for(fake, keyring).sign(PAYLOAD)

    fake.override = (503, b"")
    with pytest.raises(ServiceUnavailableError):
        client_for(fake, keyring).sign(PAYLOAD)

    def unreachable(*_: Any) -> tuple[int, bytes]:
        raise OSError("connexion refusée")

    with pytest.raises(ServiceUnavailableError):
        SigningClient("http://x", SECRET, keyring, transport=unreachable).sign(PAYLOAD)
