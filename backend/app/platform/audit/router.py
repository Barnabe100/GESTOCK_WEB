import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.platform.audit.models import AuditLog
from app.platform.context import DbSession, RequestContext, require_permission
from app.platform.identity.models import User
from app.shared.schemas import Page

router = APIRouter(tags=["audit"])

AuditView = Annotated[RequestContext, Depends(require_permission("audit.log.view"))]


class AuditLogOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    action: str
    user_id: uuid.UUID | None
    user_email: str | None
    user_name: str | None
    site_id: uuid.UUID | None
    entity_type: str | None
    entity_id: str | None
    data: dict[str, Any]
    ip_address: str | None


@router.get("/audit-logs", response_model=Page[AuditLogOut])
def list_audit_logs(
    ctx: AuditView,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Page[AuditLogOut]:
    # RLS : seules les entrées du tenant actif sont visibles ; le filtre explicite documente
    # l'intention et garde les index efficaces.
    conditions = [AuditLog.tenant_id == ctx.tenant_id]
    if action:
        conditions.append(AuditLog.action.startswith(action))
    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    rows = db.execute(
        select(AuditLog, User.email, User.full_name)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(*conditions)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        AuditLogOut(
            id=log.id,
            occurred_at=log.occurred_at,
            action=log.action,
            user_id=log.user_id,
            user_email=email,
            user_name=name,
            site_id=log.site_id,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            data=log.data,
            ip_address=log.ip_address,
        )
        for log, email, name in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)
