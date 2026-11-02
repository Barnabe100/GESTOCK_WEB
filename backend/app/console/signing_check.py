"""Contrôle de bout en bout du Signing Service (ADR-0034 ; préparation de l'exploitation, R8).

Un service DÉMARRÉ n'est pas un service OPÉRATIONNEL : ce contrôle soumet une vraie demande
de signature (authentification HMAC, horodatage, nonce) puis VÉRIFIE le document reçu avec le
trousseau public configuré — exactement le chemin d'une licence réelle.

La sonde est un payload conforme pour une entreprise NULLE (``00000000-…``), plan ``PROBE``,
valable un jour : le document signé est vérifié puis jeté, jamais enregistré ni remis à qui que
ce soit. Aucun secret n'est affiché.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.console.signing import SigningClient, Transport, urllib_transport
from app.core.config import Settings
from app.core.errors import AppError
from app.platform.licensing.keyring import KeyringError, load_keyring

NIL = str(uuid.UUID(int=0))
PROBE_PLAN = "PROBE"


class SigningConfigurationError(AppError):
    status_code = 503
    code = "signing_not_configured"
    title = "Service de signature non configuré"


@dataclass(frozen=True)
class SigningCheck:
    key_id: str
    active_keys: tuple[str, ...]
    base_url: str


def probe_payload(today: date, now: datetime) -> dict[str, Any]:
    return {
        "license_id": str(uuid.uuid4()),
        "license_number": "LIC-0000-00000",
        "license_version": 1,
        "supersedes_id": None,
        "tenant_id": NIL,
        "site_id": NIL,
        "subscription_id": NIL,
        "payment_id": NIL,
        "plan": PROBE_PLAN,
        "billing_period": "monthly",
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": "UTC",
        "valid_from": today.isoformat(),
        "valid_until": today.isoformat(),
        "max_activations": 1,
        "modules": [],
        "features": [],
        "limits": {},
        "compatibility": {},
    }


def check_signing(settings: Settings, transport: Transport = urllib_transport) -> SigningCheck:
    """Signe et vérifie une sonde ; lève une erreur explicite à la première anomalie :
    configuration absente (``signing_not_configured``), trousseau invalide ou sans clé active,
    service injoignable (``signing_service_unavailable``), demande refusée (secret HMAC
    différent : ``signing_failed``), clé du service absente du trousseau ou signature invalide."""
    missing = [
        name
        for name, value in (
            ("SM_SIGNING_SERVICE_URL", settings.signing_service_url),
            ("SM_SIGNING_CLIENT_SECRET", settings.signing_client_secret),
        )
        if not value
    ]
    if missing:
        raise SigningConfigurationError(
            "Configuration manquante : " + ", ".join(missing),
            extra={"missing": missing},
        )
    try:
        keyring = load_keyring(settings.license_public_keys_file)
    except KeyringError as exc:
        raise SigningConfigurationError(f"Trousseau public invalide : {exc}") from exc
    active = tuple(sorted(k.key_id for k in keyring.keys.values() if k.active))
    if not active:
        raise SigningConfigurationError(
            "Aucune clé publique ACTIVE dans le trousseau (SM_LICENSE_PUBLIC_KEYS_FILE ou "
            "trousseau versionné) : aucune licence ne pourrait être vérifiée"
        )
    assert settings.signing_service_url and settings.signing_client_secret
    client = SigningClient(
        base_url=settings.signing_service_url,
        secret=settings.signing_client_secret.get_secret_value().encode(),
        keyring=keyring,
        timeout=settings.signing_timeout_seconds,
        transport=transport,
    )
    now = datetime.now(UTC)
    document = client.sign(probe_payload(now.date(), now))
    return SigningCheck(
        key_id=str(document.get("key_id", "")),
        active_keys=active,
        base_url=settings.signing_service_url,
    )
