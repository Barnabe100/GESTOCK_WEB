"""Signing Service RÉEL (ADR-0034) — intégration de bout en bout avec le backend.

Le service du dépôt (``signing-service/``) est démarré pour ce module dans un processus séparé,
avec une paire Ed25519 ÉPHÉMÈRE et un secret HMAC générés HORS du dépôt (répertoire temporaire) :
aucune clé versionnée, aucun secret de production, aucune signature fabriquée. Le backend
(client de la console, contrôle ``stockmanager signing check``) lui parle en HTTP comme en
production : HMAC, horodatage, nonce à usage unique, vérification du document par le trousseau.

Prérequis : ``uv sync --frozen`` dans ``signing-service/`` (fait par la CI). Sans lui, ces tests
ÉCHOUENT (ils ne sont jamais ignorés).
"""

import hashlib
import hmac
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.cli import main
from app.console.signing import SIGN_PATH, SigningClient, request_signature
from app.console.signing_check import SigningConfigurationError, check_signing, probe_payload
from app.core.config import Settings
from app.core.errors import BadGatewayError, ServiceUnavailableError
from app.platform.licensing.keyring import (
    LicenseVerificationError,
    load_keyring,
    verify_document,
)

REPO = Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO / "signing-service"
KEY_ID = "test-ed25519-live"


@dataclass(frozen=True)
class LiveService:
    url: str
    secret: str
    keyring_file: Path
    workdir: Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _uv(*args: str, env: dict[str, str] | None = None, **kw: Any) -> Any:
    return subprocess.run(
        ["uv", "run", "--frozen", *args],
        cwd=SERVICE_DIR,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
        **kw,
    )


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveService]:
    workdir = tmp_path_factory.mktemp("signing-live")
    assert not str(workdir).startswith(str(REPO))  # jamais dans le dépôt
    keygen = _uv("signing-service-keygen", "--key-id", KEY_ID, "--out", str(workdir / "key.pem"))
    assert keygen.returncode == 0, keygen.stderr
    keyring = workdir / "public_keys.toml"
    keyring.write_text(
        "\n".join(line for line in keygen.stdout.splitlines() if not line.startswith("#"))
    )
    secret = os.urandom(48).hex()
    (workdir / "secret").write_text(secret)
    (workdir / "secret").chmod(0o600)
    port = _free_port()
    env = os.environ | {
        "SIGNING_KEY_ID": KEY_ID,
        "SIGNING_PRIVATE_KEY_FILE": str(workdir / "key.pem"),
        "SIGNING_CLIENT_SECRET_FILE": str(workdir / "secret"),
    }
    log = (workdir / "service.log").open("w")
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "--frozen",
            "uvicorn",
            "--factory",
            "signing_service.app:create_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=SERVICE_DIR,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=1) as response:  # noqa: S310
                    assert json.loads(response.read())["key_id"] == KEY_ID
                    break
            except (OSError, urllib.error.URLError):
                if process.poll() is not None or time.monotonic() > deadline:
                    log.flush()
                    raise AssertionError(
                        "Signing Service non démarré : " + (workdir / "service.log").read_text()
                    ) from None
                time.sleep(0.25)
        yield LiveService(url=url, secret=secret, keyring_file=keyring, workdir=workdir)
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


def _settings(live: LiveService, **over: Any) -> Settings:
    values: dict[str, Any] = {
        "signing_service_url": live.url,
        "signing_client_secret": live.secret,
        "license_public_keys_file": live.keyring_file,
        **over,
    }
    return Settings(**values)


def _client(live: LiveService, secret: str | None = None) -> SigningClient:
    return SigningClient(
        base_url=live.url,
        secret=(secret or live.secret).encode(),
        keyring=load_keyring(live.keyring_file),
    )


def _payload(**over: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {**probe_payload(now.date(), now), **over}


# --- Disponibilité réelle et contrôle d'exploitation ----------------------------------------


def test_check_signs_and_verifies_against_the_real_service(live: LiveService) -> None:
    result = check_signing(_settings(live))
    assert (result.key_id, result.active_keys, result.base_url) == (KEY_ID, (KEY_ID,), live.url)
    # Même contrôle par la CLI (supervision, déploiement) : code de sortie 0.
    assert main(["signing", "check"], _settings(live)) == 0


def test_check_fails_explicitly_on_every_misconfiguration(
    live: LiveService, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Configuration absente : jamais de signature ni de clé générée silencieusement.
    with pytest.raises(SigningConfigurationError) as missing:
        check_signing(_settings(live, signing_service_url=None, signing_client_secret=None))
    assert missing.value.extra["missing"] == ["SM_SIGNING_SERVICE_URL", "SM_SIGNING_CLIENT_SECRET"]
    assert main(["signing", "check"], _settings(live, signing_service_url=None)) == 1
    assert live.secret not in capsys.readouterr().err  # aucun secret affiché
    # Trousseau sans clé active (ex. trousseau versionné vide) : refus.
    empty = tmp_path / "empty.toml"
    empty.write_text("keys = []\n")
    with pytest.raises(SigningConfigurationError, match="ACTIVE"):
        check_signing(_settings(live, license_public_keys_file=empty))
    # Secret HMAC différent : demande refusée par le service (401 → signing_failed).
    with pytest.raises(BadGatewayError) as wrong:
        check_signing(_settings(live, signing_client_secret="x" * 48))
    assert (wrong.value.code, wrong.value.extra["status"]) == ("signing_failed", 401)
    # Service injoignable : 503, rien n'est enregistré.
    with pytest.raises(ServiceUnavailableError):
        check_signing(_settings(live, signing_service_url=f"http://127.0.0.1:{_free_port()}"))


def test_a_key_absent_from_the_keyring_is_never_accepted(live: LiveService, tmp_path: Path) -> None:
    # Le service signe avec SA clé ; un trousseau qui ne la contient pas la refuse.
    other = tmp_path / "other.pem"
    keygen = _uv("signing-service-keygen", "--key-id", "other-ed25519", "--out", str(other))
    assert keygen.returncode == 0, keygen.stderr
    keyring = tmp_path / "other.toml"
    keyring.write_text(
        "\n".join(line for line in keygen.stdout.splitlines() if not line.startswith("#"))
    )
    with pytest.raises(BadGatewayError) as refused:
        check_signing(_settings(live, license_public_keys_file=keyring))
    assert refused.value.code == "license_key_unknown"


# --- Émission, vérification, altération -----------------------------------------------------


def test_a_real_licence_verifies_and_any_alteration_is_rejected(live: LiveService) -> None:
    payload = _payload(
        plan="STANDARD",
        modules=["catalog", "restaurant.recipes", "sales"],
        max_activations=3,
        valid_until=(datetime.now(UTC).date() + timedelta(days=30)).isoformat(),
    )
    document = _client(live).sign(payload)
    keyring = load_keyring(live.keyring_file)
    assert verify_document(document, keyring, require_active_key=True) == payload
    assert document["key_id"] == KEY_ID
    # Contenu modifié après signature (postes, période, modules) : rejeté.
    for field, value in (
        ("max_activations", 999),
        ("valid_until", "2099-12-31"),
        ("modules", ["catalog", "restaurant.recipes", "sales", "stock"]),
    ):
        tampered = json.loads(json.dumps(document))
        tampered["payload"][field] = value
        with pytest.raises(LicenseVerificationError):
            verify_document(tampered, keyring)
    # Signature altérée ou clé annoncée inconnue : rejetées.
    forged = json.loads(json.dumps(document))
    forged["signature"] = forged["signature"][::-1]
    with pytest.raises(LicenseVerificationError):
        verify_document(forged, keyring)
    unknown = json.loads(json.dumps(document))
    unknown["key_id"] = "other-ed25519"
    with pytest.raises(LicenseVerificationError):
        verify_document(unknown, keyring)


def test_the_service_never_signs_an_unauthenticated_or_malformed_request(
    live: LiveService,
) -> None:
    def post(body: bytes, headers: dict[str, str]) -> int:
        request = urllib.request.Request(  # noqa: S310
            live.url + SIGN_PATH, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    body = json.dumps({"payload": _payload()}, separators=(",", ":")).encode()

    def signed(nonce: str, timestamp: str | None = None, data: bytes = body) -> dict[str, str]:
        ts = timestamp or str(int(time.time()))
        return {
            "Content-Type": "application/json",
            "X-Signing-Timestamp": ts,
            "X-Signing-Nonce": nonce,
            "X-Signing-Signature": request_signature(
                live.secret.encode(), "POST", SIGN_PATH, ts, nonce, data
            ),
        }

    # Sans authentification, avec une empreinte fausse, ou horodatage hors tolérance : 401.
    assert post(body, {"Content-Type": "application/json"}) == 401
    bad = signed("nonce-bad-000001") | {
        "X-Signing-Signature": hmac.new(b"autre-secret", b"x", hashlib.sha256).hexdigest()
    }
    assert post(body, bad) == 401
    assert post(body, signed("nonce-old-000001", str(int(time.time()) - 3600))) == 401
    # Demande valide : signée ; rejouée telle quelle (même nonce) : refusée.
    headers = signed("nonce-once-00001")
    assert post(body, headers) == 200
    assert post(body, headers) == 401
    # Contenu non conforme (champ inconnu, flottant) : jamais signé.
    for index, payload in enumerate((_payload(price=10), _payload(max_activations=1.5))):
        data = json.dumps({"payload": payload}, separators=(",", ":")).encode()
        assert post(data, signed(f"nonce-invalid-{index:04d}", data=data)) == 422


def test_no_key_material_is_written_in_the_repository(live: LiveService) -> None:
    for path in (live.keyring_file, live.workdir / "key.pem", live.workdir / "secret"):
        assert not str(path.resolve()).startswith(str(REPO))
    assert (live.workdir / "key.pem").stat().st_mode & 0o077 == 0  # 0600
    # La clé de test n'est jamais dans le trousseau versionné.
    assert KEY_ID not in load_keyring(None).keys
