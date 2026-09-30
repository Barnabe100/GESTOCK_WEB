"""Permissions évaluées **pour un site** (Lot 1) : un rôle limité à un site ne vaut que sur ce
site. Jamais de test sur un nom de rôle : seules comptent les permissions accordées."""

import uuid

from app.platform.context import RequestContext


def site_can(ctx: RequestContext, site_id: uuid.UUID, code: str) -> bool:
    """Le membre détient ``code`` sur ``site_id`` (capacités résolues pour ce site)."""
    return ctx.has_site_permission(site_id, code)
