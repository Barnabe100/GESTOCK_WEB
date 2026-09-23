"""Contexte de requête et dépendances FastAPI de contrôle d'accès.

Chaîne appliquée : User → Tenant → Site → Permission → Resource.

- ``CurrentUser`` : jeton valide, session non révoquée, utilisateur actif ;
- ``ActiveUser`` : idem, et aucun changement de mot de passe en attente ;
- ``TenantContext`` : tenant actif (issu du jeton), appartenance active, site éventuel
  (en-tête ``X-Site-Id``) accessible, capacités résolues — et contexte RLS appliqué ;
- ``require_permission(...)`` / ``require_module(...)`` : contrôles fins.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import get_db, set_db_context
from app.core.errors import AppError, ForbiddenError, UnauthorizedError
from app.core.security import AccessClaims, InvalidTokenError, decode_access_token
from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.audit.service import RequestMeta
from app.platform.capabilities.service import Capabilities, CapabilityService
from app.platform.identity.models import AuthSession, User
from app.platform.registry import ModuleRegistry, ModuleStatus, get_registry
from app.platform.subscriptions.service import get_subscription
from app.platform.tenancy.models import Site, Tenant, TenantStatus
from app.shared.clock import utcnow

DbSession = Annotated[Session, Depends(get_db)]


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_registry_dep() -> ModuleRegistry:
    return get_registry()


def get_now() -> datetime:
    return utcnow()


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RegistryDep = Annotated[ModuleRegistry, Depends(get_registry_dep)]
NowDep = Annotated[datetime, Depends(get_now)]
MetaDep = Annotated[RequestMeta, Depends(get_request_meta)]


# --- Authentification -------------------------------------------------------------------------


def get_access_claims(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> AccessClaims:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise UnauthorizedError()
    try:
        return decode_access_token(token, settings)
    except InvalidTokenError as exc:
        raise UnauthorizedError("Jeton invalide ou expiré", code="invalid_token") from exc


@dataclass(frozen=True)
class Authenticated:
    user: User
    claims: AccessClaims


def get_authenticated(
    claims: Annotated[AccessClaims, Depends(get_access_claims)], db: DbSession, now: NowDep
) -> Authenticated:
    set_db_context(db, user_id=claims.user_id, tenant_id=None)
    auth_session = db.get(AuthSession, claims.session_id)
    if (
        auth_session is None
        or auth_session.user_id != claims.user_id
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        raise UnauthorizedError("Session expirée", code="session_expired")
    user = db.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Compte inactif", code="account_inactive")
    return Authenticated(user=user, claims=claims)


CurrentUser = Annotated[Authenticated, Depends(get_authenticated)]


def get_active_user(auth: CurrentUser) -> Authenticated:
    if auth.user.must_change_password:
        raise ForbiddenError(
            "Vous devez changer votre mot de passe", code="password_change_required"
        )
    return auth


ActiveUser = Annotated[Authenticated, Depends(get_active_user)]


# --- Contexte tenant ------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestContext:
    user: User
    tenant: Tenant
    membership: TenantMembership
    site: Site | None
    capabilities: Capabilities
    session_id: uuid.UUID
    meta: RequestMeta

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    def has_permission(self, code: str) -> bool:
        return code in self.capabilities.permissions


def get_tenant_context(
    auth: ActiveUser,
    db: DbSession,
    registry: RegistryDep,
    now: NowDep,
    meta: MetaDep,
    x_site_id: Annotated[str | None, Header()] = None,
) -> RequestContext:
    tenant_id = auth.claims.tenant_id
    if tenant_id is None:
        raise ForbiddenError("Aucune entreprise sélectionnée", code="tenant_not_selected")

    # À partir d'ici, PostgreSQL ne laisse voir que les lignes de ce tenant.
    set_db_context(db, tenant_id=tenant_id, user_id=auth.user.id)

    tenant = db.get(Tenant, tenant_id)
    membership = db.scalars(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == auth.user.id
        )
    ).one_or_none()
    if tenant is None or membership is None or membership.status != MembershipStatus.ACTIVE:
        raise ForbiddenError("Accès à cette entreprise refusé", code="tenant_access_denied")
    if tenant.status != TenantStatus.ACTIVE:
        raise ForbiddenError("Entreprise suspendue", code="tenant_suspended")
    subscription = get_subscription(db)
    if subscription is None:
        raise ForbiddenError("Aucun abonnement", code="subscription_missing")

    site: Site | None = None
    if x_site_id:
        try:
            site_id = uuid.UUID(x_site_id)
        except ValueError as exc:
            raise AppError("Identifiant de site invalide", code="invalid_site") from exc
        site = db.get(Site, site_id)
        if site is None or not site.is_active:
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")

    capabilities = CapabilityService(db, registry).resolve(
        tenant=tenant,
        membership=membership,
        subscription=subscription,
        site_id=site.id if site else None,
        now=now,
    )
    if site is not None and site.id not in capabilities.accessible_site_ids:
        raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")

    return RequestContext(
        user=auth.user,
        tenant=tenant,
        membership=membership,
        site=site,
        capabilities=capabilities,
        session_id=auth.claims.session_id,
        meta=meta,
    )


TenantContext = Annotated[RequestContext, Depends(get_tenant_context)]


def require_permission(code: str) -> Callable[[RequestContext], RequestContext]:
    registry = get_registry()
    if registry.permission(code) is None:
        raise ValueError(f"permission inconnue du registre : {code}")

    def dependency(ctx: TenantContext) -> RequestContext:
        if code in ctx.capabilities.permissions:
            return ctx
        if code in ctx.capabilities.restricted_permissions:
            raise ForbiddenError(
                "Action indisponible avec le statut actuel de l'abonnement",
                code="subscription_restricted",
                extra={"permission": code},
            )
        raise ForbiddenError("Permission insuffisante", code="permission_denied")

    return dependency


def require_module(
    code: str, registry: ModuleRegistry | None = None
) -> Callable[[RequestContext], RequestContext]:
    registry = registry or get_registry()
    if code not in registry:
        raise ValueError(f"module inconnu du registre : {code}")

    def dependency(ctx: TenantContext) -> RequestContext:
        if code not in ctx.capabilities.modules or registry.get(code).status != (
            ModuleStatus.AVAILABLE
        ):
            raise ForbiddenError("Module non disponible", code="module_unavailable")
        return ctx

    return dependency
