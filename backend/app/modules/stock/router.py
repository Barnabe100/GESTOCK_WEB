import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.stock.document_service import EntryService, ExitService
from app.modules.stock.models import DocumentStatus, EntryKind, StockEntry, StockExit
from app.modules.stock.reason_service import ExitReasonService
from app.modules.stock.schemas import (
    CancelInput,
    EntryCreate,
    EntryInput,
    EntryOut,
    ExitCreate,
    ExitInput,
    ExitOut,
    ExitReasonInput,
    ExitReasonOut,
)
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    require_any_permission,
    require_permission,
)
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["stock"])


ReasonPick = Annotated[
    RequestContext,
    Depends(require_any_permission("stock.reason.view", "stock.exit.create", "stock.exit.update")),
]
ReasonManage = Annotated[RequestContext, Depends(require_permission("stock.reason.manage"))]
EntryView = Annotated[RequestContext, Depends(require_permission("stock.entry.view"))]
EntryCreateCtx = Annotated[RequestContext, Depends(require_permission("stock.entry.create"))]
EntryUpdate = Annotated[RequestContext, Depends(require_permission("stock.entry.update"))]
EntryValidate = Annotated[RequestContext, Depends(require_permission("stock.entry.validate"))]
EntryCancel = Annotated[RequestContext, Depends(require_permission("stock.entry.cancel"))]
ExitView = Annotated[RequestContext, Depends(require_permission("stock.exit.view"))]
ExitCreateCtx = Annotated[RequestContext, Depends(require_permission("stock.exit.create"))]
ExitUpdate = Annotated[RequestContext, Depends(require_permission("stock.exit.update"))]
ExitValidate = Annotated[RequestContext, Depends(require_permission("stock.exit.validate"))]
ExitCancel = Annotated[RequestContext, Depends(require_permission("stock.exit.cancel"))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]
DocStatusParam = Annotated[DocumentStatus | None, Query(alias="status")]


# --- Motifs de sortie ------------------------------------------------------------------------


@router.get("/exit-reasons", response_model=Page[ExitReasonOut])
def list_exit_reasons(
    ctx: ReasonPick,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[ExitReasonOut]:
    items, total = ExitReasonService(db, ctx).search(paging, search, status_filter)
    return Page(
        items=[ExitReasonOut.model_validate(r) for r in items],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.post("/exit-reasons", response_model=ExitReasonOut, status_code=status.HTTP_201_CREATED)
def create_exit_reason(body: ExitReasonInput, ctx: ReasonManage, db: DbSession) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).create(body)
    db.commit()
    return ExitReasonOut.model_validate(reason)


@router.patch("/exit-reasons/{reason_id}", response_model=ExitReasonOut)
def update_exit_reason(
    reason_id: uuid.UUID, body: ExitReasonInput, ctx: ReasonManage, db: DbSession
) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).update(reason_id, body)
    db.commit()
    return ExitReasonOut.model_validate(reason)


@router.post("/exit-reasons/{reason_id}/activate", response_model=ExitReasonOut)
def activate_exit_reason(reason_id: uuid.UUID, ctx: ReasonManage, db: DbSession) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).set_active(reason_id, True)
    db.commit()
    return ExitReasonOut.model_validate(reason)


@router.post("/exit-reasons/{reason_id}/deactivate", response_model=ExitReasonOut)
def deactivate_exit_reason(reason_id: uuid.UUID, ctx: ReasonManage, db: DbSession) -> ExitReasonOut:
    reason = ExitReasonService(db, ctx).set_active(reason_id, False)
    db.commit()
    return ExitReasonOut.model_validate(reason)


# --- Entrées ----------------------------------------------------------------------------------


@router.get("/entries", response_model=Page[EntryOut])
def list_entries(
    ctx: EntryView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    status_filter: DocStatusParam = None,
    kind: EntryKind | None = None,
    supplier_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[EntryOut]:
    service = EntryService(db, ctx, now)
    extra = [
        StockEntry.kind == kind if kind else None,
        StockEntry.supplier_id == supplier_id if supplier_id else None,
    ]
    items, total = service.search(
        paging,
        search,
        status_filter,
        site_id,
        date_from,
        date_to,
        extra=[c for c in extra if c is not None],
        search_columns=[StockEntry.document_reference],
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post("/entries", response_model=EntryOut, status_code=status.HTTP_201_CREATED)
def create_entry(body: EntryCreate, ctx: EntryCreateCtx, db: DbSession, now: NowDep) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.create(body)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


@router.get("/entries/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: uuid.UUID, ctx: EntryView, db: DbSession, now: NowDep) -> EntryOut:
    service = EntryService(db, ctx, now)
    return service.to_out([service.get(entry_id)], with_lines=True)[0]


@router.put("/entries/{entry_id}", response_model=EntryOut)
def update_entry(
    entry_id: uuid.UUID, body: EntryInput, ctx: EntryUpdate, db: DbSession, now: NowDep
) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.update(entry_id, body)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


@router.post("/entries/{entry_id}/validate", response_model=EntryOut)
def validate_entry(entry_id: uuid.UUID, ctx: EntryValidate, db: DbSession, now: NowDep) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.validate(entry_id)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


@router.post("/entries/{entry_id}/cancel", response_model=EntryOut)
def cancel_entry(
    entry_id: uuid.UUID, body: CancelInput, ctx: EntryCancel, db: DbSession, now: NowDep
) -> EntryOut:
    service = EntryService(db, ctx, now)
    entry = service.cancel(entry_id, body.reason)
    db.commit()
    return service.to_out([entry], with_lines=True)[0]


# --- Sorties ----------------------------------------------------------------------------------


@router.get("/exits", response_model=Page[ExitOut])
def list_exits(
    ctx: ExitView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: str | None = None,
    status_filter: DocStatusParam = None,
    reason_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[ExitOut]:
    service = ExitService(db, ctx, now)
    items, total = service.search(
        paging,
        search,
        status_filter,
        site_id,
        date_from,
        date_to,
        extra=[StockExit.reason_id == reason_id] if reason_id else [],
        search_columns=[StockExit.reference, StockExit.beneficiary],
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post("/exits", response_model=ExitOut, status_code=status.HTTP_201_CREATED)
def create_exit(body: ExitCreate, ctx: ExitCreateCtx, db: DbSession, now: NowDep) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.create(body)
    db.commit()
    return service.to_out([document], with_lines=True)[0]


@router.get("/exits/{exit_id}", response_model=ExitOut)
def get_exit(exit_id: uuid.UUID, ctx: ExitView, db: DbSession, now: NowDep) -> ExitOut:
    service = ExitService(db, ctx, now)
    return service.to_out([service.get(exit_id)], with_lines=True)[0]


@router.put("/exits/{exit_id}", response_model=ExitOut)
def update_exit(
    exit_id: uuid.UUID, body: ExitInput, ctx: ExitUpdate, db: DbSession, now: NowDep
) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.update(exit_id, body)
    db.commit()
    return service.to_out([document], with_lines=True)[0]


@router.post("/exits/{exit_id}/validate", response_model=ExitOut)
def validate_exit(exit_id: uuid.UUID, ctx: ExitValidate, db: DbSession, now: NowDep) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.validate(exit_id)
    db.commit()
    return service.to_out([document], with_lines=True)[0]


@router.post("/exits/{exit_id}/cancel", response_model=ExitOut)
def cancel_exit(
    exit_id: uuid.UUID, body: CancelInput, ctx: ExitCancel, db: DbSession, now: NowDep
) -> ExitOut:
    service = ExitService(db, ctx, now)
    document = service.cancel(exit_id, body.reason)
    db.commit()
    return service.to_out([document], with_lines=True)[0]
