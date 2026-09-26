"""Configuration (variables ``SIGNING_*``). Aucun secret n'a de valeur par défaut : la clé privée
et le secret partagé avec la console sont lus depuis des **fichiers montés** (secrets Docker,
systemd ``LoadCredential``…), jamais depuis une variable d'environnement ni depuis le dépôt."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIGNING_", extra="ignore")

    # Identifiant public de la clé (rotation) : repris dans chaque licence signée.
    key_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    # Clé privée Ed25519 (PEM PKCS#8, non chiffrée) : fichier monté, lisible par ce seul service.
    private_key_file: Path
    # Secret HMAC partagé avec la console TechNova (≥ 32 octets) : authentifie chaque demande.
    client_secret_file: Path
    # Écart toléré entre l'horodatage de la demande et l'horloge du service (anti-rejeu).
    max_skew_seconds: int = Field(default=300, ge=10, le=900)
    max_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
