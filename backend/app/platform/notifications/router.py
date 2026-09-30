"""Notifications applicatives vues par l'entreprise (Phase 3.3-B4, ADR-0036).

Un membre ne voit que les notifications ``SENT`` des sites auxquels il a accès **et** sur
lesquels il détient le droit de consulter l'abonnement (``subscription.subscription.view``,
revérifié site par site) ; l'état lu / non lu est propre à chaque membre. Aucune route ne
crée, ne modifie ni ne supprime une notification (écrites par le job de la plateforme).
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import Select, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import NotFoundError
from app.platform.context import DbSession, NowDep, RequestContext, require_permission
from app.platform.notifications.models import Notification, NotificationRead, NotificationStatus
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, page_params, paginate_rows
from app.shared.schemas import Page

router = APIRouter(tags=["notifications"])

VIEW = "subscription.subscription.view"
View = Annotated[RequestContext, Depends(require_permission(VIEW))]


class NotificationSite(BaseModel):
    id: uuid.UUID
    name: str
    code: str


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    step: int
    reference_date: date
    site: NotificationSite | None
    subscription_id: uuid.UUID | None
    data: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class UnreadCount(BaseModel):
    unread: int


def visible_sites(ctx: RequestContext) -> set[uuid.UUID]:
    """Sites accessibles sur lesquels le membre détient le droit de consulter l'abonnement."""
    sites = {ctx.site.id} if ctx.site else set(ctx.capabilities.accessible_site_ids)
    visible: set[uuid.UUID] = set()
    for site_id in sites:
        caps = ctx.capabilities if ctx.site else ctx.site_capabilities(site_id)
        if VIEW in caps.permissions or VIEW in caps.restricted_permissions:
            visible.add(site_id)
    return visible


def _visible(ctx: RequestContext) -> Select[Any]:
    read = (
        select(NotificationRead.read_at)
        .where(
            NotificationRead.notification_id == Notification.id,
            NotificationRead.user_id == ctx.user.id,
        )
        .correlate(Notification)
        .scalar_subquery()
    )
    return (
        select(Notification, Site.name, Site.code, read.label("read_at"))
        .select_from(Notification)
        .outerjoin(Site, Site.id == Notification.site_id)
        .where(
            Notification.status == NotificationStatus.SENT,
            # Notifications d'un site : sites visibles ; de l'entreprise (sans site) : vue
            # sans site sélectionné seulement.
            or_(Notification.site_id.in_(visible_sites(ctx)), Notification.site_id.is_(None))
            if ctx.site is None
            else Notification.site_id.in_(visible_sites(ctx)),
        )
    )


def _out(row: Any) -> NotificationOut:
    n = row.Notification
    return NotificationOut(
        id=n.id,
        kind=n.kind,
        step=n.step,
        reference_date=n.reference_date,
        site=NotificationSite(id=n.site_id, name=row.name, code=row.code) if n.site_id else None,
        subscription_id=n.subscription_id,
        data=n.data,
        created_at=n.created_at,
        read_at=row.read_at,
    )


@router.get("/notifications", response_model=Page[NotificationOut])
def list_notifications(
    ctx: View,
    db: DbSession,
    params: Annotated[PageParams, Depends(page_params)],
    unread: bool = False,
    site_id: uuid.UUID | None = None,
) -> Page[NotificationOut]:
    """Notifications visibles (plus récentes d'abord) ; filtres ``unread``, ``site_id``."""
    stmt = _visible(ctx)
    if site_id is not None:
        stmt = stmt.where(Notification.site_id == site_id)
    if unread:
        stmt = stmt.where(
            ~select(NotificationRead.notification_id)
            .where(
                NotificationRead.notification_id == Notification.id,
                NotificationRead.user_id == ctx.user.id,
            )
            .exists()
        )
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.step, Notification.id)
    rows, total = paginate_rows(db, stmt, params)
    return Page(
        items=[_out(r) for r in rows], total=total, limit=params.limit, offset=params.offset
    )


@router.get("/notifications/unread-count", response_model=UnreadCount)
def unread_count(ctx: View, db: DbSession) -> UnreadCount:
    visible = _visible(ctx).where(
        ~select(NotificationRead.notification_id)
        .where(
            NotificationRead.notification_id == Notification.id,
            NotificationRead.user_id == ctx.user.id,
        )
        .exists()
    )
    count = db.scalar(select(func.count()).select_from(visible.subquery())) or 0
    return UnreadCount(unread=count)


def _mark(db: DbSession, ctx: RequestContext, ids: list[uuid.UUID], now: datetime) -> None:
    if not ids:
        return
    db.execute(
        insert(NotificationRead)
        .values(
            [
                {
                    "tenant_id": ctx.tenant_id,
                    "notification_id": i,
                    "user_id": ctx.user.id,
                    "read_at": now,
                }
                for i in ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["notification_id", "user_id"])
    )


@router.post("/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(notification_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> Response:
    """Marque une notification visible comme lue par le membre (idempotent)."""
    found = db.execute(_visible(ctx).where(Notification.id == notification_id)).first()
    if found is None:
        raise NotFoundError("Notification introuvable", code="notification_not_found")
    _mark(db, ctx, [notification_id], now)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/notifications/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(ctx: View, db: DbSession, now: NowDep) -> Response:
    ids = [row.Notification.id for row in db.execute(_visible(ctx)).all() if row.read_at is None]
    _mark(db, ctx, ids, now)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
