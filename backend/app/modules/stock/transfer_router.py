"""Routes des transferts inter-sites : ``/api/v1/stock/transfers`` (Phase 2.5).

Toutes exigent la fonctionnalité de plan ``stock.transfers`` (``403 feature_unavailable``
sinon) en plus de la permission propre à l'opération."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.stock.models import DocumentStatus
from app.modules.stock.schemas import CancelInput, TransferCreate, TransferInput, TransferOut
from app.modules.stock.transfer_service import TransferService
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    require_feature,
    require_permission,
)
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(prefix="/transfers", dependencies=[Depends(require_feature("stock.transfers"))])

View = Annotated[RequestContext, Depends(require_permission("stock.transfer.view"))]
Create = Annotated[RequestContext, Depends(require_permission("stock.transfer.create"))]
Update = Annotated[RequestContext, Depends(require_permission("stock.transfer.update"))]
Validate = Annotated[RequestContext, Depends(require_permission("stock.transfer.validate"))]
Cancel = Annotated[RequestContext, Depends(require_permission("stock.transfer.cancel"))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[DocumentStatus | None, Query(alias="status")]


@router.get("", response_model=Page[TransferOut])
def list_transfers(
    ctx: View,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    status_filter: StatusParam = None,
    source_site_id: uuid.UUID | None = None,
    destination_site_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[TransferOut]:
    service = TransferService(db, ctx, now)
    items, total = service.search(
        paging,
        search=search,
        status=status_filter,
        source_site_id=source_site_id,
        destination_site_id=destination_site_id,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post("", response_model=TransferOut, status_code=status.HTTP_201_CREATED)
def create_transfer(body: TransferCreate, ctx: Create, db: DbSession, now: NowDep) -> TransferOut:
    service = TransferService(db, ctx, now)
    transfer = service.create(body)
    db.commit()
    return service.to_out([transfer], with_lines=True)[0]


@router.get("/{transfer_id}", response_model=TransferOut)
def get_transfer(transfer_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> TransferOut:
    service = TransferService(db, ctx, now)
    return service.to_out([service.get(transfer_id)], with_lines=True)[0]


@router.put("/{transfer_id}", response_model=TransferOut)
def update_transfer(
    transfer_id: uuid.UUID, body: TransferInput, ctx: Update, db: DbSession, now: NowDep
) -> TransferOut:
    service = TransferService(db, ctx, now)
    transfer = service.update(transfer_id, body)
    db.commit()
    return service.to_out([transfer], with_lines=True)[0]


@router.post("/{transfer_id}/validate", response_model=TransferOut)
def validate_transfer(
    transfer_id: uuid.UUID, ctx: Validate, db: DbSession, now: NowDep
) -> TransferOut:
    service = TransferService(db, ctx, now)
    transfer = service.validate(transfer_id)
    db.commit()
    return service.to_out([transfer], with_lines=True)[0]


@router.post("/{transfer_id}/cancel", response_model=TransferOut)
def cancel_transfer(
    transfer_id: uuid.UUID, body: CancelInput, ctx: Cancel, db: DbSession, now: NowDep
) -> TransferOut:
    service = TransferService(db, ctx, now)
    transfer = service.cancel(transfer_id, body.reason)
    db.commit()
    return service.to_out([transfer], with_lines=True)[0]
