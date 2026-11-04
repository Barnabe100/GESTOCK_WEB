"""Verrou d'une entreprise pendant l'application d'une restauration (R11, ADR-0058).

Chaque requête qui établit le contexte d'une entreprise (API des entreprises, accès public par
QR Code) charge sa ligne ``tenants`` sous ``FOR KEY SHARE`` : ce verrou partagé ne gêne aucune
autre requête (ni les mises à jour ordinaires de l'entreprise), mais l'agent des sauvegardes,
qui pose le verrou de restauration après un ``SELECT … FOR UPDATE``, ATTEND la fin de toutes les
requêtes en cours de cette entreprise. Une requête commencée ensuite lit le verrou et est
refusée (503 ``tenant_restore_in_progress`` ; refus générique pour le public) : aucune écriture
de l'entreprise ne peut se mêler à l'application de la sauvegarde. Les autres entreprises ne
sont jamais concernées."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ServiceUnavailableError
from app.platform.tenancy.models import Tenant


class TenantRestoreInProgressError(ServiceUnavailableError):
    code = "tenant_restore_in_progress"
    title = "Restauration en cours"


def load_tenant_for_request(db: Session, tenant_id: uuid.UUID) -> Tenant | None:
    """Entreprise relue (jamais depuis le cache de la session) sous ``FOR KEY SHARE``."""
    return db.scalars(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update(read=True, key_share=True)
        .execution_options(populate_existing=True)
    ).one_or_none()


def ensure_not_restoring(tenant: Tenant) -> None:
    if tenant.restore_locked_at is not None:
        raise TenantRestoreInProgressError(
            "Restauration de l'entreprise en cours : réessayer dans quelques minutes",
            code="tenant_restore_in_progress",
        )
