"""Notifications des membres (ADR-0049 D8, palier R4) — lecture par leur destinataire.

Un membre ne lit que SES notifications (RLS restrictive sur ``app.user_id`` et filtre
explicite), des sites auxquels il a accès et sur lesquels il détient
``organization.notification.view`` (revérifié site par site) ; jamais celles d'un autre membre,
d'un autre site ni d'une autre entreprise. Lu / non lu propre au destinataire ; « traitée »
calculée à la lecture. Aucune route ne crée ni ne supprime une notification. Interrogation
périodique par l'interface (10 à 15 s, aucun WebSocket en V1).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import Select, func, select, update

from app.core.errors import NotFoundError
from app.platform.context import DbSession, NowDep, RequestContext, require_permission
from app.platform.member_notifications.models import MemberNotification
from app.platform.member_notifications.service import READ_PERMISSION, resolutions
from app.platform.notifications.router import NotificationSite
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, page_params, paginate_rows
from app.shared.schemas import Page

router = APIRouter(tags=["member-notifications"])

VIEW = READ_PERMISSION
View = Annotated[RequestContext, Depends(require_permission(VIEW))]


class MemberNotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    site: NotificationSite
    entity_type: str
    entity_id: uuid.UUID
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None
    # Calculée à la lecture par le module émetteur (``None`` : non déterminée).
    resolved: bool | None


class UnreadCount(BaseModel):
    unread: int


def visible_sites(ctx: RequestContext) -> set[uuid.UUID]:
    """Sites accessibles (le site sélectionné, sinon tous) où le membre détient le droit de
    consulter ses notifications."""
    sites = {ctx.site.id} if ctx.site else set(ctx.capabilities.accessible_site_ids)
    return {site_id for site_id in sites if ctx.has_site_permission(site_id, VIEW)}


def _mine(ctx: RequestContext) -> Select[Any]:
    return (
        select(MemberNotification, Site.name, Site.code)
        .join(Site, Site.id == MemberNotification.site_id)
        .where(
            MemberNotification.tenant_id == ctx.tenant_id,
            MemberNotification.recipient_user_id == ctx.user.id,
            MemberNotification.site_id.in_(visible_sites(ctx)),
        )
    )


@router.get("/member-notifications", response_model=Page[MemberNotificationOut])
def list_member_notifications(
    ctx: View,
    db: DbSession,
    params: Annotated[PageParams, Depends(page_params)],
    unread: bool = False,
    site_id: uuid.UUID | None = None,
) -> Page[MemberNotificationOut]:
    """Mes notifications (plus récentes d'abord) ; filtres ``unread``, ``site_id``."""
    stmt = _mine(ctx)
    if site_id is not None:
        stmt = stmt.where(MemberNotification.site_id == site_id)
    if unread:
        stmt = stmt.where(MemberNotification.read_at.is_(None))
    stmt = stmt.order_by(MemberNotification.created_at.desc(), MemberNotification.id.desc())
    rows, total = paginate_rows(db, stmt, params)
    resolved = resolutions(db, [row.MemberNotification for row in rows])
    items = [
        MemberNotificationOut(
            id=n.id,
            kind=n.kind,
            site=NotificationSite(id=n.site_id, name=row.name, code=row.code),
            entity_type=n.entity_type,
            entity_id=n.entity_id,
            payload=n.payload,
            created_at=n.created_at,
            read_at=n.read_at,
            resolved=resolved[n.id],
        )
        for row in rows
        for n in (row.MemberNotification,)
    ]
    return Page(items=items, total=total, limit=params.limit, offset=params.offset)


@router.get("/member-notifications/unread-count", response_model=UnreadCount)
def member_unread_count(ctx: View, db: DbSession) -> UnreadCount:
    unread = _mine(ctx).where(MemberNotification.read_at.is_(None))
    return UnreadCount(unread=db.scalar(select(func.count()).select_from(unread.subquery())) or 0)


@router.post("/member-notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_member_notification_read(
    notification_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep
) -> Response:
    """Marque MA notification comme lue (idempotent : déjà lue, inchangée)."""
    found = db.execute(_mine(ctx).where(MemberNotification.id == notification_id)).first()
    if found is None:
        raise NotFoundError("Notification introuvable", code="notification_not_found")
    db.execute(
        update(MemberNotification)
        .where(
            MemberNotification.id == notification_id,
            MemberNotification.recipient_user_id == ctx.user.id,
            MemberNotification.read_at.is_(None),
        )
        .values(read_at=now)
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/member-notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_member_notifications_read(ctx: View, db: DbSession, now: NowDep) -> Response:
    """« Tout marquer lu » : mes notifications non lues des sites visibles."""
    db.execute(
        update(MemberNotification)
        .where(
            MemberNotification.tenant_id == ctx.tenant_id,
            MemberNotification.recipient_user_id == ctx.user.id,
            MemberNotification.site_id.in_(visible_sites(ctx)),
            MemberNotification.read_at.is_(None),
        )
        .values(read_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
