"""Authentification de la console TechNova : administrateurs de la plateforme uniquement.

- Identité : ``User`` global portant ``is_platform_admin`` (attribué par la CLI seulement). Le
  rôle SQL de la console ne voit **que** ces comptes (RLS) : un utilisateur d'entreprise, quel
  que soit son rôle, n'existe pas pour la console (``invalid_credentials``).
- Session : jeton opaque ``<session>.<secret>`` dans un cookie HttpOnly, ``SameSite=Strict``,
  limité au chemin de l'API de la console ; seul son hachage est stocké. Expiration absolue et
  d'inactivité ; révocation à la déconnexion. Aucune appartenance à un tenant, aucun jeton de
  l'API des tenants.
- Anti-CSRF : toute requête modifiante exige l'en-tête ``X-TechNova-Console`` (impossible à
  poser depuis un autre site sans CORS, que la console n'active pas).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_platform_audit
from app.console.models import PlatformSession
from app.core.config import Settings
from app.core.db import get_db
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import (
    InvalidTokenError,
    format_refresh_token,
    hash_token,
    new_refresh_secret,
    parse_refresh_token,
    token_matches,
    verify_password,
)
from app.platform.audit.service import RequestMeta
from app.platform.identity.models import User
from app.platform.identity.passwords import normalize_email
from app.shared.clock import utcnow

CONSOLE_HEADER = "X-TechNova-Console"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Écriture de ``last_used_at`` au plus une fois par minute (pas une écriture par requête).
TOUCH_INTERVAL = timedelta(seconds=60)


def actor_of(user: User) -> PlatformActor:
    return PlatformActor(user_id=user.id, label=user.email)


class ConsoleAuthService:
    def __init__(self, db: Session, settings: Settings, now: datetime) -> None:
        self.db = db
        self.settings = settings
        self.now = now

    def login(self, email: str, password: str, meta: RequestMeta) -> tuple[User, str]:
        """Ouvre une session ; renvoie l'administrateur et la valeur du cookie."""
        normalized = normalize_email(email)
        # RLS : seuls les comptes TechNova sont visibles du rôle de la console.
        user = self.db.scalars(select(User).where(User.email == normalized)).one_or_none()

        if user is not None and user.locked_until is not None and user.locked_until > self.now:
            self._failure(user, normalized, "locked", meta)
            raise UnauthorizedError(
                "Trop de tentatives. Réessayez plus tard.", code="account_locked"
            )
        if user is None or not verify_password(user.password_hash if user else None, password):
            if user is not None:
                user.failed_login_count += 1
                if user.failed_login_count >= self.settings.login_max_failures:
                    user.locked_until = self.now + timedelta(
                        minutes=self.settings.login_lockout_minutes
                    )
                    user.failed_login_count = 0
            self._failure(user, normalized, "invalid_credentials", meta)
            raise UnauthorizedError("Email ou mot de passe incorrect", code="invalid_credentials")
        # Défense en profondeur : la RLS ne laisse déjà voir que des comptes TechNova.
        if not user.is_platform_admin:
            self._failure(user, normalized, "not_platform_admin", meta)
            raise UnauthorizedError("Email ou mot de passe incorrect", code="invalid_credentials")
        if not user.is_active:
            self._failure(user, normalized, "inactive", meta)
            raise UnauthorizedError("Compte inactif", code="account_inactive")

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = self.now
        secret = new_refresh_secret()
        session = PlatformSession(
            user_id=user.id,
            token_hash=hash_token(secret),
            created_at=self.now,
            expires_at=self.now + timedelta(minutes=self.settings.platform_session_ttl_minutes),
            last_used_at=self.now,
            ip_address=meta.ip_address,
            user_agent=meta.user_agent[:500] if meta.user_agent else None,
        )
        self.db.add(session)
        self.db.flush()
        record_platform_audit(
            self.db,
            actor=actor_of(user),
            action="auth.login.succeeded",
            target_type="platform_session",
            target_id=session.id,
            meta=meta,
        )
        return user, format_refresh_token(session.id, secret)

    def _failure(self, user: User | None, email: str, reason: str, meta: RequestMeta) -> None:
        record_platform_audit(
            self.db,
            actor=actor_of(user) if user else None,
            action="auth.login.failed",
            data={"email": email, "reason": reason},
            meta=meta,
        )
        # L'échec (et le compteur de tentatives) est enregistré même si la requête échoue.
        self.db.commit()

    def _session(self, token: str | None) -> PlatformSession | None:
        if not token:
            return None
        try:
            session_id, secret = parse_refresh_token(token)
        except InvalidTokenError:
            return None
        session = self.db.get(PlatformSession, session_id)
        if session is None or not token_matches(secret, session.token_hash):
            return None
        return session

    def authenticate(self, token: str | None) -> tuple[User, PlatformSession]:
        session = self._session(token)
        idle = timedelta(minutes=self.settings.platform_session_idle_minutes)
        if (
            session is None
            or session.revoked_at is not None
            or session.expires_at <= self.now
            or session.last_used_at + idle <= self.now
        ):
            raise UnauthorizedError("Session expirée", code="session_expired")
        user = self.db.get(User, session.user_id)
        # Statut retiré par la CLI : le compte n'est plus visible (RLS) ; contrôle explicite.
        if user is None or not user.is_platform_admin or not user.is_active:
            raise UnauthorizedError("Session expirée", code="session_expired")
        if self.now - session.last_used_at >= TOUCH_INTERVAL:
            session.last_used_at = self.now
            self.db.commit()
        return user, session

    def logout(self, token: str | None, meta: RequestMeta) -> None:
        session = self._session(token)
        if session is None or session.revoked_at is not None:
            return
        session.revoked_at = self.now
        user = self.db.get(User, session.user_id)
        record_platform_audit(
            self.db,
            actor=actor_of(user) if user else None,
            action="auth.logout",
            target_type="platform_session",
            target_id=session.id,
            meta=meta,
        )


# --- Dépendances FastAPI -----------------------------------------------------------------------

ConsoleDb = Annotated[Session, Depends(get_db)]


def get_console_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_now() -> datetime:
    return utcnow()


def get_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


ConsoleSettings = Annotated[Settings, Depends(get_console_settings)]
NowDep = Annotated[datetime, Depends(get_now)]
MetaDep = Annotated[RequestMeta, Depends(get_meta)]


def require_console_header(request: Request) -> None:
    """Anti-CSRF : les requêtes modifiantes viennent de l'interface de la console."""
    if request.method not in SAFE_METHODS and request.headers.get(CONSOLE_HEADER) != "1":
        raise ForbiddenError("Requête refusée", code="console_header_required")


@dataclass(frozen=True)
class PlatformContext:
    user: User
    session_id: uuid.UUID
    meta: RequestMeta

    @property
    def actor(self) -> PlatformActor:
        return actor_of(self.user)


def get_platform_admin(
    request: Request, db: ConsoleDb, settings: ConsoleSettings, now: NowDep, meta: MetaDep
) -> PlatformContext:
    token = request.cookies.get(settings.platform_cookie_name)
    user, session = ConsoleAuthService(db, settings, now).authenticate(token)
    return PlatformContext(user=user, session_id=session.id, meta=meta)


PlatformAdmin = Annotated[PlatformContext, Depends(get_platform_admin)]
