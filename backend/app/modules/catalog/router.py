import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.errors import ForbiddenError
from app.modules.catalog.schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleUpdate,
    CategoryInput,
    CategoryOut,
    PackagingCreate,
    PackagingOut,
    PackagingUpdate,
    PriceChangeOut,
)
from app.modules.catalog.service import ArticleService, CategoryService, PackagingService
from app.platform.context import (
    DbSession,
    RequestContext,
    require_any_permission,
    require_permission,
)
from app.platform.costs import CATALOG_COST_FIELDS, cost_masking_route
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(route_class=cost_masking_route(CATALOG_COST_FIELDS))


CategoryView = Annotated[RequestContext, Depends(require_permission("catalog.category.view"))]
CategoryCreate = Annotated[RequestContext, Depends(require_permission("catalog.category.create"))]
CategoryUpdate = Annotated[RequestContext, Depends(require_permission("catalog.category.update"))]
CategoryStatus = Annotated[RequestContext, Depends(require_permission("catalog.category.status"))]
ArticleView = Annotated[RequestContext, Depends(require_permission("catalog.article.view"))]
ArticleCreateCtx = Annotated[RequestContext, Depends(require_permission("catalog.article.create"))]
# Modification : informations générales (``update``) et / ou prix (``price_update``) ; le
# service contrôle chaque champ modifié (Lot 3-A).
ArticleUpdateCtx = Annotated[
    RequestContext,
    Depends(require_any_permission("catalog.article.update", "catalog.article.price_update")),
]
ArticleStatus = Annotated[RequestContext, Depends(require_permission("catalog.article.status"))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]


# --- Catégories --------------------------------------------------------------------------------


@router.get("/categories", response_model=Page[CategoryOut], tags=["catalog"])
def list_categories(
    ctx: CategoryView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[CategoryOut]:
    items, total = CategoryService(db, ctx).search(paging, search, status_filter)
    return Page(
        items=[CategoryOut.model_validate(c) for c in items],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.post(
    "/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED, tags=["catalog"]
)
def create_category(body: CategoryInput, ctx: CategoryCreate, db: DbSession) -> CategoryOut:
    category = CategoryService(db, ctx).create(body)
    db.commit()
    return CategoryOut.model_validate(category)


@router.get("/categories/{category_id}", response_model=CategoryOut, tags=["catalog"])
def get_category(category_id: uuid.UUID, ctx: CategoryView, db: DbSession) -> CategoryOut:
    return CategoryOut.model_validate(CategoryService(db, ctx).get(category_id))


@router.patch("/categories/{category_id}", response_model=CategoryOut, tags=["catalog"])
def update_category(
    category_id: uuid.UUID, body: CategoryInput, ctx: CategoryUpdate, db: DbSession
) -> CategoryOut:
    category = CategoryService(db, ctx).update(category_id, body)
    db.commit()
    return CategoryOut.model_validate(category)


@router.post("/categories/{category_id}/activate", response_model=CategoryOut, tags=["catalog"])
def activate_category(category_id: uuid.UUID, ctx: CategoryStatus, db: DbSession) -> CategoryOut:
    category = CategoryService(db, ctx).set_active(category_id, True)
    db.commit()
    return CategoryOut.model_validate(category)


@router.post("/categories/{category_id}/deactivate", response_model=CategoryOut, tags=["catalog"])
def deactivate_category(category_id: uuid.UUID, ctx: CategoryStatus, db: DbSession) -> CategoryOut:
    category = CategoryService(db, ctx).set_active(category_id, False)
    db.commit()
    return CategoryOut.model_validate(category)


# --- Articles ----------------------------------------------------------------------------------


@router.get(
    "/articles",
    response_model=Page[ArticleOut],
    response_model_exclude_unset=True,
    tags=["catalog"],
)
def list_articles(
    ctx: ArticleView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
    category_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    stock_managed: bool | None = None,
) -> Page[ArticleOut]:
    service = ArticleService(db, ctx)
    items, total = service.search(
        paging, search, status_filter, category_id, supplier_id, stock_managed
    )
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.get(
    "/articles/by-barcode/{barcode}",
    response_model=ArticleOut,
    response_model_exclude_unset=True,
    tags=["catalog"],
)
def get_article_by_barcode(barcode: str, ctx: ArticleView, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    return service.to_out([service.find_active_by_barcode(barcode)])[0]


@router.post(
    "/articles",
    response_model=ArticleOut,
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
    tags=["catalog"],
)
def create_article(body: ArticleCreate, ctx: ArticleCreateCtx, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.create(body)
    db.commit()
    return service.to_out([article])[0]


@router.get(
    "/articles/{article_id}",
    response_model=ArticleOut,
    response_model_exclude_unset=True,
    tags=["catalog"],
)
def get_article(article_id: uuid.UUID, ctx: ArticleView, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    return service.to_out([service.get(article_id)])[0]


@router.patch(
    "/articles/{article_id}",
    response_model=ArticleOut,
    response_model_exclude_unset=True,
    tags=["catalog"],
)
def update_article(
    article_id: uuid.UUID, body: ArticleUpdate, ctx: ArticleUpdateCtx, db: DbSession
) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.update(article_id, body)
    db.commit()
    return service.to_out([article])[0]


@router.get(
    "/articles/{article_id}/price-history",
    response_model=Page[PriceChangeOut],
    response_model_exclude_unset=True,
    tags=["catalog"],
)
def article_price_history(
    article_id: uuid.UUID, ctx: ArticleView, db: DbSession, paging: Paging
) -> Page[PriceChangeOut]:
    """Historique des prix (journal d'audit) : réservé aux habilités sur les prix
    (``price_update``) ou au journal d'audit (``audit.log.view``) ; prix d'achat avec
    ``cost_view`` seulement."""
    if not (
        ctx.has_permission("catalog.article.price_update") or ctx.has_permission("audit.log.view")
    ):
        raise ForbiddenError("Permission insuffisante", code="permission_denied")
    items, total = ArticleService(db, ctx).price_history(article_id, paging)
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@router.post(
    "/articles/{article_id}/activate",
    response_model=ArticleOut,
    response_model_exclude_unset=True,
    tags=["catalog"],
)
def activate_article(article_id: uuid.UUID, ctx: ArticleStatus, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.set_active(article_id, True)
    db.commit()
    return service.to_out([article])[0]


@router.post(
    "/articles/{article_id}/deactivate",
    response_model=ArticleOut,
    response_model_exclude_unset=True,
    tags=["catalog"],
)
def deactivate_article(article_id: uuid.UUID, ctx: ArticleStatus, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.set_active(article_id, False)
    db.commit()
    return service.to_out([article])[0]


# --- Conditionnements (Lot 3-B, ADR-0040) ------------------------------------------------------
# Mêmes droits que l'article : consultation ``view`` ; nom, conversion et état ``update`` ; prix
# ``price_update`` (le service contrôle chaque champ).


@router.get(
    "/articles/{article_id}/packagings", response_model=Page[PackagingOut], tags=["catalog"]
)
def list_packagings(
    article_id: uuid.UUID,
    ctx: ArticleView,
    db: DbSession,
    paging: Paging,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[PackagingOut]:
    service = PackagingService(db, ctx)
    items, total = service.search(article_id, paging, status_filter)
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post(
    "/articles/{article_id}/packagings",
    response_model=PackagingOut,
    status_code=status.HTTP_201_CREATED,
    tags=["catalog"],
)
def create_packaging(
    article_id: uuid.UUID, body: PackagingCreate, ctx: ArticleUpdateCtx, db: DbSession
) -> PackagingOut:
    service = PackagingService(db, ctx)
    packaging = service.create(article_id, body)
    db.commit()
    return service.to_out([packaging])[0]


@router.patch("/packagings/{packaging_id}", response_model=PackagingOut, tags=["catalog"])
def update_packaging(
    packaging_id: uuid.UUID, body: PackagingUpdate, ctx: ArticleUpdateCtx, db: DbSession
) -> PackagingOut:
    service = PackagingService(db, ctx)
    packaging = service.update(packaging_id, body)
    db.commit()
    return service.to_out([packaging])[0]


@router.post("/packagings/{packaging_id}/activate", response_model=PackagingOut, tags=["catalog"])
def activate_packaging(
    packaging_id: uuid.UUID, ctx: ArticleUpdateCtx, db: DbSession
) -> PackagingOut:
    service = PackagingService(db, ctx)
    packaging = service.set_active(packaging_id, True)
    db.commit()
    return service.to_out([packaging])[0]


@router.post("/packagings/{packaging_id}/deactivate", response_model=PackagingOut, tags=["catalog"])
def deactivate_packaging(
    packaging_id: uuid.UUID, ctx: ArticleUpdateCtx, db: DbSession
) -> PackagingOut:
    service = PackagingService(db, ctx)
    packaging = service.set_active(packaging_id, False)
    db.commit()
    return service.to_out([packaging])[0]
