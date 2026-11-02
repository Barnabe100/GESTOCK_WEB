import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-0123456789"
# Adresse publique de l'interface en DÉVELOPPEMENT seulement (serveur Vite), quand
# ``SM_PUBLIC_APP_URL`` n'est pas défini ; jamais utilisée en test ni en production.
_DEV_PUBLIC_APP_URL = "http://localhost:5173"
_DEV_RATE_LIMIT_KEY = "dev-only-rate-limit-key-change-me-0123456789"


def public_url_errors(url: str, allowed_hosts: list[str], environment: str) -> list[str]:
    """Contrôle de l'adresse publique de l'interface (R7, ADR-0052) : schéma ``https`` (``http``
    admis en développement seulement), domaine présent dans ``allowed_hosts``, aucun chemin,
    paramètre, fragment ni identifiant."""
    errors: list[str] = []
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018 — port invalide : ValueError
    except ValueError:
        return ["adresse invalide"]
    if parts.scheme not in ("http", "https"):
        errors.append("schéma http(s) attendu")
    if parts.scheme == "http" and environment != "development":
        errors.append("https obligatoire hors développement")
    if not host:
        errors.append("domaine manquant")
    if parts.username or parts.password:
        errors.append("identifiants interdits dans l'adresse")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        errors.append("ni chemin, ni paramètre, ni fragment")
    if host and host not in {h.strip().lower() for h in allowed_hosts}:
        errors.append(f"domaine « {host} » absent de SM_PUBLIC_ALLOWED_HOSTS")
    return errors


def parse_notice_days(value: str) -> tuple[int, ...]:
    """``"30,15,…,-7"`` → ``(30, 15, …, -7)`` ; entiers distincts entre -60 et 365."""
    try:
        steps = {int(part) for part in value.split(",") if part.strip()}
    except ValueError as exc:
        raise ValueError("SM_RENEWAL_NOTICE_DAYS : entiers séparés par des virgules") from exc
    if not steps or any(not -60 <= step <= 365 for step in steps):
        raise ValueError("SM_RENEWAL_NOTICE_DAYS : au moins une étape, entre -60 et 365 jours")
    return tuple(sorted(steps, reverse=True))


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
    # Purge des notifications des membres (ADR-0049 D8, palier R4) : rôle SQL dédié, sans
    # BYPASSRLS, limité à ``SELECT (id, created_at)`` et ``DELETE`` des notifications de plus de
    # 30 jours (``stockmanager notifications purge``).
    purge_database_url: str = (
        "postgresql+psycopg://stockmanager_purge:stockmanager_purge@localhost:5432/stockmanager"
    )
    db_purge_role: str = "stockmanager_purge"
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

    # Licences (Phase 3.3-B2, ADR-0034). Signing Service : processus distinct, seul détenteur de
    # la clé privée Ed25519 ; la console l'appelle avec un secret HMAC partagé (jamais la clé).
    # Sans URL : génération impossible (503 signing_service_unavailable).
    signing_service_url: str | None = None
    signing_client_secret: SecretStr | None = None
    signing_timeout_seconds: float = 10.0
    # Trousseau des clés PUBLIQUES (vérification) ; défaut : fichier versionné du backend.
    license_public_keys_file: Path | None = None
    # Répertoire des sauvegardes monté en LECTURE SEULE dans la console (état des sauvegardes de
    # base, docs/exploitation/SAUVEGARDE_RESTAURATION.md) ; facultatif.
    backup_dir: Path | None = None
    # Postes (3.3-B3) : durée pendant laquelle une installation peut fonctionner sans contacter
    # le serveur (renvoyée au client lors du contrôle ; au-delà, poste signalé « non vu »).
    activation_offline_grace_days: int = 7
    # Notifications d'échéance (3.3-B4) : jours avant (positifs) ou après (négatifs) le dernier
    # jour couvert par la licence. Unique source de la liste (``renewal_notice_steps``).
    renewal_notice_days: str = "30,15,10,5,1,0,-1,-7"

    @property
    def renewal_notice_steps(self) -> tuple[int, ...]:
        """Étapes triées de la plus lointaine à la plus tardive (ex. 30 … -7)."""
        return parse_notice_days(self.renewal_notice_days)

    # Adresse PUBLIQUE de l'interface (R7, ADR-0052) : base des adresses intégrées aux QR Codes
    # et aux liens de suivi, construites par ``app.core.public_urls`` SEULEMENT (jamais depuis
    # ``Host``, ``X-Forwarded-Host`` ni une adresse du navigateur). Contrôlée au démarrage :
    # domaine dans ``public_allowed_hosts``, https hors développement. Absente : en
    # développement, serveur Vite local ; ailleurs, génération des QR refusée (503).
    public_app_url: str | None = None
    public_allowed_hosts: list[str] = []
    # Proxies de confiance (R7) : adresses ou réseaux (CIDR) dont l'en-tête ``X-Forwarded-For``
    # est lu pour l'adresse du client. Vide : l'adresse de la connexion seule fait foi (aucune
    # adresse transmise par le client n'est jamais acceptée d'un pair non fiable).
    trusted_proxies: list[str] = []
    # Clé secrète des empreintes HMAC des adresses IP (limitation de fréquence publique, R7).
    # Obligatoire en production (≥ 32 caractères) ; jamais journalisée.
    rate_limit_hmac_key: SecretStr = SecretStr(_DEV_RATE_LIMIT_KEY)
    # Commandes par QR Code (R7) : limitation de fréquence (fenêtre glissante, base de données).
    qr_order_ip_limit: int = 10
    qr_order_point_limit: int = 30
    qr_order_window_minutes: int = 10
    qr_read_ip_limit: int = 120
    qr_read_window_minutes: int = 1
    # Lien de suivi d'une commande QR : durée de validité après sa fin (clôture, annulation,
    # refus). Le téléphone du client est effacé 30 jours après la fin (durée figée dans la
    # migration 0057, comme la purge des notifications : ``stockmanager notifications purge``).
    qr_tracking_hours_after_end: int = 12

    @property
    def public_base_url(self) -> str | None:
        """Adresse publique contrôlée, sans barre finale ; ``None`` si non configurée hors
        développement."""
        if self.public_app_url:
            return self.public_app_url.rstrip("/")
        return _DEV_PUBLIC_APP_URL if self.environment == "development" else None

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(ipaddress.ip_network(p.strip(), strict=False) for p in self.trusted_proxies)

    # Exports (Lot 2) : nombre maximal de lignes d'un export (au-delà : affiner les filtres).
    export_max_rows: int = 50_000

    password_min_length: int = 8
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    @model_validator(mode="after")
    def _check_production_secrets(self) -> "Settings":
        parse_notice_days(self.renewal_notice_days)  # configuration invalide : refus au démarrage
        if self.environment == "production":
            secret = self.jwt_secret.get_secret_value()
            if secret == _DEV_JWT_SECRET or len(secret) < 32:
                raise ValueError("SM_JWT_SECRET doit être défini (≥ 32 caractères) en production")
            key = self.rate_limit_hmac_key.get_secret_value()
            if key == _DEV_RATE_LIMIT_KEY or len(key) < 32:
                raise ValueError(
                    "SM_RATE_LIMIT_HMAC_KEY doit être défini (≥ 32 caractères) en production"
                )
        if self.public_app_url is not None:
            errors = public_url_errors(
                self.public_app_url, self.public_allowed_hosts, self.environment
            )
            if errors:
                raise ValueError("SM_PUBLIC_APP_URL invalide : " + " ; ".join(errors))
        try:
            self.trusted_proxy_networks  # noqa: B018 — réseau invalide : refus au démarrage
        except ValueError as exc:
            raise ValueError(f"SM_TRUSTED_PROXIES invalide : {exc}") from exc
        limits = (
            self.qr_order_ip_limit,
            self.qr_order_point_limit,
            self.qr_order_window_minutes,
            self.qr_read_ip_limit,
            self.qr_read_window_minutes,
            self.qr_tracking_hours_after_end,
        )
        if any(value < 1 for value in limits):
            raise ValueError("Réglages SM_QR_* : entiers strictement positifs attendus")
        if self.signing_service_url:
            # Requête de signature par urllib : jamais de schéma ``file:`` ni exotique.
            if not self.signing_service_url.startswith(("http://", "https://")):
                raise ValueError("SM_SIGNING_SERVICE_URL : adresse http:// ou https:// attendue")
            signing_secret = (
                self.signing_client_secret.get_secret_value() if self.signing_client_secret else ""
            )
            if len(signing_secret) < 32:
                raise ValueError("SM_SIGNING_CLIENT_SECRET doit compter au moins 32 caractères")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
