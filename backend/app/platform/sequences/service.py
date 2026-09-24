import uuid

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
