"""API des recettes (palier R8, ADR-0053) : ``/restaurant/recipes``.

Monté avec ``require_module("restaurant.recipes")``. Coûts et marges retirés des réponses sans
``catalog.article.cost_view`` (``RECIPE_COST_FIELDS``, à toute profondeur)."""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.restaurant_recipes.permissions import RECIPE_MANAGE, RECIPE_VIEW
from app.modules.restaurant_recipes.schemas import (
    ProfitabilityPage,
    RecipeCreate,
    RecipeOut,
    RecipeSummaryOut,
    RecipeUpdate,
    RecipeVersionIn,
    RecipeVersionOut,
    SheetOut,
    SiteAvailabilityOut,
    SiteCostOut,
)
from app.modules.restaurant_recipes.service import RecipeService
from app.platform.context import DbSession, RequestContext, require_permission
from app.platform.costs import RECIPE_COST_FIELDS, cost_masking_route
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, PositiveQuantity, StatusFilter

router = APIRouter(tags=["restaurant.recipes"], route_class=cost_masking_route(RECIPE_COST_FIELDS))

RecipeView = Annotated[RequestContext, Depends(require_permission(RECIPE_VIEW))]
RecipeManage = Annotated[RequestContext, Depends(require_permission(RECIPE_MANAGE))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]


def _service(db: DbSession, ctx: RequestContext) -> RecipeService:
    return RecipeService(db, ctx, datetime.now(UTC))


@router.get("", response_model=Page[RecipeSummaryOut])
def list_recipes(
    ctx: RecipeView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    article_id: uuid.UUID | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[RecipeSummaryOut]:
    rows, total = _service(db, ctx).search(paging, search, status_filter, article_id)
    return Page(items=rows, total=total, limit=paging.limit, offset=paging.offset)


@router.post("", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
def create_recipe(body: RecipeCreate, ctx: RecipeManage, db: DbSession) -> RecipeOut:
    service = _service(db, ctx)
    recipe = service.create(body)
    db.commit()
    return service.get(recipe.id)


@router.get("/sheet", response_model=SheetOut)
def recipe_sheet(
    ctx: RecipeView,
    db: DbSession,
    article_id: uuid.UUID,
    quantity: Annotated[PositiveQuantity, Query()],
    packaging_id: uuid.UUID | None = None,
) -> SheetOut:
    """Fiche technique d'une ligne de commande (écran cuisine, D13)."""
    return _service(db, ctx).sheet(article_id, packaging_id, quantity)


@router.get("/profitability", response_model=ProfitabilityPage)
def recipe_profitability(
    ctx: RecipeView,
    db: DbSession,
    paging: Paging,
    date_from: date | None = None,
    date_to: date | None = None,
    site_id: uuid.UUID | None = None,
) -> ProfitabilityPage:
    """Rentabilité des plats vendus sur la période (palier R10) : portions, CA, coût réel des
    ingrédients consommés, coût par portion, marge (coûts masqués sans ``cost_view``)."""
    return _service(db, ctx).profitability(paging, date_from, date_to, site_id)


@router.get("/{recipe_id}", response_model=RecipeOut)
def get_recipe(recipe_id: uuid.UUID, ctx: RecipeView, db: DbSession) -> RecipeOut:
    return _service(db, ctx).get(recipe_id)


@router.put("/{recipe_id}", response_model=RecipeOut)
def update_recipe(
    recipe_id: uuid.UUID, body: RecipeUpdate, ctx: RecipeManage, db: DbSession
) -> RecipeOut:
    service = _service(db, ctx)
    service.update_notes(recipe_id, body.notes)
    db.commit()
    return service.get(recipe_id)


@router.get("/{recipe_id}/versions", response_model=list[RecipeVersionOut])
def list_versions(recipe_id: uuid.UUID, ctx: RecipeView, db: DbSession) -> list[RecipeVersionOut]:
    return _service(db, ctx).versions(recipe_id)


@router.post("/{recipe_id}/versions", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
def create_version(
    recipe_id: uuid.UUID, body: RecipeVersionIn, ctx: RecipeManage, db: DbSession
) -> RecipeOut:
    service = _service(db, ctx)
    service.new_version(recipe_id, body)
    db.commit()
    return service.get(recipe_id)


@router.post("/{recipe_id}/activate", response_model=RecipeOut)
def activate_recipe(recipe_id: uuid.UUID, ctx: RecipeManage, db: DbSession) -> RecipeOut:
    service = _service(db, ctx)
    service.set_active(recipe_id, True)
    db.commit()
    return service.get(recipe_id)


@router.post("/{recipe_id}/deactivate", response_model=RecipeOut)
def deactivate_recipe(recipe_id: uuid.UUID, ctx: RecipeManage, db: DbSession) -> RecipeOut:
    service = _service(db, ctx)
    service.set_active(recipe_id, False)
    db.commit()
    return service.get(recipe_id)


@router.get("/{recipe_id}/costs", response_model=list[SiteCostOut])
def recipe_costs(
    recipe_id: uuid.UUID, ctx: RecipeView, db: DbSession, site_id: uuid.UUID | None = None
) -> list[SiteCostOut]:
    return _service(db, ctx).costs(recipe_id, site_id)


@router.get("/{recipe_id}/availability", response_model=list[SiteAvailabilityOut])
def recipe_availability(
    recipe_id: uuid.UUID, ctx: RecipeView, db: DbSession, site_id: uuid.UUID | None = None
) -> list[SiteAvailabilityOut]:
    return _service(db, ctx).availability(recipe_id, site_id)
