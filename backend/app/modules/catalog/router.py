import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.catalog.schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleUpdate,
    CategoryInput,
    CategoryOut,
)
from app.modules.catalog.service import ArticleService, CategoryService
from app.platform.context import DbSession, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter()


CategoryView = Annotated[RequestContext, Depends(require_permission("catalog.category.view"))]
CategoryCreate = Annotated[RequestContext, Depends(require_permission("catalog.category.create"))]
CategoryUpdate = Annotated[RequestContext, Depends(require_permission("catalog.category.update"))]
CategoryStatus = Annotated[RequestContext, Depends(require_permission("catalog.category.status"))]
ArticleView = Annotated[RequestContext, Depends(require_permission("catalog.article.view"))]
ArticleCreateCtx = Annotated[RequestContext, Depends(require_permission("catalog.article.create"))]
ArticleUpdateCtx = Annotated[RequestContext, Depends(require_permission("catalog.article.update"))]
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


@router.get("/articles", response_model=Page[ArticleOut], tags=["catalog"])
def list_articles(
    ctx: ArticleView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
    category_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
) -> Page[ArticleOut]:
    service = ArticleService(db, ctx)
    items, total = service.search(paging, search, status_filter, category_id, supplier_id)
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.get("/articles/by-barcode/{barcode}", response_model=ArticleOut, tags=["catalog"])
def get_article_by_barcode(barcode: str, ctx: ArticleView, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    return service.to_out([service.find_active_by_barcode(barcode)])[0]


@router.post(
    "/articles", response_model=ArticleOut, status_code=status.HTTP_201_CREATED, tags=["catalog"]
)
def create_article(body: ArticleCreate, ctx: ArticleCreateCtx, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.create(body)
    db.commit()
    return service.to_out([article])[0]


@router.get("/articles/{article_id}", response_model=ArticleOut, tags=["catalog"])
def get_article(article_id: uuid.UUID, ctx: ArticleView, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    return service.to_out([service.get(article_id)])[0]


@router.patch("/articles/{article_id}", response_model=ArticleOut, tags=["catalog"])
def update_article(
    article_id: uuid.UUID, body: ArticleUpdate, ctx: ArticleUpdateCtx, db: DbSession
) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.update(article_id, body)
    db.commit()
    return service.to_out([article])[0]


@router.post("/articles/{article_id}/activate", response_model=ArticleOut, tags=["catalog"])
def activate_article(article_id: uuid.UUID, ctx: ArticleStatus, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.set_active(article_id, True)
    db.commit()
    return service.to_out([article])[0]


@router.post("/articles/{article_id}/deactivate", response_model=ArticleOut, tags=["catalog"])
def deactivate_article(article_id: uuid.UUID, ctx: ArticleStatus, db: DbSession) -> ArticleOut:
    service = ArticleService(db, ctx)
    article = service.set_active(article_id, False)
    db.commit()
    return service.to_out([article])[0]
