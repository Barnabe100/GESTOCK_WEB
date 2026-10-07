import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.catalog.api import lot_tracking_available, resolve_barcode
from app.modules.catalog.assortment_service import AssortmentService
from app.modules.catalog.schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleSiteOut,
    ArticleUpdate,
    AssortmentArticlesInput,
    AssortmentChangeOut,
    AssortmentCopyInput,
    AssortmentRemovalOut,
    AssortmentStatusFilter,
    BarcodeCreate,
    BarcodeOut,
    CategoryInput,
    CategoryOut,
    LotTrackingOut,
    PackagingCreate,
    PackagingOut,
    PackagingUpdate,
    PriceChangeOut,
    ScanOut,
    SiteArticleOut,
)
from app.modules.catalog.service import (
    ArticleService,
    BarcodeService,
    CategoryService,
    PackagingService,
)
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
# Lot 3-D : codes-barres = donnée générale de l'article (aucune permission nouvelle).
BarcodeManage = Annotated[RequestContext, Depends(require_permission("catalog.article.update"))]
# Assortiment par site (ADR-0046, D5) : nature ``admin`` ; le service revérifie la permission
# et l'abonnement sur le site VISÉ.
AssortmentManage = Annotated[
    RequestContext, Depends(require_permission("catalog.assortment.manage"))
]
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
    site_id: uuid.UUID | None = None,
    in_site_assortment: bool | None = None,
) -> Page[ArticleOut]:
    """``site_id`` (ADR-0046) : chaque article indique son état dans l'assortiment de ce site
    (``site_assortment``) — le catalogue reste global au tenant. ``in_site_assortment`` : filtre
    sur cet état (ajout à l'assortiment : ``false``)."""
    service = ArticleService(db, ctx)
    items, total = service.search(
        paging,
        search,
        status_filter,
        category_id,
        supplier_id,
        stock_managed,
        site_id=site_id,
        in_site_assortment=in_site_assortment,
    )
    return Page(
        items=service.to_out(items, site_id), total=total, limit=paging.limit, offset=paging.offset
    )


@router.get("/lot-tracking", response_model=LotTrackingOut, tags=["catalog"])
def lot_tracking(ctx: ArticleView) -> LotTrackingOut:
    """Fermeture P1-b (Lot 3-G, ADR-0045) : le suivi par lot est-il activable ? Faux tant que la
    consommation des lots (Lot 3-H) n'est pas livrée ; le serveur refuse aussi l'activation."""
    return LotTrackingOut(available=lot_tracking_available())


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


# --- Codes-barres (Lot 3-D, ADR-0042) ---------------------------------------------------------
# Un code identifie UNE présentation : l'article en unité de base ou un conditionnement. Code
# principal : champ ``barcode`` de l'article ; ici, codes supplémentaires et de conditionnement.


@router.get("/barcodes/resolve", response_model=ScanOut, tags=["catalog"])
def resolve_barcode_route(
    ctx: ArticleView,
    db: DbSession,
    code: Annotated[str, Query(min_length=1, max_length=50)],
    site_id: uuid.UUID | None = None,
) -> ScanOut:
    """Scan des écrans opérationnels : égalité EXACTE parmi les présentations actives, sinon
    ``404 barcode_unknown`` — jamais de recherche partielle ni de premier résultat.
    ``site_id`` (ADR-0046) : état de l'article dans l'assortiment de ce site
    (``article.site_assortment``) ; l'enregistrement du document refuse un article hors
    assortiment (``422 article_not_in_site_assortment``)."""
    match = resolve_barcode(db, code)
    if match is None:
        raise NotFoundError("Code-barres inconnu", code="barcode_unknown")
    articles = ArticleService(db, ctx)
    packagings = PackagingService(db, ctx)
    return ScanOut(
        article=articles.to_out([articles.get(match.article_id)], site_id)[0],
        packaging=(
            packagings.to_out([packagings.get(match.packaging_id)])[0]
            if match.packaging_id
            else None
        ),
    )


@router.get("/articles/{article_id}/barcodes", response_model=Page[BarcodeOut], tags=["catalog"])
def list_barcodes(
    article_id: uuid.UUID, ctx: ArticleView, db: DbSession, paging: Paging
) -> Page[BarcodeOut]:
    """Tous les codes de l'article : principal, supplémentaires, conditionnements."""
    service = BarcodeService(db, ctx)
    items, total = service.search(article_id, paging)
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.post(
    "/articles/{article_id}/barcodes",
    response_model=BarcodeOut,
    status_code=status.HTTP_201_CREATED,
    tags=["catalog"],
)
def add_article_barcode(
    article_id: uuid.UUID, body: BarcodeCreate, ctx: BarcodeManage, db: DbSession
) -> BarcodeOut:
    service = BarcodeService(db, ctx)
    barcode = service.add(article_id, body.code)
    db.commit()
    return service.to_out([barcode])[0]


@router.post(
    "/packagings/{packaging_id}/barcodes",
    response_model=BarcodeOut,
    status_code=status.HTTP_201_CREATED,
    tags=["catalog"],
)
def add_packaging_barcode(
    packaging_id: uuid.UUID, body: BarcodeCreate, ctx: BarcodeManage, db: DbSession
) -> BarcodeOut:
    service = BarcodeService(db, ctx)
    article_id = PackagingService(db, ctx).get(packaging_id).article_id
    barcode = service.add(article_id, body.code, packaging_id)
    db.commit()
    return service.to_out([barcode])[0]


@router.delete("/barcodes/{barcode_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["catalog"])
def remove_barcode(barcode_id: uuid.UUID, ctx: BarcodeManage, db: DbSession) -> None:
    BarcodeService(db, ctx).remove(barcode_id)
    db.commit()


# --- Assortiment par site (Recette, étape 1, ADR-0046) ------------------------------------------


@router.get("/sites/{site_id}/articles", response_model=Page[SiteArticleOut], tags=["catalog"])
def list_site_articles(
    site_id: uuid.UUID,
    ctx: ArticleView,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    status_filter: Annotated[AssortmentStatusFilter, Query(alias="status")] = (
        AssortmentStatusFilter.ACTIVE
    ),
) -> Page[SiteArticleOut]:
    """Assortiment du site (catalogue ≠ assortiment ≠ stock) : articles actifs (défaut),
    retirés ou tous."""
    items, total = AssortmentService(db, ctx).list_site_articles(
        site_id, paging, search=search, category_id=category_id, status=status_filter
    )
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@router.post("/sites/{site_id}/articles", response_model=AssortmentChangeOut, tags=["catalog"])
def add_site_articles(
    site_id: uuid.UUID, body: AssortmentArticlesInput, ctx: AssortmentManage, db: DbSession
) -> AssortmentChangeOut:
    """Ajout / réactivation (idempotent, tout ou rien) ; aucun stock créé."""
    result = AssortmentService(db, ctx).add(site_id, set(body.article_ids))
    db.commit()
    return result


@router.post(
    "/sites/{site_id}/articles/remove", response_model=AssortmentRemovalOut, tags=["catalog"]
)
def remove_site_articles(
    site_id: uuid.UUID, body: AssortmentArticlesInput, ctx: AssortmentManage, db: DbSession
) -> AssortmentRemovalOut:
    """Retrait (désactivation, tout ou rien) : refusé si stock / lots non nuls sur ce site
    (``409 article_has_stock``) ou documents ouverts (``409 article_in_open_documents``)."""
    result = AssortmentService(db, ctx).remove(site_id, set(body.article_ids))
    db.commit()
    return result


@router.post("/sites/{site_id}/articles/copy", response_model=AssortmentChangeOut, tags=["catalog"])
def copy_site_articles(
    site_id: uuid.UUID, body: AssortmentCopyInput, ctx: AssortmentManage, db: DbSession
) -> AssortmentChangeOut:
    """Copie de l'assortiment d'un autre site : ajout seulement, catégorie facultative."""
    result = AssortmentService(db, ctx).copy(site_id, body.source_site_id, body.category_id)
    db.commit()
    return result


@router.get("/articles/{article_id}/sites", response_model=list[ArticleSiteOut], tags=["catalog"])
def article_sites(article_id: uuid.UUID, ctx: ArticleView, db: DbSession) -> list[ArticleSiteOut]:
    """État de l'article dans l'assortiment des sites visibles du membre."""
    return AssortmentService(db, ctx).article_sites(article_id)
