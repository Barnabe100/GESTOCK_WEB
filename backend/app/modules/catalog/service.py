"""Services du catalogue. Ne valident pas la transaction (ADR-0008) ; chaque changement est
audité dans la même transaction."""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, BusinessRuleError, ConflictError, NotFoundError
from app.modules.catalog.models import Article, Category
from app.modules.catalog.schemas import ArticleCreate, ArticleOut, ArticleUpdate, CategoryInput
from app.modules.suppliers.api import get_supplier_ref, supplier_names
from app.platform.audit.service import audit_action, changes
from app.platform.context import RequestContext
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter, text_sort
from app.shared.schemas import StatusFilter

CATEGORY_SORT = {"name": text_sort(Category.name), "created_at": Category.created_at}
ARTICLE_SORT = {
    "reference": text_sort(Article.reference),
    "designation": text_sort(Article.designation),
    "category": text_sort(Category.name),
    "sale_price": Article.sale_price,
    "purchase_price": Article.purchase_price,
    "created_at": Article.created_at,
}

_CONSTRAINT_ERRORS = {
    "uq_catalog_categories_tenant_name": ("category_name_taken", "Cette catégorie existe déjà"),
    "uq_catalog_articles_tenant_reference": (
        "article_reference_taken",
        "Cette référence est déjà utilisée",
    ),
    "uq_catalog_articles_tenant_barcode_active": (
        "article_barcode_taken",
        "Ce code-barres est déjà utilisé par un article actif",
    ),
}


def _flush(db: Session) -> None:
    """Traduit une violation d'unicité (y compris en cas de concurrence) en 409 explicite."""
    try:
        db.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        code, message = _CONSTRAINT_ERRORS.get(constraint or "", ("conflict", "Conflit"))
        raise ConflictError(message, code=code) from exc


def _status_condition(column: Any, status: StatusFilter) -> Any:
    return None if status is StatusFilter.ALL else column.is_(status is StatusFilter.ACTIVE)


class CategoryService:
    """Règles CAT-01 à CAT-06."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def search(
        self, params: PageParams, search: str | None, status: StatusFilter
    ) -> tuple[list[Category], int]:
        stmt = select(Category)
        for condition in (
            search_filter(search, Category.name),
            _status_condition(Category.is_active, status),
        ):
            if condition is not None:
                stmt = stmt.where(condition)
        stmt = apply_sort(stmt, params.sort, CATEGORY_SORT, "name", Category.id)
        return paginate(self.db, stmt, params)

    def get(self, category_id: uuid.UUID) -> Category:
        category = self.db.get(Category, category_id)
        if category is None:
            raise NotFoundError("Catégorie introuvable", code="category_not_found")
        return category

    def create(self, data: CategoryInput) -> Category:
        category = Category(tenant_id=self.ctx.tenant_id, name=data.name)
        self.db.add(category)
        _flush(self.db)
        audit_action(
            self.db,
            self.ctx,
            "category.created",
            entity_type="category",
            entity_id=category.id,
            data={"name": category.name},
        )
        return category

    def update(self, category_id: uuid.UUID, data: CategoryInput) -> Category:
        category = self.get(category_id)
        before = {"name": category.name}
        category.name = data.name
        _flush(self.db)
        diff = changes(before, {"name": category.name})
        if diff:
            audit_action(
                self.db,
                self.ctx,
                "category.updated",
                entity_type="category",
                entity_id=category.id,
                data=diff,
            )
        return category

    def set_active(self, category_id: uuid.UUID, active: bool) -> Category:
        category = self.get(category_id)
        if category.is_active != active:
            category.is_active = active
            audit_action(
                self.db,
                self.ctx,
                "category.activated" if active else "category.deactivated",
                entity_type="category",
                entity_id=category.id,
                data={"name": category.name},
            )
        return category


class ArticleService:
    """Règles ART-01 à ART-16 (hors stock, tenu par site dans le module ``stock``)."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    # --- Lecture ----------------------------------------------------------------------------

    def search(
        self,
        params: PageParams,
        search: str | None,
        status: StatusFilter,
        category_id: uuid.UUID | None = None,
        supplier_id: uuid.UUID | None = None,
    ) -> tuple[list[Article], int]:
        stmt = select(Article).join(
            Category,
            (Category.id == Article.category_id) & (Category.tenant_id == Article.tenant_id),
        )
        conditions = [
            search_filter(
                search, Article.reference, Article.designation, Article.barcode, Category.name
            ),
            _status_condition(Article.is_active, status),
            Article.category_id == category_id if category_id else None,
            Article.main_supplier_id == supplier_id if supplier_id else None,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        stmt = apply_sort(stmt, params.sort, ARTICLE_SORT, "reference", Article.id)
        return paginate(self.db, stmt, params)

    def get(self, article_id: uuid.UUID) -> Article:
        article = self.db.get(Article, article_id)
        if article is None:
            raise NotFoundError("Article introuvable", code="article_not_found")
        return article

    def find_active_by_barcode(self, barcode: str) -> Article:
        """ART-15 : un scan ne retombe jamais sur un article désactivé."""
        article = self.db.scalars(
            select(Article).where(Article.barcode == barcode.strip(), Article.is_active.is_(True))
        ).one_or_none()
        if article is None:
            raise NotFoundError("Aucun article actif pour ce code-barres", code="article_not_found")
        return article

    def to_out(self, articles: list[Article]) -> list[ArticleOut]:
        names = supplier_names(
            self.db, {a.main_supplier_id for a in articles if a.main_supplier_id}
        )
        return [
            ArticleOut(
                id=a.id,
                reference=a.reference,
                designation=a.designation,
                category_id=a.category_id,
                category_name=a.category.name,
                unit=a.unit,
                main_supplier_id=a.main_supplier_id,
                main_supplier_name=names.get(a.main_supplier_id) if a.main_supplier_id else None,
                purchase_price=a.purchase_price,
                sale_price=a.sale_price,
                min_stock=a.min_stock,
                max_stock=a.max_stock,
                description=a.description,
                barcode=a.barcode,
                is_active=a.is_active,
                created_at=a.created_at,
                updated_at=a.updated_at,
            )
            for a in articles
        ]

    # --- Règles de sélection (ART-10) --------------------------------------------------------

    def _ensure_active_category(self, category_id: uuid.UUID) -> None:
        category = self.db.get(Category, category_id)
        if category is None:
            raise BusinessRuleError("Catégorie introuvable", code="category_not_found")
        if not category.is_active:
            raise BusinessRuleError(
                "La catégorie sélectionnée est inactive", code="category_inactive"
            )

    def _ensure_active_supplier(self, supplier_id: uuid.UUID) -> None:
        if "suppliers" not in self.ctx.capabilities.modules:
            raise BusinessRuleError(
                "Le module Fournisseurs n'est pas actif", code="module_unavailable"
            )
        supplier = get_supplier_ref(self.db, supplier_id)
        if supplier is None:
            raise BusinessRuleError("Fournisseur introuvable", code="supplier_not_found")
        if not supplier.is_active:
            raise BusinessRuleError(
                "Le fournisseur sélectionné est inactif", code="supplier_inactive"
            )

    def _ensure_barcode_free(self, barcode: str, exclude_id: uuid.UUID | None = None) -> None:
        stmt = select(Article.id).where(Article.barcode == barcode, Article.is_active.is_(True))
        if exclude_id is not None:
            stmt = stmt.where(Article.id != exclude_id)
        if self.db.scalars(stmt).first() is not None:
            raise ConflictError(
                "Ce code-barres est déjà utilisé par un article actif",
                code="article_barcode_taken",
            )

    @staticmethod
    def _check_thresholds(article: Article) -> None:
        if article.max_stock is not None and article.max_stock < article.min_stock:
            raise BusinessRuleError(
                "Le stock maximum doit être supérieur ou égal au stock minimum",
                code="invalid_stock_thresholds",
            )

    # --- Écriture ---------------------------------------------------------------------------

    def create(self, data: ArticleCreate) -> Article:
        self._ensure_active_category(data.category_id)
        if data.main_supplier_id is not None:
            self._ensure_active_supplier(data.main_supplier_id)
        if data.barcode:
            self._ensure_barcode_free(data.barcode)
        article = Article(tenant_id=self.ctx.tenant_id, **data.model_dump())
        self._check_thresholds(article)
        self.db.add(article)
        _flush(self.db)
        self.db.refresh(article)
        audit_action(
            self.db,
            self.ctx,
            "article.created",
            entity_type="article",
            entity_id=article.id,
            data={"reference": article.reference, "designation": article.designation},
        )
        return article

    def update(self, article_id: uuid.UUID, data: ArticleUpdate) -> Article:
        article = self.get(article_id)
        updates = data.model_dump(exclude_unset=True)
        required = (
            "reference",
            "designation",
            "category_id",
            "unit",
            "purchase_price",
            "sale_price",
            "min_stock",
        )
        missing = [field for field in required if field in updates and updates[field] is None]
        if missing:
            raise AppError(
                "Champs obligatoires manquants", code="validation_error", extra={"fields": missing}
            )

        # Une nouvelle association doit être active ; l'association existante est conservée
        # même si la catégorie / le fournisseur est devenu inactif (ART-10).
        if "category_id" in updates and updates["category_id"] != article.category_id:
            self._ensure_active_category(updates["category_id"])
        new_supplier = updates.get("main_supplier_id")
        if new_supplier is not None and new_supplier != article.main_supplier_id:
            self._ensure_active_supplier(new_supplier)
        if updates.get("barcode") and article.is_active and updates["barcode"] != article.barcode:
            self._ensure_barcode_free(updates["barcode"], exclude_id=article.id)

        before = {key: getattr(article, key) for key in updates}
        for key, value in updates.items():
            setattr(article, key, value)
        self._check_thresholds(article)
        _flush(self.db)
        diff = changes(before, updates)
        if diff:
            audit_action(
                self.db,
                self.ctx,
                "article.updated",
                entity_type="article",
                entity_id=article.id,
                data=diff,
            )
        self.db.refresh(article)
        return article

    def set_active(self, article_id: uuid.UUID, active: bool) -> Article:
        article = self.get(article_id)
        if article.is_active == active:
            return article
        if active and article.barcode:
            self._ensure_barcode_free(article.barcode, exclude_id=article.id)  # ART-16
        article.is_active = active
        _flush(self.db)
        audit_action(
            self.db,
            self.ctx,
            "article.activated" if active else "article.deactivated",
            entity_type="article",
            entity_id=article.id,
            data={"reference": article.reference},
        )
        return article
