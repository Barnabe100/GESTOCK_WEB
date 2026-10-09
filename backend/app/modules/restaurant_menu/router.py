"""API du menu d'un site (palier R1, ADR-0049) : ``/restaurant/menu``.

Le routeur est monté avec ``require_module("restaurant.menu")`` (site sélectionné sans menu
effectif : ``403 module_unavailable``) ; sans site sélectionné, le service limite les lectures
aux sites où le menu est effectif pour le membre et revérifie chaque écriture pour son site."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.restaurant_menu.schemas import (
    AvailabilityInput,
    ItemCreate,
    ItemOut,
    ItemUpdate,
    SectionCreate,
    SectionOut,
    SectionUpdate,
)
from app.modules.restaurant_menu.service import AvailabilityFilter, MenuService
from app.platform.context import DbSession, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["restaurant.menu"])

MenuView = Annotated[RequestContext, Depends(require_permission("restaurant.menu.view"))]
MenuManage = Annotated[RequestContext, Depends(require_permission("restaurant.menu.manage"))]
MenuAvailability = Annotated[
    RequestContext, Depends(require_permission("restaurant.menu.availability"))
]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]


# --- Sections -----------------------------------------------------------------------------------


@router.get("/sections", response_model=Page[SectionOut])
def list_sections(
    ctx: MenuView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[SectionOut]:
    rows, total = MenuService(db, ctx).search_sections(paging, search, status_filter, site_id)
    return Page(
        items=[SectionOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/sections/{section_id}", response_model=SectionOut)
def get_section(section_id: uuid.UUID, ctx: MenuView, db: DbSession) -> SectionOut:
    return SectionOut.model_validate(MenuService(db, ctx).get_section(section_id))


@router.post("/sections", response_model=SectionOut, status_code=status.HTTP_201_CREATED)
def create_section(body: SectionCreate, ctx: MenuManage, db: DbSession) -> SectionOut:
    service = MenuService(db, ctx)
    section = service.create_section(body.site_id, body.name, body.sort_order)
    db.commit()
    return SectionOut.model_validate(service.section_row(section.id))


@router.put("/sections/{section_id}", response_model=SectionOut)
def update_section(
    section_id: uuid.UUID, body: SectionUpdate, ctx: MenuManage, db: DbSession
) -> SectionOut:
    service = MenuService(db, ctx)
    service.update_section(section_id, body.name, body.sort_order)
    db.commit()
    return SectionOut.model_validate(service.section_row(section_id))


@router.post("/sections/{section_id}/activate", response_model=SectionOut)
def activate_section(section_id: uuid.UUID, ctx: MenuManage, db: DbSession) -> SectionOut:
    service = MenuService(db, ctx)
    service.set_section_active(section_id, True)
    db.commit()
    return SectionOut.model_validate(service.section_row(section_id))


@router.post("/sections/{section_id}/deactivate", response_model=SectionOut)
def deactivate_section(section_id: uuid.UUID, ctx: MenuManage, db: DbSession) -> SectionOut:
    service = MenuService(db, ctx)
    service.set_section_active(section_id, False)
    db.commit()
    return SectionOut.model_validate(service.section_row(section_id))


# --- Éléments -----------------------------------------------------------------------------------


@router.get("/items", response_model=Page[ItemOut])
def list_items(
    ctx: MenuView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
    availability: AvailabilityFilter = AvailabilityFilter.ALL,
) -> Page[ItemOut]:
    rows, total = MenuService(db, ctx).search_items(
        paging,
        search=search,
        status=status_filter,
        availability=availability,
        site_id=site_id,
        section_id=section_id,
    )
    return Page(
        items=[ItemOut.model_validate(r) for r in rows],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: uuid.UUID, ctx: MenuView, db: DbSession) -> ItemOut:
    return ItemOut.model_validate(MenuService(db, ctx).get_item(item_id))


@router.post("/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def create_item(body: ItemCreate, ctx: MenuManage, db: DbSession) -> ItemOut:
    service = MenuService(db, ctx)
    item = service.create_item(body)
    db.commit()
    return ItemOut.model_validate(service.item_row(item.id))


@router.put("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: uuid.UUID, body: ItemUpdate, ctx: MenuManage, db: DbSession) -> ItemOut:
    service = MenuService(db, ctx)
    service.update_item(item_id, body)
    db.commit()
    return ItemOut.model_validate(service.item_row(item_id))


@router.post("/items/{item_id}/activate", response_model=ItemOut)
def activate_item(item_id: uuid.UUID, ctx: MenuManage, db: DbSession) -> ItemOut:
    service = MenuService(db, ctx)
    service.set_item_active(item_id, True)
    db.commit()
    return ItemOut.model_validate(service.item_row(item_id))


@router.post("/items/{item_id}/deactivate", response_model=ItemOut)
def deactivate_item(item_id: uuid.UUID, ctx: MenuManage, db: DbSession) -> ItemOut:
    service = MenuService(db, ctx)
    service.set_item_active(item_id, False)
    db.commit()
    return ItemOut.model_validate(service.item_row(item_id))


@router.put("/items/{item_id}/availability", response_model=ItemOut)
def set_item_availability(
    item_id: uuid.UUID, body: AvailabilityInput, ctx: MenuAvailability, db: DbSession
) -> ItemOut:
    service = MenuService(db, ctx)
    service.set_availability(item_id, body.available, body.reason)
    db.commit()
    return ItemOut.model_validate(service.item_row(item_id))
