"""Notifications des membres (ADR-0049 D8, palier R4) — émission et résolution.

- **Émission** : par le module dont une action provoque l'avis, DANS sa transaction et après ses
  propres verrous (``RESTAURANT.md`` §7 : insertion des notifications en dernier, sans verrou).
  Le module calcule les destinataires (ses règles) ; la plateforme écarte toujours l'auteur de
  l'action et insère au plus UNE notification par destinataire et par évènement source (clé
  anti-doublon, index unique) : rejouer l'émission ne crée rien.
- **Résolution** (« traitée ») : calculée à la LECTURE par le module émetteur (port enregistré
  par type), jamais écrite — aucune écriture sur les notifications des autres.
"""

import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.platform.context import RequestContext
from app.platform.member_notifications.models import MemberNotification
from app.shared.ids import new_id

# ``resolver(db, notifications)`` → ``{identifiant: traitée ?}`` pour les notifications de SON
# type ; une notification absente du résultat est « non déterminée » (``None``).
NotificationResolver = Callable[[Session, Sequence[MemberNotification]], dict[uuid.UUID, bool]]

_resolvers: dict[str, NotificationResolver] = {}

DUPLICATE_KEY = "uq_member_notifications_source"


def register_notification_resolver(kind: str, resolver: NotificationResolver) -> None:
    """Appelé par le module émetteur à son chargement."""
    _resolvers[kind] = resolver


def resolutions(
    db: Session, notifications: Sequence[MemberNotification]
) -> dict[uuid.UUID, bool | None]:
    by_kind: dict[str, list[MemberNotification]] = {}
    for notification in notifications:
        by_kind.setdefault(notification.kind, []).append(notification)
    result: dict[uuid.UUID, bool | None] = {n.id: None for n in notifications}
    for kind, items in by_kind.items():
        resolver = _resolvers.get(kind)
        if resolver is not None:
            result.update(resolver(db, items))
    return result


def notify_members(
    db: Session,
    ctx: RequestContext,
    *,
    site_id: uuid.UUID,
    recipients: Iterable[uuid.UUID],
    kind: str,
    source_type: str,
    source_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    payload: dict[str, Any],
    now: datetime,
) -> list[uuid.UUID]:
    """Notifie ``recipients`` (sauf l'auteur de l'action, toujours) ; renvoie les destinataires
    effectivement notifiés par cet appel. Idempotent par (destinataire, évènement source)."""
    targets = sorted(set(recipients) - {ctx.user.id})
    created: list[uuid.UUID] = []
    for user_id in targets:
        # Une insertion par destinataire dans un point de sauvegarde : la clé anti-doublon est
        # appliquée par l'index unique. ``ON CONFLICT`` est inutilisable ici — PostgreSQL y
        # applique la politique de LECTURE, réservée au destinataire (RLS restrictive) —, et
        # l'auteur ne peut pas vérifier l'existence d'une notification qu'il ne voit pas.
        try:
            with db.begin_nested():
                db.execute(
                    insert(MemberNotification).values(
                        id=new_id(),
                        tenant_id=ctx.tenant_id,
                        site_id=site_id,
                        recipient_user_id=user_id,
                        kind=kind,
                        source_type=source_type,
                        source_id=source_id,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        payload=payload,
                        created_at=now,
                    )
                )
        except IntegrityError as exc:
            if DUPLICATE_KEY not in str(exc.orig):
                raise
            continue  # déjà notifié pour cet évènement : rien de plus
        created.append(user_id)
    return created
