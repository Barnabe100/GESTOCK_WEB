"""Motifs de sortie système créés pour chaque entreprise (Q7)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.stock.models import ExitReason

SYSTEM_EXIT_REASONS: tuple[tuple[str, str, str], ...] = (
    ("CONSOMMATION_INTERNE", "Consommation interne", "Utilisation par l'entreprise elle-même."),
    ("DOTATION", "Dotation", "Remise à un service, un employé ou un bénéficiaire."),
    ("PERTE", "Perte", "Marchandise perdue ou disparue."),
    ("CASSE", "Casse", "Marchandise endommagée ou détruite."),
    ("ECHANTILLON", "Échantillon", "Échantillon ou article de démonstration."),
    ("AUTRE", "Autre", "Autre motif (à préciser en commentaire)."),
)


def ensure_system_exit_reasons(db: Session, tenant_id: uuid.UUID) -> None:
    """Crée les motifs système manquants du tenant (idempotent)."""
    existing = set(db.scalars(select(ExitReason.code).where(ExitReason.is_system.is_(True))))
    for code, label, description in SYSTEM_EXIT_REASONS:
        if code not in existing:
            db.add(
                ExitReason(
                    tenant_id=tenant_id,
                    code=code,
                    label=label,
                    description=description,
                    is_system=True,
                )
            )
