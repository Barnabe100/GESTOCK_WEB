"""Trousseau des clés **publiques** de signature et vérification des documents ``.lic``.

Le backend ne détient **aucune** clé privée (ADR-0034) : il vérifie seulement. Trousseau :
fichier TOML versionné (``data/public_keys.toml``) ; ``SM_LICENSE_PUBLIC_KEYS_FILE`` le
remplace (développement et tests : clés éphémères). Une clé ``retired`` vérifie encore les
licences déjà émises mais n'est plus acceptée pour une nouvelle signature (rotation).
"""

import base64
import binascii
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.platform.licensing.canonical import (
    LICENSE_FORMAT,
    LICENSE_FORMAT_VERSION,
    CanonicalError,
    canonical_bytes,
)

DEFAULT_KEYRING = Path(__file__).parent / "data" / "public_keys.toml"
KEY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,62}$")


class KeyringError(ValueError):
    pass


class LicenseVerificationError(ValueError):
    """Document ``.lic`` invalide ; ``code`` : cause stable (format, clé, signature)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PublicKey:
    key_id: str
    key: Ed25519PublicKey
    active: bool


@dataclass(frozen=True)
class Keyring:
    keys: dict[str, PublicKey]

    def get(self, key_id: str) -> PublicKey | None:
        return self.keys.get(key_id)


def parse_keyring(data: dict[str, Any]) -> Keyring:
    keys: dict[str, PublicKey] = {}
    for entry in data.get("keys", []):
        key_id = entry.get("key_id", "")
        if not KEY_ID.match(key_id) or key_id in keys:
            raise KeyringError(f"key_id invalide ou en double : {key_id!r}")
        if entry.get("algorithm") != "Ed25519":
            raise KeyringError(f"algorithme non pris en charge pour {key_id}")
        if entry.get("status") not in ("active", "retired"):
            raise KeyringError(f"statut invalide pour {key_id}")
        if any("private" in k.lower() for k in entry):
            raise KeyringError("le trousseau ne contient que des clés publiques")
        try:
            raw = base64.b64decode(entry.get("public_key", ""), validate=True)
            key = Ed25519PublicKey.from_public_bytes(raw)
        except (binascii.Error, ValueError) as exc:
            raise KeyringError(f"clé publique invalide pour {key_id}") from exc
        keys[key_id] = PublicKey(key_id=key_id, key=key, active=entry["status"] == "active")
    return Keyring(keys=keys)


@lru_cache(maxsize=8)
def load_keyring(path: Path | None = None) -> Keyring:
    with (path or DEFAULT_KEYRING).open("rb") as handle:
        return parse_keyring(tomllib.load(handle))


def verify_document(
    document: Any, keyring: Keyring, *, require_active_key: bool = False
) -> dict[str, Any]:
    """Vérifie un document ``.lic`` v1 et renvoie son payload. ``require_active_key`` : exigé
    pour une signature **nouvelle** (clé retirée refusée)."""
    if not isinstance(document, dict):
        raise LicenseVerificationError("license_format_invalid")
    if set(document) != {"format", "version", "key_id", "payload", "signature"}:
        raise LicenseVerificationError("license_format_invalid")
    if (
        document["format"] != LICENSE_FORMAT
        or document["version"] != LICENSE_FORMAT_VERSION
        or not isinstance(document["payload"], dict)
        or not isinstance(document["signature"], str)
        or not isinstance(document["key_id"], str)
    ):
        raise LicenseVerificationError("license_format_invalid")
    public = keyring.get(document["key_id"])
    if public is None:
        raise LicenseVerificationError("license_key_unknown")
    if require_active_key and not public.active:
        raise LicenseVerificationError("license_key_retired")
    try:
        signature = base64.b64decode(document["signature"], validate=True)
        public.key.verify(signature, canonical_bytes(document))
    except (binascii.Error, CanonicalError, InvalidSignature, ValueError) as exc:
        raise LicenseVerificationError("license_signature_invalid") from exc
    return document["payload"]
