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
from dataclasses import dataclass, field
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
from app.platform.capabilities.service import (
    Capabilities,
    CapabilityService,
    SubscriptionMissingError,
)
from app.platform.identity.models import AuthSession, User
from app.platform.registry import AccessKind, ModuleRegistry, ModuleStatus, get_registry
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


# Natures de permission qui ne modifient rien : pas de revérification par site (Q6, ADR-0033).
_READ_ONLY_ACCESS = frozenset({AccessKind.READ, AccessKind.EXPORT})


@dataclass(frozen=True)
class RequestContext:
    user: User
    tenant: Tenant
    membership: TenantMembership
    site: Site | None
    capabilities: Capabilities
    session_id: uuid.UUID
    meta: RequestMeta
    # Capacités sur un autre site que le site sélectionné (abonnement de ce site), et
    # exigences de permission de la route (renseignées par ``require_permission``).
    site_capabilities: Callable[[uuid.UUID], Capabilities] = field(
        default=lambda _site_id: _no_site_capabilities(), compare=False, repr=False
    )
    requirements: list[frozenset[str]] = field(default_factory=list, compare=False, repr=False)

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    def has_permission(self, code: str) -> bool:
        return code in self.capabilities.permissions

    def has_feature(self, code: str) -> bool:
        return code in self.capabilities.features

    def ensure_site_allows(self, site_id: uuid.UUID) -> None:
        """1 site = 1 abonnement (ADR-0033) : une opération qui **écrit** sur un site doit être
        autorisée par l'abonnement de CE site, même si elle est lancée sans site sélectionné
        (capacités consolidées de l'entreprise). Les lectures ne sont pas revérifiées."""
        if self.site is not None and self.site.id == site_id:
            return  # capacités déjà résolues pour ce site
        writes = [r for r in self.requirements if not _read_only(r)]
        if not writes:
            return
        capabilities = self.site_capabilities(site_id)
        for requirement in writes:
            if requirement & capabilities.permissions:
                continue
            extra = {"site_id": str(site_id)}
            if requirement & capabilities.restricted_permissions:
                raise ForbiddenError(
                    "Action indisponible avec le statut de l'abonnement de ce site",
                    code="subscription_restricted",
                    extra=extra,
                )
            raise ForbiddenError(
                "Permission insuffisante sur ce site", code="permission_denied", extra=extra
            )


def _no_site_capabilities() -> Capabilities:
    raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")


def _read_only(requirement: frozenset[str]) -> bool:
    registry = get_registry()
    return all(
        (definition := registry.permission(code)) is not None
        and definition.access in _READ_ONLY_ACCESS
        for code in requirement
    )


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
    site: Site | None = None
    if x_site_id:
        try:
            site_id = uuid.UUID(x_site_id)
        except ValueError as exc:
            raise AppError("Identifiant de site invalide", code="invalid_site") from exc
        site = db.get(Site, site_id)
        if site is None or not site.is_active:
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")

    service = CapabilityService(db, registry)

    def resolve(target: uuid.UUID | None) -> Capabilities:
        try:
            return service.resolve(tenant=tenant, membership=membership, site_id=target, now=now)
        except SubscriptionMissingError as exc:
            raise ForbiddenError("Aucun abonnement", code="subscription_missing") from exc

    capabilities = resolve(site.id if site else None)
    if site is not None and site.id not in capabilities.accessible_site_ids:
        raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")

    cache: dict[uuid.UUID, Capabilities] = {}

    def site_capabilities(target: uuid.UUID) -> Capabilities:
        if target not in capabilities.accessible_site_ids:
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")
        if target not in cache:
            cache[target] = resolve(target)
        return cache[target]

    return RequestContext(
        user=auth.user,
        tenant=tenant,
        membership=membership,
        site=site,
        capabilities=capabilities,
        session_id=auth.claims.session_id,
        meta=meta,
        site_capabilities=site_capabilities,
    )


TenantContext = Annotated[RequestContext, Depends(get_tenant_context)]


# Codes utilisés par les dépendances d'autorisation. Ils sont vérifiés contre le registre au
# démarrage de l'application (``verify_declared_requirements``) : les routeurs de modules sont
# importés pendant la construction du registre, on ne peut donc pas le consulter à ce moment.
_DECLARED_PERMISSIONS: set[str] = set()
_DECLARED_MODULES: set[str] = set()
_DECLARED_FEATURES: set[str] = set()


def verify_declared_requirements(registry: ModuleRegistry) -> None:
    """Échec au démarrage si une route exige une permission, un module ou une fonctionnalité
    inconnus du registre (faute de frappe, module retiré…)."""
    errors = [
        f"permission inconnue : {c}" for c in _DECLARED_PERMISSIONS if not registry.permission(c)
    ]
    errors += [f"module inconnu : {c}" for c in _DECLARED_MODULES if c not in registry]
    errors += [
        f"fonctionnalité inconnue : {c}"
        for c in _DECLARED_FEATURES
        if registry.module_of_feature(c) is None
    ]
    if errors:
        raise ValueError("Exigences d'autorisation invalides : " + ", ".join(sorted(errors)))


def require_permission(code: str) -> Callable[[RequestContext], RequestContext]:
    _DECLARED_PERMISSIONS.add(code)

    def dependency(ctx: TenantContext) -> RequestContext:
        ctx.requirements.append(frozenset({code}))
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


def require_any_permission(*codes: str) -> Callable[[RequestContext], RequestContext]:
    """Au moins une des permissions (ex. choisir un motif de sortie sans l'administrer)."""
    _DECLARED_PERMISSIONS.update(codes)

    def dependency(ctx: TenantContext) -> RequestContext:
        ctx.requirements.append(frozenset(codes))
        if any(code in ctx.capabilities.permissions for code in codes):
            return ctx
        if any(code in ctx.capabilities.restricted_permissions for code in codes):
            raise ForbiddenError(
                "Action indisponible avec le statut actuel de l'abonnement",
                code="subscription_restricted",
            )
        raise ForbiddenError("Permission insuffisante", code="permission_denied")

    return dependency


def require_module(
    code: str, registry: ModuleRegistry | None = None
) -> Callable[[RequestContext], RequestContext]:
    _DECLARED_MODULES.add(code)

    def dependency(ctx: TenantContext) -> RequestContext:
        manifest_registry = registry or get_registry()
        if (
            code not in ctx.capabilities.modules
            or manifest_registry.get(code).status != ModuleStatus.AVAILABLE
        ):
            raise ForbiddenError("Module non disponible", code="module_unavailable")
        return ctx

    return dependency


def require_feature(code: str) -> Callable[[RequestContext], RequestContext]:
    """Exige une fonctionnalité optionnelle du plan (ex. ``stock.transfers``)."""
    _DECLARED_FEATURES.add(code)

    def dependency(ctx: TenantContext) -> RequestContext:
        if code not in ctx.capabilities.features:
            raise ForbiddenError(
                "Fonctionnalité non incluse dans votre abonnement", code="feature_unavailable"
            )
        return ctx

    return dependency
