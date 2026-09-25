from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-0123456789"


class Settings(BaseSettings):
    """Configuration de l'application, lue depuis l'environnement (préfixe SM_)."""

    model_config = SettingsConfigDict(env_prefix="SM_", env_file=".env", extra="ignore")

    app_name: str = "StockManager Web"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False

    api_v1_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Connexion applicative : rôle SQL sans BYPASSRLS, soumis à la Row-Level Security.
    database_url: str = (
        "postgresql+psycopg://stockmanager_app:stockmanager_app@localhost:5432/stockmanager"
    )
    # Connexion propriétaire : migrations Alembic et synchronisation du catalogue.
    migration_database_url: str = (
        "postgresql+psycopg://stockmanager:stockmanager@localhost:5432/stockmanager"
    )
    # Rôle SQL applicatif auquel les migrations accordent les droits.
    db_app_role: str = "stockmanager_app"
    # Console TechNova (ADR-0031) : processus distinct, rôle SQL dédié (sans BYPASSRLS, droits
    # minimaux : aucun accès aux données des tenants).
    platform_database_url: str = (
        "postgresql+psycopg://stockmanager_platform:stockmanager_platform@localhost:5432/"
        "stockmanager"
    )
    db_platform_role: str = "stockmanager_platform"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: int = 30
    db_echo: bool = False

    jwt_secret: SecretStr = SecretStr(_DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 30
    # Fenêtre pendant laquelle l'ancien jeton de rafraîchissement reste accepté
    # (requêtes concurrentes de plusieurs onglets).
    refresh_token_reuse_grace_seconds: int = 60

    refresh_cookie_name: str = "sm_refresh"
    refresh_cookie_secure: bool = True

    # Inscription publique (Phase 3.2) : ouverte ou non ; limite par adresse IP du client
    # (derrière un reverse proxy, uvicorn doit recevoir l'IP réelle : --proxy-headers et
    # --forwarded-allow-ips, sinon toutes les inscriptions partagent l'IP du proxy).
    signup_enabled: bool = True
    signup_rate_limit_attempts: int = 5
    signup_rate_limit_window_minutes: int = 60
    # Adresse commerciale affichée pour les offres sur contact (« Contacter TechNova »).
    sales_contact_email: str | None = None

    # Console TechNova : préfixe de l'API, cookie de session (HttpOnly, SameSite=Strict),
    # expiration absolue et après inactivité.
    platform_api_prefix: str = "/platform-api/v1"
    platform_cookie_name: str = "sm_platform_session"
    platform_cookie_secure: bool = True
    platform_session_ttl_minutes: int = 480
    platform_session_idle_minutes: int = 30

    password_min_length: int = 8
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        if self.environment == "production":
            secret = self.jwt_secret.get_secret_value()
            if secret == _DEV_JWT_SECRET or len(secret) < 32:
                raise ValueError("SM_JWT_SECRET doit être défini (≥ 32 caractères) en production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
