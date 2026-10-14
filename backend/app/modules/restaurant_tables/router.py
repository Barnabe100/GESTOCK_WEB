"""API des zones et des tables d'un site (palier R5-A, ADR-0049) : ``/restaurant/tables``.

Le routeur est monté avec ``require_module("restaurant.tables")`` (site sélectionné sans le
module effectif : ``403 module_unavailable``) ; sans site sélectionné, le service limite les
lectures aux sites où le module est effectif pour le membre et revérifie chaque écriture pour
son site. Les routes des zones et du plan de salle sont déclarées AVANT ``/{table_id}``."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.restaurant_tables.permissions import TABLE_MANAGE, TABLE_VIEW
from app.modules.restaurant_tables.schemas import (
    BoardOut,
    TableCreate,
    TableOut,
    TableUpdate,
    ZoneCreate,
    ZoneOut,
    ZoneUpdate,
)
from app.modules.restaurant_tables.service import TableService
from app.platform.context import DbSession, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["restaurant.tables"])

TablesView = Annotated[RequestContext, Depends(require_permission(TABLE_VIEW))]
TablesManage = Annotated[RequestContext, Depends(require_permission(TABLE_MANAGE))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]


# --- Zones --------------------------------------------------------------------------------------


@router.get("/zones", response_model=Page[ZoneOut])
def list_zones(
    ctx: TablesView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[ZoneOut]:
    rows, total = TableService(db, ctx).search_zones(paging, search, status_filter, site_id)
    return Page(
        items=[ZoneOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/zones/{zone_id}", response_model=ZoneOut)
def get_zone(zone_id: uuid.UUID, ctx: TablesView, db: DbSession) -> ZoneOut:
    return ZoneOut.model_validate(TableService(db, ctx).get_zone(zone_id))


@router.post("/zones", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
def create_zone(body: ZoneCreate, ctx: TablesManage, db: DbSession) -> ZoneOut:
    service = TableService(db, ctx)
    zone = service.create_zone(body.site_id, body.name, body.sort_order)
    db.commit()
    return ZoneOut.model_validate(service.zone_row(zone.id))


@router.put("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: uuid.UUID, body: ZoneUpdate, ctx: TablesManage, db: DbSession) -> ZoneOut:
    service = TableService(db, ctx)
    service.update_zone(zone_id, body.name, body.sort_order)
    db.commit()
    return ZoneOut.model_validate(service.zone_row(zone_id))


@router.post("/zones/{zone_id}/activate", response_model=ZoneOut)
def activate_zone(zone_id: uuid.UUID, ctx: TablesManage, db: DbSession) -> ZoneOut:
    service = TableService(db, ctx)
    service.set_zone_active(zone_id, True)
    db.commit()
    return ZoneOut.model_validate(service.zone_row(zone_id))


@router.post("/zones/{zone_id}/deactivate", response_model=ZoneOut)
def deactivate_zone(zone_id: uuid.UUID, ctx: TablesManage, db: DbSession) -> ZoneOut:
    service = TableService(db, ctx)
    service.set_zone_active(zone_id, False)
    db.commit()
    return ZoneOut.model_validate(service.zone_row(zone_id))


# --- Plan de salle (R5-B, AR-3) -----------------------------------------------------------------


@router.get("/board", response_model=BoardOut)
def table_board(ctx: TablesView, db: DbSession, site_id: uuid.UUID | None = None) -> BoardOut:
    """États Libre / Occupée / À régler des tables d'un site, calculés à la lecture."""
    return TableService(db, ctx).board(site_id)


# --- Tables -------------------------------------------------------------------------------------


@router.get("", response_model=Page[TableOut])
def list_tables(
    ctx: TablesView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    zone_id: uuid.UUID | None = None,
    without_zone: bool = False,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[TableOut]:
    rows, total = TableService(db, ctx).search_tables(
        paging,
        search=search,
        status=status_filter,
        site_id=site_id,
        zone_id=zone_id,
        without_zone=without_zone,
    )
    return Page(
        items=[TableOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.post("", response_model=TableOut, status_code=status.HTTP_201_CREATED)
def create_table(body: TableCreate, ctx: TablesManage, db: DbSession) -> TableOut:
    service = TableService(db, ctx)
    table = service.create_table(body)
    db.commit()
    return TableOut.model_validate(service.table_row(table.id))


@router.get("/{table_id}", response_model=TableOut)
def get_table(table_id: uuid.UUID, ctx: TablesView, db: DbSession) -> TableOut:
    return TableOut.model_validate(TableService(db, ctx).get_table(table_id))


@router.put("/{table_id}", response_model=TableOut)
def update_table(
    table_id: uuid.UUID, body: TableUpdate, ctx: TablesManage, db: DbSession
) -> TableOut:
    service = TableService(db, ctx)
    service.update_table(table_id, body)
    db.commit()
    return TableOut.model_validate(service.table_row(table_id))


@router.post("/{table_id}/activate", response_model=TableOut)
def activate_table(table_id: uuid.UUID, ctx: TablesManage, db: DbSession) -> TableOut:
    service = TableService(db, ctx)
    service.set_table_active(table_id, True)
    db.commit()
    return TableOut.model_validate(service.table_row(table_id))


@router.post("/{table_id}/deactivate", response_model=TableOut)
def deactivate_table(table_id: uuid.UUID, ctx: TablesManage, db: DbSession) -> TableOut:
    service = TableService(db, ctx)
    service.set_table_active(table_id, False)
    db.commit()
    return TableOut.model_validate(service.table_row(table_id))
