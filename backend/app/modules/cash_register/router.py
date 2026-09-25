import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.modules.cash_register.models import CashMovementType, CashSessionStatus
from app.modules.cash_register.schemas import (
    CashMovementCreate,
    CashMovementOut,
    CashRegisterCreate,
    CashRegisterOut,
    CashRegisterUpdate,
    CashSessionClose,
    CashSessionOpen,
    CashSessionOut,
)
from app.modules.cash_register.service import CashService
from app.platform.context import DbSession, NowDep, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["cash_register"])

P = "cash_register"
RegisterView = Annotated[RequestContext, Depends(require_permission(f"{P}.register.view"))]
RegisterManage = Annotated[RequestContext, Depends(require_permission(f"{P}.register.manage"))]
SessionView = Annotated[RequestContext, Depends(require_permission(f"{P}.session.view"))]
SessionOpen = Annotated[RequestContext, Depends(require_permission(f"{P}.session.open"))]
SessionClose = Annotated[RequestContext, Depends(require_permission(f"{P}.session.close"))]
MovementCreate = Annotated[RequestContext, Depends(require_permission(f"{P}.movement.create"))]
Paging = Annotated[PageParams, Depends(page_params)]
Amount = Annotated[Decimal | None, Query(ge=0, max_digits=18, decimal_places=2)]

# --- Caisses ---------------------------------------------------------------------------------


@router.get("/registers", response_model=Page[CashRegisterOut])
def list_registers(
    ctx: RegisterView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: Annotated[str | None, Query(max_length=100)] = None,
    site_id: uuid.UUID | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = StatusFilter.ALL,
) -> Page[CashRegisterOut]:
    service = CashService(db, ctx, now)
    items, total = service.search_registers(
        paging, search=search, site_id=site_id, status=status_filter
    )
    return Page(
        items=service.registers_out(items), total=total, limit=paging.limit, offset=paging.offset
    )


@router.post("/registers", response_model=CashRegisterOut, status_code=status.HTTP_201_CREATED)
def create_register(
    body: CashRegisterCreate, ctx: RegisterManage, db: DbSession, now: NowDep
) -> CashRegisterOut:
    service = CashService(db, ctx, now)
    register = service.create_register(body)
    db.commit()
    return service.registers_out([register])[0]


@router.get("/registers/{register_id}", response_model=CashRegisterOut)
def get_register(
    register_id: uuid.UUID, ctx: RegisterView, db: DbSession, now: NowDep
) -> CashRegisterOut:
    service = CashService(db, ctx, now)
    return service.registers_out([service.get_register(register_id)])[0]


@router.patch("/registers/{register_id}", response_model=CashRegisterOut)
def update_register(
    register_id: uuid.UUID,
    body: CashRegisterUpdate,
    ctx: RegisterManage,
    db: DbSession,
    now: NowDep,
) -> CashRegisterOut:
    service = CashService(db, ctx, now)
    register = service.update_register(register_id, body)
    db.commit()
    return service.registers_out([register])[0]


@router.post("/registers/{register_id}/activate", response_model=CashRegisterOut)
def activate_register(
    register_id: uuid.UUID, ctx: RegisterManage, db: DbSession, now: NowDep
) -> CashRegisterOut:
    service = CashService(db, ctx, now)
    register = service.set_register_active(register_id, True)
    db.commit()
    return service.registers_out([register])[0]


@router.post("/registers/{register_id}/deactivate", response_model=CashRegisterOut)
def deactivate_register(
    register_id: uuid.UUID, ctx: RegisterManage, db: DbSession, now: NowDep
) -> CashRegisterOut:
    service = CashService(db, ctx, now)
    register = service.set_register_active(register_id, False)
    db.commit()
    return service.registers_out([register])[0]


# --- Sessions --------------------------------------------------------------------------------


@router.get("/sessions", response_model=Page[CashSessionOut])
def list_sessions(
    ctx: SessionView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    search: Annotated[str | None, Query(max_length=100)] = None,
    cash_register_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    status_filter: Annotated[CashSessionStatus | None, Query(alias="status")] = None,
    opened_by: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[CashSessionOut]:
    service = CashService(db, ctx, now)
    items, total = service.search_sessions(
        paging,
        search=search,
        register_id=cash_register_id,
        site_id=site_id,
        status=status_filter,
        opened_by=opened_by,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(
        items=service.sessions_out(items), total=total, limit=paging.limit, offset=paging.offset
    )


@router.post("/sessions", response_model=CashSessionOut, status_code=status.HTTP_201_CREATED)
def open_session(
    body: CashSessionOpen, ctx: SessionOpen, db: DbSession, now: NowDep
) -> CashSessionOut:
    service = CashService(db, ctx, now)
    session = service.open_session(body.cash_register_id, body.opening_float)
    db.commit()
    return service.sessions_out([session])[0]


@router.get("/sessions/{session_id}", response_model=CashSessionOut)
def get_session(
    session_id: uuid.UUID, ctx: SessionView, db: DbSession, now: NowDep
) -> CashSessionOut:
    service = CashService(db, ctx, now)
    return service.sessions_out([service.get_session(session_id)])[0]


@router.post("/sessions/{session_id}/close", response_model=CashSessionOut)
def close_session(
    session_id: uuid.UUID, body: CashSessionClose, ctx: SessionClose, db: DbSession, now: NowDep
) -> CashSessionOut:
    service = CashService(db, ctx, now)
    session = service.close_session(session_id, body.counted_balance, body.note)
    db.commit()
    return service.sessions_out([session])[0]


# --- Mouvements (journal) --------------------------------------------------------------------


def _journal(
    ctx: RequestContext,
    db: DbSession,
    now: NowDep,
    paging: PageParams,
    **filters: object,
) -> Page[CashMovementOut]:
    service = CashService(db, ctx, now)
    rows, total = service.search_movements(paging, **filters)  # type: ignore[arg-type]
    return Page(
        items=service.movements_out(rows), total=total, limit=paging.limit, offset=paging.offset
    )


@router.get("/movements", response_model=Page[CashMovementOut])
def list_movements(
    ctx: SessionView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    cash_session_id: uuid.UUID | None = None,
    cash_register_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    movement_type: CashMovementType | None = None,
    created_by: uuid.UUID | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    min_amount: Amount = None,
    max_amount: Amount = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[CashMovementOut]:
    return _journal(
        ctx,
        db,
        now,
        paging,
        session_id=cash_session_id,
        register_id=cash_register_id,
        site_id=site_id,
        movement_type=movement_type,
        created_by=created_by,
        search=search,
        min_amount=min_amount,
        max_amount=max_amount,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/sessions/{session_id}/movements", response_model=Page[CashMovementOut])
def list_session_movements(
    session_id: uuid.UUID,
    ctx: SessionView,
    db: DbSession,
    now: NowDep,
    paging: Paging,
    movement_type: CashMovementType | None = None,
    created_by: uuid.UUID | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    min_amount: Amount = None,
    max_amount: Amount = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[CashMovementOut]:
    CashService(db, ctx, now).get_session(session_id)  # site et tenant : 404 / 403
    return _journal(
        ctx,
        db,
        now,
        paging,
        session_id=session_id,
        movement_type=movement_type,
        created_by=created_by,
        search=search,
        min_amount=min_amount,
        max_amount=max_amount,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("/sessions/{session_id}/movements", response_model=CashMovementOut)
def create_movement(
    session_id: uuid.UUID,
    body: CashMovementCreate,
    ctx: MovementCreate,
    db: DbSession,
    now: NowDep,
    response: Response,
) -> CashMovementOut:
    service = CashService(db, ctx, now)
    movement, replayed = service.manual_movement(session_id, body)
    db.commit()
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return service.movement_out(movement)
