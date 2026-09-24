import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.sales.models import SaleStatus
from app.modules.sales.schemas import SaleCancel, SaleCreate, SaleInput, SaleOut
from app.modules.sales.service import SaleService
from app.platform.context import DbSession, NowDep, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(tags=["sales"])

View = Annotated[RequestContext, Depends(require_permission("sales.sale.view"))]
Create = Annotated[RequestContext, Depends(require_permission("sales.sale.create"))]
Update = Annotated[RequestContext, Depends(require_permission("sales.sale.update"))]
Validate = Annotated[RequestContext, Depends(require_permission("sales.sale.validate"))]
Cancel = Annotated[RequestContext, Depends(require_permission("sales.sale.cancel"))]
Paging = Annotated[PageParams, Depends(page_params)]


@router.get("", response_model=Page[SaleOut])
def list_sales(
    ctx: View,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    status_filter: Annotated[SaleStatus | None, Query(alias="status")] = None,
    site_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[SaleOut]:
    service = SaleService(db, ctx, now)
    items, total = service.search(
        paging,
        search=search,
        status=status_filter,
        site_id=site_id,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post("", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def create_sale(body: SaleCreate, ctx: Create, db: DbSession, now: NowDep) -> SaleOut:
    service = SaleService(db, ctx, now)
    sale = service.create(body)
    db.commit()
    return service.to_out([sale], with_lines=True)[0]


@router.get("/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> SaleOut:
    service = SaleService(db, ctx, now)
    return service.to_out([service.get(sale_id)], with_lines=True)[0]


@router.put("/{sale_id}", response_model=SaleOut)
def update_sale(
    sale_id: uuid.UUID, body: SaleInput, ctx: Update, db: DbSession, now: NowDep
) -> SaleOut:
    service = SaleService(db, ctx, now)
    sale = service.update(sale_id, body)
    db.commit()
    return service.to_out([sale], with_lines=True)[0]


@router.post("/{sale_id}/validate", response_model=SaleOut)
def validate_sale(sale_id: uuid.UUID, ctx: Validate, db: DbSession, now: NowDep) -> SaleOut:
    service = SaleService(db, ctx, now)
    sale = service.validate(sale_id)
    db.commit()
    return service.to_out([sale], with_lines=True)[0]


@router.post("/{sale_id}/cancel", response_model=SaleOut)
def cancel_sale(
    sale_id: uuid.UUID, body: SaleCancel, ctx: Cancel, db: DbSession, now: NowDep
) -> SaleOut:
    service = SaleService(db, ctx, now)
    sale = service.cancel(sale_id, body.reason)
    db.commit()
    return service.to_out([sale], with_lines=True)[0]
