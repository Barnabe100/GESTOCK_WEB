"""Portée des sites du menu (palier R1, ADR-0049).

Mêmes règles que les autres données par site, sans dépendre d'un autre module métier (le menu ne
dépend que du catalogue) ; un filtre de plus : un site n'est lisible que si le menu y est
EFFECTIF pour le membre (``restaurant.menu.view`` résolu pour CE site — modules effectifs,
abonnement et rôles du site). ``require_module`` du routeur ne contrôle que le site sélectionné
ou, sans site sélectionné, l'ensemble des sites accessibles."""

import uuid

from app.core.errors import BusinessRuleError, ForbiddenError, NotFoundError
from app.platform.context import RequestContext

VIEW = "restaurant.menu.view"


def readable_site_ids(ctx: RequestContext, site_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Sites dont le membre lit le menu : le site sélectionné, sinon ses sites accessibles,
    restreints au site demandé en filtre, puis aux sites où le menu est effectif pour lui
    (un site non lisible donne une liste vide, jamais une erreur)."""
    visible = {ctx.site.id} if ctx.site is not None else set(ctx.capabilities.accessible_site_ids)
    if site_id is not None:
        visible &= {site_id}
    return {site for site in visible if ctx.has_site_permission(site, VIEW)}


def operation_site(ctx: RequestContext, site_id: uuid.UUID | None) -> uuid.UUID:
    """Site d'une nouvelle écriture : le site sélectionné (``X-Site-Id``), sinon celui fourni,
    accessible au membre. L'abonnement ET les modules effectifs de CE site doivent autoriser
    l'écriture (``ensure_site_allows`` : permission requise résolue pour ce site)."""
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
    """Ligne existante (section, élément) : introuvable si son site n'est pas lisible par le
    membre ; refusée si elle appartient à un autre site que le site sélectionné ; une écriture
    est revérifiée pour l'abonnement et les modules de son site."""
    if site_id not in ctx.capabilities.accessible_site_ids or not ctx.has_site_permission(
        site_id, VIEW
    ):
        raise NotFoundError("Élément introuvable", code=not_found_code)
    if ctx.site is not None and ctx.site.id != site_id:
        raise ForbiddenError("Cet élément appartient à un autre site", code="site_mismatch")
    ctx.ensure_site_allows(site_id)
