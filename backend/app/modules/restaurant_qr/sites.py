"""Portée des sites des points QR (palier R7, ADR-0052).

Mêmes règles que le menu (``restaurant_menu/sites.py``) : un site n'est lisible que si le
module y est EFFECTIF pour le membre (``restaurant.qr.point.view`` résolu pour CE site —
modules effectifs, abonnement et rôles du site) ; une écriture vise le site sélectionné, sinon
le site fourni, revérifié pour l'abonnement et les modules de CE site."""

import uuid

from app.core.errors import BusinessRuleError, ForbiddenError, NotFoundError
from app.modules.restaurant_qr.permissions import POINT_VIEW
from app.platform.context import RequestContext


def readable_site_ids(ctx: RequestContext, site_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Sites dont le membre lit les points QR (un site non lisible donne une liste
    vide, jamais une erreur)."""
    visible = {ctx.site.id} if ctx.site is not None else set(ctx.capabilities.accessible_site_ids)
    if site_id is not None:
        visible &= {site_id}
    return {site for site in visible if ctx.has_site_permission(site, POINT_VIEW)}


def operation_site(ctx: RequestContext, site_id: uuid.UUID | None) -> uuid.UUID:
    """Site d'une nouvelle écriture : le site sélectionné (``X-Site-Id``), sinon celui fourni,
    accessible au membre et autorisé par l'abonnement et les modules de CE site."""
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


def ensure_row_site(ctx: RequestContext, site_id: uuid.UUID, not_found_code: str) -> None:
    """Ligne existante (point QR) : introuvable si son site n'est pas lisible par le
    membre ; refusée si elle appartient à un autre site que le site sélectionné ; une écriture
    est revérifiée pour l'abonnement et les modules de son site."""
    if site_id not in ctx.capabilities.accessible_site_ids or not ctx.has_site_permission(
        site_id, POINT_VIEW
    ):
        raise NotFoundError("Élément introuvable", code=not_found_code)
    if ctx.site is not None and ctx.site.id != site_id:
        raise ForbiddenError("Cet élément appartient à un autre site", code="site_mismatch")
    ctx.ensure_site_allows(site_id)


def site_modules(ctx: RequestContext, site_id: uuid.UUID) -> frozenset[str]:
    """Modules EFFECTIFS de ``site_id`` pour le membre (capacités résolues pour CE site)."""
    if ctx.site is not None and ctx.site.id == site_id:
        return frozenset(ctx.capabilities.modules)
    return frozenset(ctx.site_capabilities(site_id).modules)
