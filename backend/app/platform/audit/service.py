import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.platform.audit.models import AuditLog


@dataclass(frozen=True)
class RequestMeta:
    ip_address: str | None = None
    user_agent: str | None = None


def record_audit(
    session: Session,
    *,
    action: str,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    site_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    data: dict[str, Any] | None = None,
    meta: RequestMeta | None = None,
) -> AuditLog:
    """Ajoute une entrée au journal, dans la transaction de l'opération auditée."""
    entry = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        site_id=site_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        data=data or {},
        ip_address=meta.ip_address if meta else None,
        user_agent=meta.user_agent[:500] if meta and meta.user_agent else None,
    )
    session.add(entry)
    return entry
