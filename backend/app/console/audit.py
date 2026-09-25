"""Journal d'audit de la plateforme (actions TechNova), écrit dans la transaction de l'action."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.console.models import PlatformAuditLog
from app.platform.audit.service import RequestMeta


@dataclass(frozen=True)
class PlatformActor:
    """Auteur d'une action de la plateforme : un administrateur TechNova ou la CLI."""

    user_id: uuid.UUID | None
    label: str


CLI_ACTOR = PlatformActor(user_id=None, label="cli")


def plain(value: Any) -> Any:
    """Valeur sérialisable en JSON (montants en chaînes, jamais en flottants)."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def record_platform_audit(
    db: Session,
    *,
    actor: PlatformActor | None,
    action: str,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
    tenant_id: uuid.UUID | None = None,
    meta: RequestMeta | None = None,
) -> PlatformAuditLog:
    """Ajoute une entrée au journal de la plateforme.

    ``tenant_id`` : tenant directement concerné (Phase 3.2-G) ; l'opération écrira alors aussi
    une entrée miroir dans le journal de ce tenant. Aucune n'est écrite pour une action de
    portée plateforme (ex. tarif d'un plan)."""
    entry = PlatformAuditLog(
        actor_user_id=actor.user_id if actor else None,
        actor_label=actor.label if actor else "anonymous",
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        tenant_id=tenant_id,
        before={k: plain(v) for k, v in (before or {}).items()},
        after={k: plain(v) for k, v in (after or {}).items()},
        reason=reason,
        data=data or {},
        ip_address=meta.ip_address if meta else None,
        user_agent=meta.user_agent[:500] if meta and meta.user_agent else None,
    )
    db.add(entry)
    return entry
