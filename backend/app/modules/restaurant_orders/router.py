"""API des commandes de restauration (palier R2, ADR-0049) : ``/restaurant/orders`` et
``/restaurant/settings/{site_id}``.

Monté avec ``require_module("restaurant.orders")`` (site sélectionné sans commandes
effectives : ``403 module_unavailable``) ; sans site sélectionné, le service limite les
lectures aux sites où les commandes sont effectives pour le membre et revérifie chaque écriture
pour son site, avec la permission précise de l'action sur CE site. Le canal de la commande est
posé par la route (``STAFF`` ici), jamais par le client."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.modules.restaurant_orders.models import OrderChannel, PrepStatus, SettlementStatus
from app.modules.restaurant_orders.permissions import (
    ORDER_CANCEL,
    ORDER_CANCEL_PREPARED,
    ORDER_CLAIM,
    ORDER_CREATE,
    ORDER_PREPARE,
    ORDER_REASSIGN,
    ORDER_SERVE,
    ORDER_VIEW,
    SETTINGS_MANAGE,
)
from app.modules.restaurant_orders.schemas import (
    AssigneeOut,
    EventOut,
    LinesAdd,
    LinesCancel,
    LineSelection,
    OrderCancel,
    OrderCreate,
    OrderOut,
    OrderReassign,
    OrderSettle,
    SettingsOut,
    SettingsUpdate,
    SettlementOut,
    TicketOut,
)
from app.modules.restaurant_orders.service import OrderService, OrderState
from app.platform.context import (
    DbSession,
    RequestContext,
    require_any_permission,
    require_permission,
)
from app.shared.clock import utcnow
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(tags=["restaurant.orders"])

OrderView = Annotated[RequestContext, Depends(require_permission(ORDER_VIEW))]
OrderCreator = Annotated[RequestContext, Depends(require_permission(ORDER_CREATE))]
OrderPreparer = Annotated[RequestContext, Depends(require_permission(ORDER_PREPARE))]
OrderServer = Annotated[RequestContext, Depends(require_permission(ORDER_SERVE))]
OrderCanceller = Annotated[
    RequestContext, Depends(require_any_permission(ORDER_CANCEL, ORDER_CANCEL_PREPARED))
]
OrderClaimer = Annotated[RequestContext, Depends(require_permission(ORDER_CLAIM))]
OrderReassigner = Annotated[RequestContext, Depends(require_permission(ORDER_REASSIGN))]
SettingsManager = Annotated[RequestContext, Depends(require_permission(SETTINGS_MANAGE))]
Paging = Annotated[PageParams, Depends(page_params)]


def _service(db: DbSession, ctx: RequestContext) -> OrderService:
    return OrderService(db, ctx, utcnow())


# --- Réglages du site ---------------------------------------------------------------------------


@router.get("/settings/{site_id}", response_model=SettingsOut)
def get_settings(site_id: uuid.UUID, ctx: OrderView, db: DbSession) -> SettingsOut:
    settings = _service(db, ctx).get_settings(site_id)
    db.commit()  # réglages créés au premier usage s'ils manquaient (activation inerte)
    return SettingsOut.model_validate(settings)


@router.put("/settings/{site_id}", response_model=SettingsOut)
def update_settings(
    site_id: uuid.UUID, body: SettingsUpdate, ctx: SettingsManager, db: DbSession
) -> SettingsOut:
    settings = _service(db, ctx).update_settings(site_id, body)
    db.commit()
    return SettingsOut.model_validate(settings)


# --- Commandes ----------------------------------------------------------------------------------


@router.get("/orders", response_model=Page[OrderOut])
def list_orders(
    ctx: OrderView,
    db: DbSession,
    paging: Paging,
    site_id: uuid.UUID | None = None,
    state: OrderState = OrderState.ACTIVE,
    prep_status: PrepStatus | None = None,
    settlement_status: SettlementStatus | None = None,
    unsettled_served: bool = False,
    search: Annotated[str | None, Query(max_length=40)] = None,
    business_date: date | None = None,
) -> Page[OrderOut]:
    items, total = _service(db, ctx).search(
        paging,
        site_id=site_id,
        state=state,
        prep=prep_status,
        settlement=settlement_status,
        unsettled_served=unsettled_served,
        search=search,
        day=business_date,
    )
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@router.post("/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    body: OrderCreate, ctx: OrderCreator, db: DbSession, response: Response
) -> OrderOut:
    service = _service(db, ctx)
    order, replayed = service.create(body, OrderChannel.STAFF)
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK
    return service.detail(order.id)


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, ctx: OrderView, db: DbSession) -> OrderOut:
    return _service(db, ctx).detail(order_id)


@router.get("/orders/{order_id}/events", response_model=list[EventOut])
def order_events(order_id: uuid.UUID, ctx: OrderView, db: DbSession) -> list[EventOut]:
    return _service(db, ctx).events(order_id)


@router.get("/orders/{order_id}/ticket", response_model=TicketOut)
def order_ticket(order_id: uuid.UUID, ctx: OrderView, db: DbSession) -> TicketOut:
    return _service(db, ctx).ticket(order_id)


@router.post("/orders/{order_id}/lines", response_model=OrderOut)
def add_lines(order_id: uuid.UUID, body: LinesAdd, ctx: OrderCreator, db: DbSession) -> OrderOut:
    service = _service(db, ctx)
    service.add_lines(order_id, body)
    db.commit()
    return service.detail(order_id)


def _transition(
    db: DbSession, ctx: RequestContext, order_id: uuid.UUID, action: str, body: LineSelection
) -> OrderOut:
    service = _service(db, ctx)
    service.transition(order_id, action, body.line_ids)
    db.commit()
    return service.detail(order_id)


@router.post("/orders/{order_id}/start", response_model=OrderOut)
def start_preparation(
    order_id: uuid.UUID, ctx: OrderPreparer, db: DbSession, body: LineSelection | None = None
) -> OrderOut:
    return _transition(db, ctx, order_id, "start", body or LineSelection())


@router.post("/orders/{order_id}/ready", response_model=OrderOut)
def mark_ready(
    order_id: uuid.UUID, ctx: OrderPreparer, db: DbSession, body: LineSelection | None = None
) -> OrderOut:
    return _transition(db, ctx, order_id, "ready", body or LineSelection())


@router.post("/orders/{order_id}/revert", response_model=OrderOut)
def revert_ready(
    order_id: uuid.UUID, ctx: OrderPreparer, db: DbSession, body: LineSelection | None = None
) -> OrderOut:
    return _transition(db, ctx, order_id, "revert", body or LineSelection())


@router.post("/orders/{order_id}/serve", response_model=OrderOut)
def serve(
    order_id: uuid.UUID, ctx: OrderServer, db: DbSession, body: LineSelection | None = None
) -> OrderOut:
    return _transition(db, ctx, order_id, "serve", body or LineSelection())


@router.post("/orders/{order_id}/cancel-lines", response_model=OrderOut)
def cancel_lines(
    order_id: uuid.UUID, body: LinesCancel, ctx: OrderCanceller, db: DbSession
) -> OrderOut:
    service = _service(db, ctx)
    service.cancel_lines(order_id, body.line_ids, body.reason)
    db.commit()
    return service.detail(order_id)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: uuid.UUID, body: OrderCancel, ctx: OrderCanceller, db: DbSession
) -> OrderOut:
    service = _service(db, ctx)
    service.cancel(order_id, body.reason)
    db.commit()
    return service.detail(order_id)


# --- Prise en charge (R2-C, D7) -----------------------------------------------------------------


@router.post("/orders/{order_id}/claim", response_model=OrderOut)
def claim_order(order_id: uuid.UUID, ctx: OrderClaimer, db: DbSession) -> OrderOut:
    service = _service(db, ctx)
    service.claim(order_id)
    db.commit()
    return service.detail(order_id)


@router.get("/orders/{order_id}/assignees", response_model=list[AssigneeOut])
def order_assignees(order_id: uuid.UUID, ctx: OrderReassigner, db: DbSession) -> list[AssigneeOut]:
    """Membres éligibles à la réattribution (``order.claim`` effective sur le site)."""
    return [
        AssigneeOut(user_id=user_id, full_name=name)
        for user_id, name in _service(db, ctx).assignees(order_id)
    ]


@router.post("/orders/{order_id}/reassign", response_model=OrderOut)
def reassign_order(
    order_id: uuid.UUID, body: OrderReassign, ctx: OrderReassigner, db: DbSession
) -> OrderOut:
    service = _service(db, ctx)
    service.reassign(order_id, body.assignee_user_id, body.reason)
    db.commit()
    return service.detail(order_id)


# --- Règlement T2 (R2-D, D6) --------------------------------------------------------------------


@router.post(
    "/orders/{order_id}/settle", response_model=SettlementOut, status_code=status.HTTP_201_CREATED
)
def settle_order(
    order_id: uuid.UUID, body: OrderSettle, ctx: OrderView, db: DbSession, response: Response
) -> SettlementOut:
    """Règlement : permissions EXISTANTES des ventes sur le site de la commande
    (``sales.sale.create`` + ``sales.sale.validate``, ``sales.payment.create`` avec des
    paiements, ``sales.sale.credit_create`` pour un reste dû) ; tout ou rien. La même clé
    renvoie la vente déjà enregistrée (``200``). Reçu : ``/sales/{id}/receipt``."""
    service = _service(db, ctx)
    _, sale_id, replayed = service.settle(order_id, body)
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK
    return SettlementOut(order=service.detail(order_id), sale_id=sale_id, replayed=replayed)
