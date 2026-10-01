"""Services du catalogue. Ne valident pas la transaction (ADR-0008) ; chaque changement est
audité dans la même transaction."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    AppError,
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.modules.catalog.models import Article, Category, Packaging
from app.modules.catalog.sales_port import packagings_in_use
from app.modules.catalog.schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleUpdate,
    CategoryInput,
    PackagingCreate,
    PackagingOut,
    PackagingUpdate,
    PriceChangeOut,
)
from app.modules.catalog.stock_port import sites_with_stock
from app.modules.suppliers.api import get_supplier_ref, supplier_names
from app.platform.audit.service import audit_action, changes, entity_field_history
from app.platform.context import RequestContext
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter, text_sort
from app.shared.schemas import StatusFilter

# Lot 3-A (ADR-0039) : permissions distinctes pour les prix et les coûts internes.
ARTICLE_UPDATE = "catalog.article.update"
PRICE_UPDATE = "catalog.article.price_update"
COST_VIEW = "catalog.article.cost_view"
SALE_PRICE = "sale_price"
PURCHASE_PRICE = "purchase_price"
PRICE_FIELDS = (SALE_PRICE, PURCHASE_PRICE)
CENT = Decimal("0.01")

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
    "uq_catalog_packagings_article_name_active": (
        "packaging_name_taken",
        "Un conditionnement actif de cet article porte déjà ce nom",
    ),
}
PACKAGING_SORT = {
    "conversion": Packaging.conversion,
    "name": text_sort(Packaging.name),
    "sale_price": Packaging.sale_price,
    "created_at": Packaging.created_at,
}


def _flush(db: Session) -> None:
    """Traduit une violation d'unicité (y compris en cas de concurrence) en 409 explicite."""
    try:
        db.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        code, message = _CONSTRAINT_ERRORS.get(constraint or "", ("conflict", "Conflit"))
        raise ConflictError(message, code=code) from exc


def _ensure_price_allowed(ctx: RequestContext) -> None:
    """Prix catalogue (article ou conditionnement) : ``catalog.article.price_update``."""
    if not ctx.has_permission(PRICE_UPDATE):
        raise ForbiddenError(
            "Vous n'êtes pas autorisé à modifier les prix catalogue",
            code="price_update_not_allowed",
        )


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
        stock_managed: bool | None = None,
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
            Article.stock_managed.is_(stock_managed) if stock_managed is not None else None,
        ]
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        # Trier par prix d'achat révélerait l'ordre des coûts : réservé à ``cost_view``.
        sortable = (
            ARTICLE_SORT
            if self.can_view_costs
            else {k: v for k, v in ARTICLE_SORT.items() if k != PURCHASE_PRICE}
        )
        stmt = apply_sort(stmt, params.sort, sortable, "reference", Article.id)
        return paginate(self.db, stmt, params)

    @property
    def can_view_costs(self) -> bool:
        return self.ctx.has_permission(COST_VIEW)

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
        costs = self.can_view_costs
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
                sale_price=a.sale_price,
                min_stock=a.min_stock,
                max_stock=a.max_stock,
                description=a.description,
                barcode=a.barcode,
                is_active=a.is_active,
                stock_managed=a.stock_managed,
                decimal_quantity_allowed=a.decimal_quantity_allowed,
                created_at=a.created_at,
                updated_at=a.updated_at,
                # Coût interne : champ ABSENT de la réponse sans ``cost_view`` (les routes
                # sérialisent avec ``response_model_exclude_unset``).
                **({PURCHASE_PRICE: a.purchase_price} if costs else {}),
            )
            for a in articles
        ]

    def price_history(
        self, article_id: uuid.UUID, params: PageParams
    ) -> tuple[list[PriceChangeOut], int]:
        """Historique des prix de l'article, lu dans le journal d'audit existant (création et
        modifications) ; prix d'achat seulement avec ``cost_view``."""
        article = self.get(article_id)
        fields = PRICE_FIELDS if self.can_view_costs else (SALE_PRICE,)
        rows, total = entity_field_history(
            self.db,
            self.ctx.tenant_id,
            "article",
            article.id,
            actions=("article.created", "article.updated"),
            fields=fields,
            limit=params.limit,
            offset=params.offset,
        )
        items = []
        for log, user_name in rows:
            values: dict[str, Any] = {}
            for field in fields:
                before, after = _price_change(log.action, log.data.get(field))
                values[f"{field}_before"] = before
                values[f"{field}_after"] = after
            items.append(
                PriceChangeOut(
                    id=log.id, occurred_at=log.occurred_at, user_name=user_name, **values
                )
            )
        return items, total

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

    def _ensure_price_allowed(self) -> None:
        _ensure_price_allowed(self.ctx)

    def create(self, data: ArticleCreate) -> Article:
        # Un prix catalogue (vente ou achat) ne se fixe qu'avec ``price_update`` (Lot 3-A) ;
        # sans elle, l'article est créé aux prix par défaut (0), à compléter par un habilité.
        if any(getattr(data, field) != 0 for field in PRICE_FIELDS):
            self._ensure_price_allowed()
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
            data={
                "reference": article.reference,
                "designation": article.designation,
                # Prix initiaux : point de départ de l'historique des prix.
                SALE_PRICE: format(article.sale_price, "f"),
                PURCHASE_PRICE: format(article.purchase_price, "f"),
                "stock_managed": article.stock_managed,
                "decimal_quantity_allowed": article.decimal_quantity_allowed,
            },
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
            "stock_managed",
            "decimal_quantity_allowed",
        )
        missing = [field for field in required if field in updates and updates[field] is None]
        if missing:
            raise AppError(
                "Champs obligatoires manquants", code="validation_error", extra={"fields": missing}
            )

        # Lot 3-A : valeurs inchangées ignorées (formulaire complet) ; les prix exigent
        # ``price_update``, les autres champs ``catalog.article.update`` — aucune des deux
        # n'accorde l'autre.
        for field in PRICE_FIELDS:
            if updates.get(field) is not None:
                updates[field] = updates[field].quantize(CENT)  # audit : « 130.00 »
        updates = {k: v for k, v in updates.items() if getattr(article, k) != v}
        if any(field in updates for field in PRICE_FIELDS):
            self._ensure_price_allowed()
        if any(field not in PRICE_FIELDS for field in updates) and not self.ctx.has_permission(
            ARTICLE_UPDATE
        ):
            raise ForbiddenError("Permission insuffisante", code="permission_denied")
        if updates.get("stock_managed") is False:
            self._ensure_no_stock(article)
        if updates.get("decimal_quantity_allowed") is False:
            self._ensure_whole_packagings(article)

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

    def _ensure_no_stock(self, article: Article) -> None:
        """« Géré » → « non géré » : stock nul sur TOUS les sites du tenant. Verrou exclusif de
        l'article d'abord : une opération de stock en cours (verrou partagé de l'article) se
        termine avant la vérification, et aucune ne peut commencer avant la fin de celle-ci.
        Aucun ajustement ni mouvement automatique."""
        self.db.execute(select(Article.id).where(Article.id == article.id).with_for_update()).one()
        sites = sites_with_stock(self.db, self.ctx.tenant_id, article.id)
        if sites:
            raise ConflictError(
                "Cet article a encore du stock sur au moins un site : ramenez-le à zéro "
                "avant de le passer en article non géré en stock",
                code="article_has_stock",
                extra={"sites": sites},
            )

    def _ensure_whole_packagings(self, article: Article) -> None:
        """Quantités entières seulement : aucun conditionnement ACTIF à conversion décimale
        (il ne pourrait plus être vendu en quantité entière d'unités de base)."""
        names = self.db.scalars(
            select(Packaging.name)
            .where(
                Packaging.article_id == article.id,
                Packaging.is_active.is_(True),
                Packaging.conversion != func.trunc(Packaging.conversion),
            )
            .order_by(Packaging.name)
        ).all()
        if names:
            raise ConflictError(
                "Des conditionnements actifs de cet article ont une conversion décimale : "
                "désactivez-les avant d'interdire les quantités décimales",
                code="article_has_fractional_packagings",
                extra={"packagings": list(names)},
            )

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


class PackagingService:
    """Conditionnements de vente d'un article (Lot 3-B, ADR-0040). Mêmes droits que l'article :
    nom, conversion et état avec ``catalog.article.update`` ; prix avec
    ``catalog.article.price_update`` (création sans elle : prix 0). Jamais supprimé.
    Conversion figée dès qu'une vente (brouillon compris) utilise le conditionnement."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def _article(self, article_id: uuid.UUID) -> Article:
        return ArticleService(self.db, self.ctx).get(article_id)

    def get(self, packaging_id: uuid.UUID, *, lock: bool = False) -> Packaging:
        stmt = select(Packaging).where(Packaging.id == packaging_id)
        packaging = self.db.scalars(stmt.with_for_update() if lock else stmt).one_or_none()
        if packaging is None:
            raise NotFoundError("Conditionnement introuvable", code="packaging_not_found")
        return packaging

    def search(
        self, article_id: uuid.UUID, params: PageParams, status: StatusFilter
    ) -> tuple[list[Packaging], int]:
        self._article(article_id)
        stmt = select(Packaging).where(Packaging.article_id == article_id)
        condition = _status_condition(Packaging.is_active, status)
        if condition is not None:
            stmt = stmt.where(condition)
        stmt = apply_sort(stmt, params.sort, PACKAGING_SORT, "conversion", Packaging.id)
        return paginate(self.db, stmt, params)

    def to_out(self, packagings: list[Packaging]) -> list[PackagingOut]:
        used = packagings_in_use(self.db, {p.id for p in packagings})
        return [
            PackagingOut(
                id=p.id,
                article_id=p.article_id,
                name=p.name,
                conversion=p.conversion,
                sale_price=p.sale_price,
                is_active=p.is_active,
                in_use=p.id in used,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in packagings
        ]

    def _require_general(self) -> None:
        if not self.ctx.has_permission(ARTICLE_UPDATE):
            raise ForbiddenError("Permission insuffisante", code="permission_denied")

    @staticmethod
    def _ensure_whole(article: Article, conversion: Decimal) -> None:
        """Article vendu en quantités entières : une conversion décimale donnerait des
        quantités de base fractionnaires (1 conditionnement = 2,5 pièces)."""
        if not article.decimal_quantity_allowed and conversion != conversion.to_integral_value():
            raise BusinessRuleError(
                "Cet article n'accepte pas les quantités décimales : la conversion doit être "
                "un nombre entier",
                code="packaging_conversion_not_whole",
            )

    def _audit(self, action: str, packaging: Packaging, article: Article, data: Any) -> None:
        audit_action(
            self.db,
            self.ctx,
            f"packaging.{action}",
            entity_type="packaging",
            entity_id=packaging.id,
            data={
                "article_id": str(article.id),
                "reference": article.reference,
                "name": packaging.name,
                **data,
            },
        )

    def create(self, article_id: uuid.UUID, data: PackagingCreate) -> Packaging:
        self._require_general()
        if data.sale_price != 0:
            _ensure_price_allowed(self.ctx)
        article = self._article(article_id)
        self._ensure_whole(article, data.conversion)
        packaging = Packaging(
            tenant_id=self.ctx.tenant_id,
            article_id=article.id,
            name=data.name,
            conversion=data.conversion,
            sale_price=data.sale_price.quantize(CENT),
            is_active=True,
        )
        self.db.add(packaging)
        _flush(self.db)
        self.db.refresh(packaging)
        self._audit(
            "created",
            packaging,
            article,
            {
                "conversion": format(packaging.conversion, "f"),
                SALE_PRICE: format(packaging.sale_price, "f"),
            },
        )
        return packaging

    def update(self, packaging_id: uuid.UUID, data: PackagingUpdate) -> Packaging:
        # Verrou exclusif d'abord : une vente en cours (verrou partagé du conditionnement) se
        # termine avant la vérification d'usage ; aucune ne lit une conversion en transition.
        packaging = self.get(packaging_id, lock=True)
        updates = data.model_dump(exclude_unset=True)
        missing = [field for field, value in updates.items() if value is None]
        if missing:
            raise AppError(
                "Champs obligatoires manquants", code="validation_error", extra={"fields": missing}
            )
        if SALE_PRICE in updates:
            updates[SALE_PRICE] = updates[SALE_PRICE].quantize(CENT)
        updates = {k: v for k, v in updates.items() if getattr(packaging, k) != v}
        if SALE_PRICE in updates:
            _ensure_price_allowed(self.ctx)
        if any(field != SALE_PRICE for field in updates):
            self._require_general()
        article = self._article(packaging.article_id)
        if "conversion" in updates:
            if packagings_in_use(self.db, {packaging.id}):
                raise ConflictError(
                    "Ce conditionnement figure déjà sur une vente : sa conversion ne peut plus "
                    "changer. Désactivez-le et créez un nouveau conditionnement.",
                    code="packaging_in_use",
                )
            self._ensure_whole(article, updates["conversion"])
        before = {key: getattr(packaging, key) for key in updates}
        for key, value in updates.items():
            setattr(packaging, key, value)
        _flush(self.db)
        diff = changes(before, updates)
        if diff:
            self._audit("updated", packaging, article, diff)
        self.db.refresh(packaging)
        return packaging

    def set_active(self, packaging_id: uuid.UUID, active: bool) -> Packaging:
        self._require_general()
        packaging = self.get(packaging_id, lock=True)
        if packaging.is_active == active:
            return packaging
        article = self._article(packaging.article_id)
        if active:
            self._ensure_whole(article, packaging.conversion)
        packaging.is_active = active
        _flush(self.db)
        self._audit("activated" if active else "deactivated", packaging, article, {})
        return packaging


def _price_change(action: str, value: Any) -> tuple[Any, Any]:
    """(avant, après) d'un prix dans une entrée d'audit : à la création, valeur initiale ;
    à la modification, différence ``{"before", "after"}`` du journal."""
    if value is None:
        return None, None
    if action == "article.created":
        return None, value
    return value.get("before"), value.get("after")
