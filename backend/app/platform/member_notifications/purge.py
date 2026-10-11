"""Purge des notifications des membres (ADR-0049 D8, palier R4) :
``stockmanager notifications purge``.

Lancée par cron / systemd (quotidien), avec le rôle SQL DÉDIÉ ``stockmanager_purge`` : il ne voit
que l'identifiant et la date des notifications de plus de 30 jours et ne peut supprimer qu'elles
(politique RLS de la migration 0045, quelle que soit la requête envoyée). Idempotente ; deux
exécutions simultanées suppriment au plus une fois chaque ligne.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.member_notifications.models import RETENTION_DAYS


def purge_expired(db: Session) -> int:
    """Supprime les notifications de plus de ``RETENTION_DAYS`` jours ; renvoie leur nombre."""
    result = db.execute(
        text(
            "DELETE FROM member_notifications "
            "WHERE created_at < now() - make_interval(days => :days)"
        ),
        {"days": RETENTION_DAYS},
    )
    return int(getattr(result, "rowcount", 0) or 0)
