import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.platform.audit.models import AuditLog

if TYPE_CHECKING:
    from app.platform.context import RequestContext


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


def audit_action(
    session: Session,
    ctx: "RequestContext",
    action: str,
    *,
    entity_type: str,
    entity_id: uuid.UUID | str | None,
    data: dict[str, Any] | None = None,
    site_id: uuid.UUID | None = None,
) -> AuditLog:
    """Raccourci pour une action faite dans un contexte tenant (utilisateur, site, requête).
    ``site_id`` : site concerné par l'opération (par défaut, le site sélectionné)."""
    return record_audit(
        session,
        action=action,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user.id,
        site_id=site_id or (ctx.site.id if ctx.site else None),
        entity_type=entity_type,
        entity_id=entity_id,
        data=data,
        meta=ctx.meta,
    )


def changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Différences avant/après, sérialisables en JSON, pour le journal d'audit."""

    def plain(value: Any) -> Any:
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, (uuid.UUID,)) or type(value).__name__ == "Decimal":
            return str(value)
        return value

    diff = {k: {"before": plain(before.get(k)), "after": plain(v)} for k, v in after.items()}
    return {k: v for k, v in diff.items() if v["before"] != v["after"]}
