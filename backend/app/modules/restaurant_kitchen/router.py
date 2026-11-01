"""API de la cuisine d'un site (palier R6, ADR-0050) : ``/restaurant/kitchen``.

Le routeur est monté avec ``require_module("restaurant.kitchen")`` (site sélectionné sans le
module effectif : ``403 module_unavailable``) ; sans site sélectionné, le service limite les
lectures aux sites où le module est effectif pour le membre et revérifie chaque écriture pour son
site. Préparer une ligne (commencer, prête, retour) reste dans l'API des commandes
(``restaurant.orders.order.prepare``)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.modules.restaurant_kitchen.permissions import (
    LINE_ROUTE,
    PRIORITY_SET,
    STATION_MANAGE,
    STATION_VIEW,
)
from app.modules.restaurant_kitchen.schemas import (
    BoardOut,
    LinesRoute,
    PrioritySet,
    RouteOut,
    RouteSet,
    StationCreate,
    StationOut,
    StationUpdate,
)
from app.modules.restaurant_kitchen.service import KitchenService
from app.modules.restaurant_orders.api import OrderOut
from app.platform.context import DbSession, RequestContext, require_permission
from app.shared.clock import utcnow
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["restaurant.kitchen"])

KitchenView = Annotated[RequestContext, Depends(require_permission(STATION_VIEW))]
KitchenManage = Annotated[RequestContext, Depends(require_permission(STATION_MANAGE))]
LineRouter = Annotated[RequestContext, Depends(require_permission(LINE_ROUTE))]
PrioritySetter = Annotated[RequestContext, Depends(require_permission(PRIORITY_SET))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]


def _service(db: DbSession, ctx: RequestContext) -> KitchenService:
    return KitchenService(db, ctx, utcnow())


# --- Postes -------------------------------------------------------------------------------------


@router.get("/stations", response_model=Page[StationOut])
def list_stations(
    ctx: KitchenView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[StationOut]:
    rows, total = _service(db, ctx).search_stations(paging, search, status_filter, site_id)
    return Page(
        items=[StationOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/stations/{station_id}", response_model=StationOut)
def get_station(station_id: uuid.UUID, ctx: KitchenView, db: DbSession) -> StationOut:
    return StationOut.model_validate(_service(db, ctx).get_station(station_id))


@router.post("/stations", response_model=StationOut, status_code=status.HTTP_201_CREATED)
def create_station(body: StationCreate, ctx: KitchenManage, db: DbSession) -> StationOut:
    service = _service(db, ctx)
    station = service.create_station(body.site_id, body.name, body.sort_order)
    db.commit()
    return StationOut.model_validate(service.station_row(station.id))


@router.put("/stations/{station_id}", response_model=StationOut)
def update_station(
    station_id: uuid.UUID, body: StationUpdate, ctx: KitchenManage, db: DbSession
) -> StationOut:
    service = _service(db, ctx)
    service.update_station(station_id, body.name, body.sort_order)
    db.commit()
    return StationOut.model_validate(service.station_row(station_id))


@router.post("/stations/{station_id}/activate", response_model=StationOut)
def activate_station(station_id: uuid.UUID, ctx: KitchenManage, db: DbSession) -> StationOut:
    service = _service(db, ctx)
    service.set_station_active(station_id, True)
    db.commit()
    return StationOut.model_validate(service.station_row(station_id))


@router.post("/stations/{station_id}/deactivate", response_model=StationOut)
def deactivate_station(station_id: uuid.UUID, ctx: KitchenManage, db: DbSession) -> StationOut:
    service = _service(db, ctx)
    service.set_station_active(station_id, False)
    db.commit()
    return StationOut.model_validate(service.station_row(station_id))


# --- Affectations du menu -----------------------------------------------------------------------


@router.get("/routes", response_model=list[RouteOut])
def list_routes(
    ctx: KitchenView, db: DbSession, site_id: uuid.UUID | None = None
) -> list[RouteOut]:
    """Affectations des sections et des éléments du menu aux postes (sites lisibles)."""
    return _service(db, ctx).routes(site_id)


@router.put("/routes", response_model=RouteOut | None)
def set_route(body: RouteSet, ctx: KitchenManage, db: DbSession) -> RouteOut | Response:
    """Affecte (ou retire, ``station_id`` nul : ``204``) une section ou un élément du menu."""
    route = _service(db, ctx).set_route(body)
    db.commit()
    return route if route is not None else Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Écran cuisine ------------------------------------------------------------------------------


@router.get("/board", response_model=BoardOut)
def kitchen_board(
    ctx: KitchenView,
    db: DbSession,
    site_id: uuid.UUID | None = None,
    station_id: uuid.UUID | None = None,
    unrouted: bool = False,
) -> BoardOut:
    """Tickets à préparer et prêts du site (exige aussi ``restaurant.orders.order.view``)."""
    return _service(db, ctx).board(site_id, station_id, unrouted)


# --- Commandes : priorité et poste des lignes ---------------------------------------------------


@router.put("/orders/{order_id}/priority", response_model=OrderOut)
def set_order_priority(
    order_id: uuid.UUID, body: PrioritySet, ctx: PrioritySetter, db: DbSession
) -> OrderOut:
    service = _service(db, ctx)
    service.set_priority(order_id, body)
    db.commit()
    return service.order(order_id)


@router.put("/orders/{order_id}/route", response_model=OrderOut)
def route_order_lines(
    order_id: uuid.UUID, body: LinesRoute, ctx: LineRouter, db: DbSession
) -> OrderOut:
    service = _service(db, ctx)
    service.route_lines(order_id, body)
    db.commit()
    return service.order(order_id)
