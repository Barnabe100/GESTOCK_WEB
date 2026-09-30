import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.platform.sequences.models import DocumentSequence


def next_number(db: Session, tenant_id: uuid.UUID, key: str, prefix: str, width: int = 6) -> str:
    """Numéro suivant pour ``key`` dans le tenant (ex. ``ENT-000001``).

    Un seul ordre SQL atomique (``INSERT … ON CONFLICT DO UPDATE … RETURNING``) : la ligne du
    compteur reste verrouillée jusqu'à la fin de la transaction, ce qui sérialise les créations
    concurrentes. Si la transaction échoue, l'incrément est annulé avec elle.
    """
    stmt = (
        insert(DocumentSequence)
        .values(tenant_id=tenant_id, sequence_key=key, next_value=1)
        .on_conflict_do_update(
            index_elements=[DocumentSequence.tenant_id, DocumentSequence.sequence_key],
            set_={"next_value": DocumentSequence.next_value + 1},
        )
        .returning(DocumentSequence.next_value)
    )
    value = db.execute(stmt).scalar_one()
    return f"{prefix}-{value:0{width}d}"


def site_sequence_key(site_id: uuid.UUID, kind: str, year: int) -> str:
    """Clé d'un compteur par site et par année (ex. numéros de vente ``VENT-{SITE}-{ANNÉE}-…``).
    Le préfixe ``{site_id}:`` permet de savoir si un site a déjà émis des numéros."""
    return f"{site_id}:{kind}:{year}"


def site_has_numbers(db: Session, site_id: uuid.UUID) -> bool:
    """Le site a déjà émis au moins un numéro portant son code (le code devient alors stable)."""
    stmt = select(DocumentSequence.sequence_key).where(
        DocumentSequence.sequence_key.startswith(f"{site_id}:")
    )
    return db.scalar(stmt.limit(1)) is not None
