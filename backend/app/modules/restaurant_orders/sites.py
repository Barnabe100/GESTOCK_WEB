"""Portée des sites des commandes (palier R2, ADR-0049) — mêmes règles que le menu (R1).

Un site n'est lisible que si les commandes y sont EFFECTIVES pour le membre
(``restaurant.orders.order.view`` résolu pour CE site : modules effectifs, abonnement et rôles du
site). Toute écriture est revérifiée pour le site visé (``ensure_site_allows``) et la permission
précise de l'action est exigée sur CE site."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, ForbiddenError, NotFoundError
from app.modules.restaurant_orders.permissions import ORDER_VIEW
from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.capabilities.service import CapabilityService, SubscriptionMissingError
from app.platform.context import RequestContext
from app.platform.registry import get_registry


def readable_site_ids(ctx: RequestContext, site_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Sites dont le membre lit les commandes : le site sélectionné, sinon ses sites accessibles,
    restreints au filtre, puis aux sites où les commandes sont effectives pour lui."""
    visible = {ctx.site.id} if ctx.site is not None else set(ctx.capabilities.accessible_site_ids)
    if site_id is not None:
        visible &= {site_id}
    return {site for site in visible if ctx.has_site_permission(site, ORDER_VIEW)}


def operation_site(ctx: RequestContext, site_id: uuid.UUID | None) -> uuid.UUID:
    """Site d'une nouvelle écriture : le site sélectionné, sinon celui fourni, accessible ;
    l'abonnement et les modules de CE site doivent autoriser l'écriture."""
    if ctx.site is not None:
        if site_id is not None and site_id != ctx.site.id:
            raise ForbiddenError(
                "Le site ne correspond pas au site sélectionné", code="site_mismatch"
            )
        return ctx.site.id
    if site_id is None:
        raise BusinessRuleError("Choisissez un site", code="site_required")
    if site_id not in ctx.capabilities.accessible_site_ids:
        raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")
    ctx.ensure_site_allows(site_id)
    return site_id


def ensure_row_site(ctx: RequestContext, site_id: uuid.UUID, *, write: bool) -> None:
    """Commande existante : introuvable si son site n'est pas lisible ; refusée si elle relève
    d'un autre site que le site sélectionné ; une écriture est revérifiée pour son site."""
    if site_id not in ctx.capabilities.accessible_site_ids or not ctx.has_site_permission(
        site_id, ORDER_VIEW
    ):
        raise NotFoundError("Commande introuvable", code="order_not_found")
    if ctx.site is not None and ctx.site.id != site_id:
        raise ForbiddenError("Cette commande appartient à un autre site", code="site_mismatch")
    if write:
        ctx.ensure_site_allows(site_id)


def require_site_permission(ctx: RequestContext, site_id: uuid.UUID, code: str) -> None:
    """Permission précise de l'action sur CE site (union des rôles du membre pour le site ;
    jamais de test d'un nom de rôle)."""
    if not ctx.has_site_permission(site_id, code):
        raise ForbiddenError(
            "Permission insuffisante", code="permission_denied", extra={"permission": code}
        )


def member_holds(
    db: Session,
    ctx: RequestContext,
    user_id: uuid.UUID,
    site_id: uuid.UUID,
    permission: str,
    now: datetime,
) -> bool:
    """Un AUTRE membre détient-il ``permission`` effective sur ce site ? Appartenance active à
    l'entreprise, compte actif, site accessible, puis capacités résolues pour CE site (rôles du
    tenant et du site, modules effectifs, abonnement du site et son statut) — le même calcul que
    pour ses propres requêtes, jamais un nom de rôle."""
    membership = db.scalars(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id, TenantMembership.user_id == user_id
        )
    ).one_or_none()
    if (
        membership is None
        or membership.status is not MembershipStatus.ACTIVE
        or not membership.user.is_active
    ):
        return False
    service = CapabilityService(db, get_registry())
    if site_id not in service.accessible_site_ids(membership):
        return False
    try:
        capabilities = service.resolve(
            tenant=ctx.tenant, membership=membership, site_id=site_id, now=now
        )
    except SubscriptionMissingError:
        return False
    return permission in capabilities.permissions
