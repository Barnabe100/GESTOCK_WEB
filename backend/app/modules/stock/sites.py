"""Site d'une opération de stock et date du jour du tenant (§6.6 du plan)."""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.errors import BusinessRuleError, ForbiddenError, NotFoundError
from app.platform.context import RequestContext


def operation_site(ctx: RequestContext, site_id: uuid.UUID | None) -> uuid.UUID:
    """Site sur lequel porte une nouvelle opération : le site sélectionné (``X-Site-Id``), sinon
    celui fourni, qui doit être actif et accessible au membre."""
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
    return site_id


def visible_site_ids(ctx: RequestContext) -> set[uuid.UUID]:
    """Sites dont le membre voit les données : le site sélectionné, sinon ses sites."""
    if ctx.site is not None:
        return {ctx.site.id}
    return set(ctx.capabilities.accessible_site_ids)


def sees_all_sites(ctx: RequestContext) -> bool:
    """Vue consolidée de tous les sites du tenant : membre sans restriction de site (propriétaire
    ou « tous les sites ») et aucun site sélectionné. Sinon, les données se limitent aux sites
    visibles (``visible_site_ids``)."""
    return ctx.site is None and (ctx.membership.is_owner or ctx.membership.all_sites)


def filter_site_ids(ctx: RequestContext, site_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Sites visibles, restreints au site demandé en filtre (vide s'il n'est pas visible)."""
    visible = visible_site_ids(ctx)
    return visible & {site_id} if site_id is not None else visible


def ensure_document_site(ctx: RequestContext, site_id: uuid.UUID, not_found_code: str) -> None:
    """Un document d'un site non accessible est introuvable ; d'un autre site que le site
    sélectionné, refusé (les rôles limités à un site ne valent que sur ce site)."""
    if site_id not in ctx.capabilities.accessible_site_ids:
        raise NotFoundError("Document introuvable", code=not_found_code)
    if ctx.site is not None and ctx.site.id != site_id:
        raise ForbiddenError("Ce document appartient à un autre site", code="site_mismatch")


def tenant_today(ctx: RequestContext, now: datetime) -> date:
    """Date du jour dans le fuseau horaire du tenant (ENT-02 adapté)."""
    return now.astimezone(ZoneInfo(ctx.tenant.timezone)).date()
