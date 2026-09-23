"""Authentification : connexion, sessions (jetons de rafraîchissement rotatifs), mot de passe.

Le tenant actif est déterminé explicitement : il est lié au jeton d'accès, choisi à la connexion
ou au rafraîchissement (``tenant_id``), et vérifié contre une appartenance active.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import set_db_context
from app.core.errors import BusinessRuleError, ForbiddenError, UnauthorizedError
from app.core.security import (
    AccessClaims,
    InvalidTokenError,
    create_access_token,
    format_refresh_token,
    hash_password,
    hash_token,
    new_refresh_secret,
    parse_refresh_token,
    password_needs_rehash,
    token_matches,
    verify_password,
)
from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.audit.service import RequestMeta, record_audit
from app.platform.identity.models import AuthSession, User
from app.platform.identity.passwords import normalize_email, validate_new_password
from app.platform.tenancy.models import Tenant, TenantStatus


@dataclass(frozen=True)
class MembershipInfo:
    membership: TenantMembership
    tenant: Tenant


@dataclass(frozen=True)
class IssuedSession:
    user: User
    access_token: str
    # Nouveau jeton de rafraîchissement à poser en cookie (None : conserver le cookie actuel).
    refresh_token: str | None
    tenant_id: uuid.UUID | None
    memberships: list[MembershipInfo]


class AuthService:
    def __init__(self, session: Session, settings: Settings, now: datetime) -> None:
        self.db = session
        self.settings = settings
        self.now = now

    # --- Appartenances ---------------------------------------------------------------------

    def memberships_of(self, user: User) -> list[MembershipInfo]:
        """Appartenances actives de l'utilisateur dans des tenants actifs (tous tenants)."""
        set_db_context(self.db, user_id=user.id, tenant_id=None)
        rows = self.db.execute(
            select(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user.id,
                TenantMembership.status == MembershipStatus.ACTIVE,
                Tenant.status == TenantStatus.ACTIVE,
            )
            .order_by(Tenant.name)
        ).all()
        return [MembershipInfo(membership=m, tenant=t) for m, t in rows]

    def _select_tenant(
        self, memberships: list[MembershipInfo], requested: uuid.UUID | None
    ) -> uuid.UUID | None:
        if requested is not None:
            if not any(info.tenant.id == requested for info in memberships):
                raise ForbiddenError("Accès à cette entreprise refusé", code="tenant_access_denied")
            return requested
        if len(memberships) == 1:
            return memberships[0].tenant.id
        return None

    def _access_token(self, user: User, session_id: uuid.UUID, tenant_id: uuid.UUID | None) -> str:
        claims = AccessClaims(user_id=user.id, session_id=session_id, tenant_id=tenant_id)
        return create_access_token(claims, self.settings, self.now)

    # --- Connexion -------------------------------------------------------------------------

    def login(
        self, email: str, password: str, tenant_id: uuid.UUID | None, meta: RequestMeta
    ) -> IssuedSession:
        normalized = normalize_email(email)
        user = self.db.scalars(select(User).where(User.email == normalized)).one_or_none()

        if user is not None and user.locked_until is not None and user.locked_until > self.now:
            self._audit_failure(user, normalized, "locked", meta)
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
            self._audit_failure(user, normalized, "invalid_credentials", meta)
            raise UnauthorizedError("Email ou mot de passe incorrect", code="invalid_credentials")

        if not user.is_active:
            self._audit_failure(user, normalized, "inactive", meta)
            raise UnauthorizedError("Compte inactif", code="account_inactive")

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = self.now
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        memberships = self.memberships_of(user)
        selected = self._select_tenant(memberships, tenant_id)

        secret = new_refresh_secret()
        auth_session = AuthSession(
            user_id=user.id,
            refresh_token_hash=hash_token(secret),
            created_at=self.now,
            expires_at=self.now + timedelta(days=self.settings.refresh_token_ttl_days),
            last_used_at=self.now,
            user_agent=meta.user_agent[:500] if meta.user_agent else None,
            ip_address=meta.ip_address,
        )
        self.db.add(auth_session)
        self.db.flush()

        set_db_context(self.db, tenant_id=selected, user_id=user.id)
        record_audit(
            self.db,
            action="auth.login.succeeded",
            tenant_id=selected,
            user_id=user.id,
            entity_type="auth_session",
            entity_id=auth_session.id,
            meta=meta,
        )
        return IssuedSession(
            user=user,
            access_token=self._access_token(user, auth_session.id, selected),
            refresh_token=format_refresh_token(auth_session.id, secret),
            tenant_id=selected,
            memberships=memberships,
        )

    def _audit_failure(self, user: User | None, email: str, reason: str, meta: RequestMeta) -> None:
        set_db_context(self.db, tenant_id=None, user_id=user.id if user else None)
        record_audit(
            self.db,
            action="auth.login.failed",
            tenant_id=None,
            user_id=user.id if user else None,
            data={"email": email, "reason": reason},
            meta=meta,
        )
        # L'échec est tracé même si la requête se termine en erreur.
        self.db.commit()

    # --- Rafraîchissement --------------------------------------------------------------------

    def refresh(
        self, refresh_token: str | None, tenant_id: uuid.UUID | None, meta: RequestMeta
    ) -> IssuedSession:
        if not refresh_token:
            raise UnauthorizedError("Session expirée", code="session_expired")
        try:
            session_id, secret = parse_refresh_token(refresh_token)
        except InvalidTokenError as exc:
            raise UnauthorizedError("Session expirée", code="session_expired") from exc

        # Verrou de ligne : sérialise les rafraîchissements concurrents d'une même session.
        auth_session = self.db.scalars(
            select(AuthSession).where(AuthSession.id == session_id).with_for_update()
        ).one_or_none()
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= self.now
        ):
            raise UnauthorizedError("Session expirée", code="session_expired")

        new_cookie: str | None
        if token_matches(secret, auth_session.refresh_token_hash):
            new_secret = new_refresh_secret()
            auth_session.previous_token_hash = auth_session.refresh_token_hash
            auth_session.refresh_token_hash = hash_token(new_secret)
            auth_session.rotated_at = self.now
            new_cookie = format_refresh_token(auth_session.id, new_secret)
        elif (
            token_matches(secret, auth_session.previous_token_hash)
            and auth_session.rotated_at is not None
            and self.now - auth_session.rotated_at
            <= timedelta(seconds=self.settings.refresh_token_reuse_grace_seconds)
        ):
            # Requête concurrente d'un autre onglet juste après une rotation : acceptée,
            # sans nouvelle rotation (le cookie à jour a déjà été posé).
            new_cookie = None
        else:
            # Réutilisation d'un ancien jeton : vol probable → révocation de la session.
            auth_session.revoked_at = self.now
            set_db_context(self.db, tenant_id=None, user_id=auth_session.user_id)
            record_audit(
                self.db,
                action="auth.refresh.reuse_detected",
                tenant_id=None,
                user_id=auth_session.user_id,
                entity_type="auth_session",
                entity_id=auth_session.id,
                meta=meta,
            )
            self.db.commit()
            raise UnauthorizedError("Session expirée", code="session_expired")

        user = self.db.get(User, auth_session.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Compte inactif", code="account_inactive")
        auth_session.last_used_at = self.now

        memberships = self.memberships_of(user)
        selected = self._select_tenant(memberships, tenant_id)
        return IssuedSession(
            user=user,
            access_token=self._access_token(user, auth_session.id, selected),
            refresh_token=new_cookie,
            tenant_id=selected,
            memberships=memberships,
        )

    # --- Déconnexion -----------------------------------------------------------------------

    def logout(self, refresh_token: str | None, meta: RequestMeta) -> None:
        if not refresh_token:
            return
        try:
            session_id, secret = parse_refresh_token(refresh_token)
        except InvalidTokenError:
            return
        auth_session = self.db.get(AuthSession, session_id)
        if auth_session is None or auth_session.revoked_at is not None:
            return
        if not (
            token_matches(secret, auth_session.refresh_token_hash)
            or token_matches(secret, auth_session.previous_token_hash)
        ):
            return
        auth_session.revoked_at = self.now
        set_db_context(self.db, tenant_id=None, user_id=auth_session.user_id)
        record_audit(
            self.db,
            action="auth.logout",
            tenant_id=None,
            user_id=auth_session.user_id,
            entity_type="auth_session",
            entity_id=auth_session.id,
            meta=meta,
        )

    # --- Mot de passe ----------------------------------------------------------------------

    def change_password(
        self,
        user: User,
        current_session_id: uuid.UUID,
        current_password: str,
        new_password: str,
        meta: RequestMeta,
    ) -> None:
        if not verify_password(user.password_hash, current_password):
            raise BusinessRuleError(
                "Mot de passe actuel incorrect", code="invalid_current_password"
            )
        if current_password == new_password:
            raise BusinessRuleError(
                "Le nouveau mot de passe doit être différent", code="password_unchanged"
            )
        validate_new_password(new_password, email=user.email, settings=self.settings)
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        user.password_changed_at = self.now
        # Les autres sessions de l'utilisateur sont révoquées.
        for other in self.db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.id != current_session_id,
                AuthSession.revoked_at.is_(None),
            )
        ):
            other.revoked_at = self.now
        set_db_context(self.db, tenant_id=None, user_id=user.id)
        record_audit(
            self.db, action="auth.password.changed", tenant_id=None, user_id=user.id, meta=meta
        )
