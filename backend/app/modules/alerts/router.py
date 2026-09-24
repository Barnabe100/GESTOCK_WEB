import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.modules.stock.api import (
    LevelOut,
    StateFilter,
    count_alerts,
    filter_site_ids,
    list_levels,
)
from app.platform.context import DbSession, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(tags=["alerts"])

AlertView = Annotated[RequestContext, Depends(require_permission("alerts.stock.view"))]
Paging = Annotated[PageParams, Depends(page_params)]


class AlertSummary(BaseModel):
    out: int
    low: int


@router.get("/stock", response_model=Page[LevelOut])
def list_stock_alerts(
    ctx: AlertView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    category_id: uuid.UUID | None = None,
    state: StateFilter = StateFilter.ALERTS,
) -> Page[LevelOut]:
    """Articles actifs en rupture ou en stock faible sur les sites visibles."""
    if state not in (StateFilter.ALERTS, StateFilter.OUT, StateFilter.LOW):
        state = StateFilter.ALERTS
    rows, total = list_levels(
        db,
        ctx.tenant_id,
        filter_site_ids(ctx, site_id),
        paging,
        search=search,
        category_id=category_id,
        state=state,
    )
    return Page(
        items=[LevelOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/stock/summary", response_model=AlertSummary)
def stock_alert_summary(
    ctx: AlertView, db: DbSession, site_id: uuid.UUID | None = None
) -> AlertSummary:
    return AlertSummary(**count_alerts(db, ctx.tenant_id, filter_site_ids(ctx, site_id)))
