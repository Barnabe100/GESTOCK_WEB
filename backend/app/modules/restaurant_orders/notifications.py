"""Avis « commande prête » (ADR-0049 D8, palier R4) — émis par le moteur des commandes,
portés par le mécanisme générique de la plateforme (``app.platform.member_notifications``).

- **Quand** : transition « prête » (évènement ``READY``) laissant TOUTE la commande prête (sans
  postes de préparation, R6 non livré : un avis par commande). Une transition répétée légitime
  (lignes ajoutées puis prêtes, correction « prête → en préparation → prête ») est un nouvel
  évènement, donc un nouvel avis ; rejouer l'émission d'un même évènement ne crée rien.
- **Destinataires** : le responsable de la commande ET les membres du site détenant
  EFFECTIVEMENT ``restaurant.orders.order.serve`` (capacités résolues pour CE site : appartenance
  active, accès et rôles du site, modules effectifs, abonnement du site), tous devant détenir
  ``restaurant.orders.order.view`` sur ce site ; MOINS l'auteur de l'action, toujours. Candidats
  bornés à l'équipe du site (appartenances actives ayant accès au site).
- **Contexte** (``payload``) : numéro du jour, date, lignes concernées (celles prêtes à servir),
  sans prix, client ni donnée interne.
- **Résolution** calculée à la lecture : « traitée » quand toutes les lignes concernées sont
  servies (ou remises) ou annulées.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_orders.models import (
    FINAL_LINE_STATUSES,
    LineStatus,
    RestaurantOrder,
    RestaurantOrderLine,
)
from app.modules.restaurant_orders.permissions import ORDER_CLAIM, ORDER_SERVE, ORDER_VIEW
from app.modules.restaurant_orders.sites import member_permissions
from app.platform.access.models import MembershipStatus, TenantMembership
from app.platform.access.site_access import site_access_condition
from app.platform.context import RequestContext
from app.platform.member_notifications.models import MemberNotification
from app.platform.member_notifications.service import notify_members

KIND_ORDER_READY = "restaurant.order.ready"
SOURCE_ORDER_EVENT = "restaurant_order_event"
ENTITY_ORDER = "restaurant_order"
# Permissions pouvant rendre destinataire de « commande prête » : ``serve`` (titulaires) et
# ``claim`` (prise en charge : responsable). Déclarées à la plateforme pour l'avertissement de
# cohérence des rôles personnalisés ; le calcul des destinataires ci-dessous reste la règle D8.
READY_RECEIVER_PERMISSIONS = (ORDER_SERVE, ORDER_CLAIM)


def ready_recipients(
    db: Session, ctx: RequestContext, order: RestaurantOrder, now: datetime
) -> list[uuid.UUID]:
    """Responsable + titulaires effectifs de ``order.serve`` sur le site, lecteurs de la
    commande ; l'auteur est retiré par la plateforme (``notify_members``)."""
    candidates = db.scalars(
        select(TenantMembership.user_id).where(
            TenantMembership.tenant_id == ctx.tenant_id,
            TenantMembership.status == MembershipStatus.ACTIVE,
            site_access_condition(order.site_id),
        )
    ).all()
    recipients: list[uuid.UUID] = []
    for user_id in sorted(set(candidates)):
        if user_id == ctx.user.id:
            continue  # jamais l'auteur de l'action
        permissions = member_permissions(db, ctx, user_id, order.site_id, now)
        if ORDER_VIEW not in permissions:
            continue
        if user_id == order.assigned_user_id or ORDER_SERVE in permissions:
            recipients.append(user_id)
    return recipients


def notify_order_ready(
    db: Session,
    ctx: RequestContext,
    order: RestaurantOrder,
    lines: Sequence[RestaurantOrderLine],
    event_id: uuid.UUID,
    now: datetime,
) -> list[uuid.UUID]:
    """Avis de l'évènement ``READY`` ``event_id`` (la commande entière est prête)."""
    ready = [line for line in lines if line.status is LineStatus.READY]
    return notify_members(
        db,
        ctx,
        site_id=order.site_id,
        recipients=ready_recipients(db, ctx, order, now),
        kind=KIND_ORDER_READY,
        source_type=SOURCE_ORDER_EVENT,
        source_id=event_id,
        entity_type=ENTITY_ORDER,
        entity_id=order.id,
        payload={
            "daily_number": order.daily_number,
            "business_date": order.business_date.isoformat(),
            "line_count": len(ready),
            "line_ids": [str(line.id) for line in ready],
        },
        now=now,
    )


def resolve_ready(
    db: Session, notifications: Sequence[MemberNotification]
) -> dict[uuid.UUID, bool]:
    """« Traitée » : toutes les lignes concernées sont servies (remises) ou annulées."""
    wanted = {
        uuid.UUID(line_id)
        for notification in notifications
        for line_id in notification.payload.get("line_ids", [])
    }
    statuses = {
        line_id: status
        for line_id, status in db.execute(
            select(RestaurantOrderLine.id, RestaurantOrderLine.status).where(
                RestaurantOrderLine.id.in_(wanted)
            )
        ).tuples()
    }
    return {
        notification.id: all(
            statuses.get(uuid.UUID(line_id)) in FINAL_LINE_STATUSES
            for line_id in notification.payload.get("line_ids", [])
        )
        for notification in notifications
    }
