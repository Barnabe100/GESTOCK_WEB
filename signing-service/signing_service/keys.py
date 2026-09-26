"""Chargement de la clé privée et du secret client depuis leurs fichiers montés.

La clé privée ne quitte jamais ce processus : elle n'est ni journalisée, ni renvoyée, ni
exposée par une route ; seul son identifiant public (``key_id``) et la clé **publique**
dérivée sont connus à l'extérieur.
"""

import logging
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger("signing_service")

MIN_SECRET_BYTES = 32


class KeyConfigurationError(RuntimeError):
    pass


def _warn_permissions(path: Path, label: str) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise KeyConfigurationError(f"{label} : fichier modifiable par d'autres utilisateurs")
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        # Secrets Docker montés en 0444 : accepté, mais signalé (sans le contenu).
        logger.warning("%s lisible par d'autres utilisateurs du système", label)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    if not path.is_file():
        raise KeyConfigurationError("clé privée introuvable (SIGNING_PRIVATE_KEY_FILE)")
    _warn_permissions(path, "clé privée")
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError) as exc:  # contenu jamais recopié dans le message
        raise KeyConfigurationError("clé privée illisible (PEM PKCS#8 attendu)") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyConfigurationError("la clé privée doit être une clé Ed25519")
    return key


def load_client_secret(path: Path) -> bytes:
    if not path.is_file():
        raise KeyConfigurationError("secret client introuvable (SIGNING_CLIENT_SECRET_FILE)")
    _warn_permissions(path, "secret client")
    secret = path.read_bytes().strip()
    if len(secret) < MIN_SECRET_BYTES:
        raise KeyConfigurationError(f"secret client trop court (≥ {MIN_SECRET_BYTES} octets)")
    return secret


def public_key_b64(key: Ed25519PrivateKey) -> str:
    """Clé publique brute (32 octets) en base64 : forme publiée dans le trousseau du backend."""
    import base64

    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")
