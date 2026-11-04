"""Verrou d'une entreprise pendant l'application d'une restauration (R11, ADR-0058).

Verrou CONSULTATIF PostgreSQL, clé ``hashtextextended('sm.tenant_restore:<entreprise>', 0)``
(même convention que les autres verrous de l'application) — aucune colonne, aucune table :

- chaque requête qui établit le contexte d'une entreprise (API des entreprises, accès public par
  QR Code) prend ce verrou en mode PARTAGÉ pour sa transaction
  (``pg_try_advisory_xact_lock_shared``) : les requêtes ne se gênent jamais entre elles ;
- l'agent des sauvegardes le prend en mode EXCLUSIF de session avant d'appliquer une
  restauration : il ATTEND la fin des requêtes en cours de cette entreprise, puis toute requête
  suivante n'obtient pas le verrou partagé et est refusée (503 ``tenant_restore_in_progress`` ;
  refus générique pour le public). Une session de l'agent interrompue libère le verrou ;
- les autres entreprises ont d'autres clés : jamais concernées."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ServiceUnavailableError

LOCK_NAMESPACE = "sm.tenant_restore:"


class TenantRestoreInProgressError(ServiceUnavailableError):
    code = "tenant_restore_in_progress"
    title = "Restauration en cours"


def restore_lock_key(tenant_id: uuid.UUID) -> str:
    return f"{LOCK_NAMESPACE}{tenant_id}"


def tenant_available(db: Session, tenant_id: uuid.UUID) -> bool:
    """Vrai si aucune restauration n'est appliquée à cette entreprise ; le verrou partagé est
    alors tenu jusqu'à la fin de la transaction de la requête."""
    return bool(
        db.scalar(
            select(
                func.pg_try_advisory_xact_lock_shared(
                    func.hashtextextended(restore_lock_key(tenant_id), 0)
                )
            )
        )
    )


def ensure_not_restoring(db: Session, tenant_id: uuid.UUID) -> None:
    if not tenant_available(db, tenant_id):
        raise TenantRestoreInProgressError(
            "Restauration de l'entreprise en cours : réessayer dans quelques minutes",
            code="tenant_restore_in_progress",
        )
