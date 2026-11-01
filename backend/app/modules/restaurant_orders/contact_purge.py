"""Effacement des téléphones des clients (palier R7, ADR-0052) : 30 jours après la fin d'une
commande (close, annulée ou refusée), par la tâche quotidienne
``stockmanager notifications purge`` et le rôle SQL DÉDIÉ ``stockmanager_purge``.

Ce rôle ne voit que les commandes finies depuis plus de 30 jours qui portent encore un
téléphone, et ne peut qu'effacer ce téléphone (politiques et droits de la migration 0057, quelle
que soit la requête envoyée) ; le déclencheur des états finaux n'admet que cet effacement sur une
commande finie. Idempotent ; deux exécutions simultanées effacent au plus une fois."""

from sqlalchemy import text
from sqlalchemy.orm import Session

# Durée figée dans la migration 0057 (politique du rôle de purge) ; rappelée ici pour le message.
CONTACT_RETENTION_DAYS = 30


def erase_expired_contacts(db: Session) -> int:
    result = db.execute(
        text(
            "UPDATE restaurant_orders SET contact_phone = NULL "
            "WHERE contact_phone IS NOT NULL "
            "AND status IN ('CLOSED', 'CANCELLED', 'REJECTED') "
            "AND coalesce(closed_at, cancelled_at) < now() - make_interval(days => :days)"
        ),
        {"days": CONTACT_RETENTION_DAYS},
    )
    return int(getattr(result, "rowcount", 0) or 0)
