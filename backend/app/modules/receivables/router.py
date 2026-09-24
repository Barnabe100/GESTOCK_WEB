import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.receivables.schemas import (
    CreditExposureOut,
    ReceivableDetail,
    ReceivableOut,
    ReceivableSummary,
)
from app.modules.receivables.service import ReceivableFilters, ReceivableService, ReceivableStatus
from app.platform.context import DbSession, NowDep, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

# Consultation seule : aucune route d'écriture (les encaissements restent dans ``sales``).
router = APIRouter(tags=["receivables"])
customer_router = APIRouter(tags=["receivables"])

View = Annotated[RequestContext, Depends(require_permission("receivables.receivable.view"))]
Paging = Annotated[PageParams, Depends(page_params)]
Amount = Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)]


def receivable_filters(
    search: Annotated[str | None, Query(max_length=100)] = None,
    customer_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_amount: Amount = None,
    max_amount: Amount = None,
    status: ReceivableStatus | None = None,
) -> ReceivableFilters:
    return ReceivableFilters(
        search=search,
        customer_id=customer_id,
        site_id=site_id,
        date_from=date_from,
        date_to=date_to,
        min_amount=min_amount,
        max_amount=max_amount,
        status=status,
    )


Filters = Annotated[ReceivableFilters, Depends(receivable_filters)]


@router.get("", response_model=Page[ReceivableOut])
def list_receivables(
    ctx: View, db: DbSession, now: NowDep, paging: Paging, filters: Filters
) -> Page[ReceivableOut]:
    items, total = ReceivableService(db, ctx, now).search(paging, filters)
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@router.get("/summary", response_model=ReceivableSummary)
def receivables_summary(
    ctx: View, db: DbSession, now: NowDep, filters: Filters
) -> ReceivableSummary:
    return ReceivableService(db, ctx, now).summary(filters)


@router.get("/{sale_id}", response_model=ReceivableDetail)
def get_receivable(sale_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> ReceivableDetail:
    return ReceivableService(db, ctx, now).detail(sale_id)


@customer_router.get("/{customer_id}/receivables", response_model=Page[ReceivableOut])
def list_customer_receivables(
    customer_id: uuid.UUID,
    ctx: View,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    filters: Filters,
) -> Page[ReceivableOut]:
    items, total = ReceivableService(db, ctx, now).customer_receivables(
        customer_id, paging, filters
    )
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@customer_router.get("/{customer_id}/credit-exposure", response_model=CreditExposureOut)
def customer_credit_exposure(
    customer_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep
) -> CreditExposureOut:
    return ReceivableService(db, ctx, now).credit_exposure(customer_id)
