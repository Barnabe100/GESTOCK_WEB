"""Primitives cryptographiques : hachage des mots de passe (Argon2id) et jetons."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

_hasher = PasswordHasher()
# Hash factice pour égaliser le temps de réponse quand l'utilisateur n'existe pas.
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# --- Jetons d'accès (JWT courts) -------------------------------------------------------------


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    tenant_id: uuid.UUID | None


class InvalidTokenError(Exception):
    pass


def create_access_token(claims: AccessClaims, settings: Settings, now: datetime) -> str:
    payload = {
        "sub": str(claims.user_id),
        "sid": str(claims.session_id),
        "tid": str(claims.tenant_id) if claims.tenant_id else None,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str, settings: Settings) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "sid", "exp", "iat"]},
        )
        if payload.get("typ") != "access":
            raise InvalidTokenError("type de jeton invalide")
        tid = payload.get("tid")
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            session_id=uuid.UUID(payload["sid"]),
            tenant_id=uuid.UUID(tid) if tid else None,
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc


# --- Jetons de rafraîchissement (opaques, stockés hachés) -------------------------------------


def new_refresh_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def token_matches(secret: str, token_hash: str | None) -> bool:
    return token_hash is not None and hmac.compare_digest(hash_token(secret), token_hash)


def format_refresh_token(session_id: uuid.UUID, secret: str) -> str:
    return f"{session_id}.{secret}"


def parse_refresh_token(token: str) -> tuple[uuid.UUID, str]:
    session_part, _, secret = token.partition(".")
    try:
        return uuid.UUID(session_part), secret
    except ValueError as exc:
        raise InvalidTokenError("jeton de rafraîchissement mal formé") from exc
