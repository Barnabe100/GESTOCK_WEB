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
from app.modules.catalog.api import barcode_search, resolve_barcode
from app.modules.catalog.lot_flags_port import BlockerKind, lot_flags_blockers
from app.modules.catalog.lot_tracking import lot_tracking_available
from app.modules.catalog.models import Article, Barcode, BarcodeKind, Category, Packaging
from app.modules.catalog.schemas import (
    ArticleCreate,
    ArticleOut,
    ArticleUpdate,
    BarcodeOut,
    CategoryInput,
    PackagingCreate,
    PackagingOut,
    PackagingUpdate,
    PriceChangeOut,
)
from app.modules.catalog.stock_port import sites_with_lot_stock, sites_with_stock
from app.modules.catalog.usage_port import packagings_in_use
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
# Lot 3-G (ADR-0045) : réglages de suivi par lot / de péremption d'un article.
LOT_FLAGS = frozenset({"lot_tracked", "expiry_tracked"})

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
    # Lot 3-D : registre commun (code principal compris, réactivations, concurrence).
    "uq_catalog_barcodes_tenant_code_active": (
        "barcode_taken",
        "Ce code-barres est déjà utilisé par un élément actif",
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


def _ensure_lot_tracking_available() -> None:
    """Disponibilité du suivi par lot (P1-b, ADR-0045) : levée avec le Lot 3-H ; le refus ne
    s'appliquerait que si la constante du code était refermée — jamais un réglage."""
    if not lot_tracking_available():
        raise BusinessRuleError(
            "Le suivi par lot sera disponible avec la consommation des lots par les ventes, "
            "les sorties, les transferts et les inventaires",
            code="lot_tracking_unavailable",
        )


def _check_lot_flags(stock_managed: bool, lot_tracked: bool, expiry_tracked: bool) -> None:
    """Cohérence des réglages (Lot 3-G) : péremption ⇒ lot ⇒ article géré en stock."""
    if expiry_tracked and not lot_tracked:
        raise BusinessRuleError(
            "Le suivi de péremption exige le suivi par lot", code="expiry_tracking_requires_lots"
        )
    if lot_tracked and not stock_managed:
        raise BusinessRuleError(
            "Le suivi par lot exige un article géré en stock", code="lot_tracking_requires_stock"
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
            # Lot 3-D : aussi les codes supplémentaires et ceux des conditionnements.
            barcode_search(
                search,
                search_filter(
                    search, Article.reference, Article.designation, Article.barcode, Category.name
                ),
                Article.id,
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
        """ART-15 : un scan ne retombe jamais sur un article désactivé. Lot 3-D : tout code du
        registre (principal, supplémentaire, conditionnement) désigne son article."""
        match = resolve_barcode(self.db, barcode)
        if match is None:
            raise NotFoundError("Aucun article actif pour ce code-barres", code="article_not_found")
        return self.get(match.article_id)

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
                lot_tracked=a.lot_tracked,
                expiry_tracked=a.expiry_tracked,
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
        """Code principal : libre parmi TOUS les codes actifs du tenant (Lot 3-D : principaux,
        supplémentaires, conditionnements), hormis le code principal actuel de l'article."""
        if taken_codes(self.db, [barcode], exclude_primary_of=exclude_id):
            raise ConflictError(
                "Ce code-barres est déjà utilisé par un élément actif",
                code="article_barcode_taken",
                extra={"codes": [barcode]},
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
        _check_lot_flags(data.stock_managed, data.lot_tracked, data.expiry_tracked)
        if data.lot_tracked:
            _ensure_lot_tracking_available()
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
                "lot_tracked": article.lot_tracked,
                "expiry_tracked": article.expiry_tracked,
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
            "lot_tracked",
            "expiry_tracked",
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
        if (LOT_FLAGS | {"stock_managed"}) & updates.keys():
            _check_lot_flags(
                updates.get("stock_managed", article.stock_managed),
                updates.get("lot_tracked", article.lot_tracked),
                updates.get("expiry_tracked", article.expiry_tracked),
            )
        if LOT_FLAGS & updates.keys():
            self._ensure_lot_flags_change(article, updates)
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

    def _ensure_lot_flags_change(self, article: Article, updates: dict[str, Any]) -> None:
        """Lot 3-G (ADR-0045) : suivi par lot / de péremption modifiés seulement à stock nul sur
        TOUS les sites (D6) et soldes de lots tous nuls (D7) ; activation du suivi par lot
        soumise à sa disponibilité (P1-b, levée avec le Lot 3-H). Verrou exclusif de l'article
        d'abord : une réception en cours de validation (verrou partagé) se termine avant la
        vérification."""
        if updates.get("lot_tracked", article.lot_tracked) and not article.lot_tracked:
            _ensure_lot_tracking_available()
        self.db.execute(select(Article.id).where(Article.id == article.id).with_for_update()).one()
        sites = sorted(
            set(sites_with_stock(self.db, self.ctx.tenant_id, article.id))
            | set(sites_with_lot_stock(self.db, self.ctx.tenant_id, article.id))
        )
        if sites:
            raise ConflictError(
                "Le suivi par lot ou de péremption ne se modifie qu'à stock nul sur tous les sites",
                code="article_has_stock",
                extra={"sites": sites},
            )
        # Lot 3-H : aucun document ne doit devenir incohérent (inventaire ouvert au mode figé,
        # brouillon portant des lots ; à l'activation, document validé annulable sans lot).
        changing = any(
            key in updates and updates[key] != getattr(article, key) for key in LOT_FLAGS
        )
        if not changing:
            return
        enabling = bool(updates.get("lot_tracked", article.lot_tracked)) and not article.lot_tracked
        blockers = lot_flags_blockers(self.db, self.ctx.tenant_id, article.id, enabling)
        open_documents = sorted(
            {b.document for b in blockers if b.kind is BlockerKind.OPEN_DOCUMENT}
        )
        if open_documents:
            raise ConflictError(
                "Des documents en cours concernent cet article : validez-les, annulez-les ou "
                "retirez-en ses lots avant de modifier son suivi par lot ou de péremption",
                code="article_in_open_documents",
                extra={"documents": open_documents[:20], "count": len(open_documents)},
            )
        history = sorted({b.document for b in blockers if b.kind is BlockerKind.UNTRACKED_HISTORY})
        if history:
            raise ConflictError(
                "Des documents validés encore annulables ont des mouvements sans lot pour cet "
                "article : leur annulation deviendrait impossible une fois le suivi par lot "
                "activé",
                code="article_has_untracked_history",
                extra={"documents": history[:20], "count": len(history)},
            )

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
        if active:  # ART-16, Lot 3-D : ses codes (principal, supplémentaires) encore libres
            ensure_codes_free(
                self.db, article_codes(self.db, article.id, None), "article_barcode_taken"
            )
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
    ``catalog.article.price_update`` (création sans elle : prix NON CONFIGURÉ, invendable).
    Jamais supprimé.
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
        # Prix : fixé seulement avec ``price_update`` ; sinon NON CONFIGURÉ (``NULL``) et le
        # conditionnement reste invendable — jamais un prix 0 implicite (validation du lot).
        if data.sale_price is not None:
            _ensure_price_allowed(self.ctx)
        article = self._article(article_id)
        self._ensure_whole(article, data.conversion)
        packaging = Packaging(
            tenant_id=self.ctx.tenant_id,
            article_id=article.id,
            name=data.name,
            conversion=data.conversion,
            sale_price=data.sale_price.quantize(CENT) if data.sale_price is not None else None,
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
                SALE_PRICE: (
                    format(packaging.sale_price, "f") if packaging.sale_price is not None else None
                ),
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
            # Article inactif : les codes du conditionnement restent libérés (rien à vérifier).
            if article.is_active:
                ensure_codes_free(self.db, article_codes(self.db, article.id, packaging.id))
        packaging.is_active = active
        _flush(self.db)
        self._audit("activated" if active else "deactivated", packaging, article, {})
        return packaging


# --- Codes-barres (Lot 3-D, ADR-0042) ------------------------------------------------------------


def taken_codes(
    db: Session,
    codes: list[str],
    *,
    exclude_primary_of: uuid.UUID | None = None,
    exclude_owner: tuple[uuid.UUID, uuid.UUID | None] | None = None,
) -> list[str]:
    """Codes déjà portés par une présentation ACTIVE du tenant (registre commun). Exclusions :
    le code principal actuel d'un article (modification de ce code), ou les codes de l'élément
    réactivé (article : tous ses codes, conditionnements compris ; conditionnement : les
    siens)."""
    if not codes:
        return []
    stmt = select(Barcode.code).where(Barcode.code.in_(codes), Barcode.is_active.is_(True))
    if exclude_primary_of is not None:
        stmt = stmt.where(
            ~((Barcode.article_id == exclude_primary_of) & (Barcode.kind == BarcodeKind.PRIMARY))
        )
    if exclude_owner is not None:
        article_id, packaging_id = exclude_owner
        stmt = stmt.where(
            Barcode.article_id != article_id
            if packaging_id is None
            else Barcode.packaging_id.is_distinct_from(packaging_id)
        )
    return sorted(set(db.scalars(stmt)))


def article_codes(
    db: Session, article_id: uuid.UUID, packaging_id: uuid.UUID | None
) -> tuple[tuple[uuid.UUID, uuid.UUID | None], list[str]]:
    """Codes que la réactivation d'un élément rend à nouveau actifs : un conditionnement (ses
    codes), ou un article — son unité de base (principal + supplémentaires) ET ses
    conditionnements actifs, dont les codes sont libérés tant que l'article est inactif
    (validation du Lot 3-D)."""
    stmt = select(Barcode.code).where(Barcode.article_id == article_id)
    if packaging_id is None:
        active_packagings = select(Packaging.id).where(
            Packaging.article_id == article_id, Packaging.is_active.is_(True)
        )
        stmt = stmt.where(
            Barcode.packaging_id.is_(None) | Barcode.packaging_id.in_(active_packagings)
        )
    else:
        stmt = stmt.where(Barcode.packaging_id == packaging_id)
    return (article_id, packaging_id), list(db.scalars(stmt))


def ensure_codes_free(
    db: Session,
    owner_codes: tuple[tuple[uuid.UUID, uuid.UUID | None], list[str]],
    error_code: str = "barcode_taken",
) -> None:
    """Réactivation : chaque code du porteur doit être resté libre (un autre élément actif a pu
    le reprendre pendant la désactivation). L'index unique protège aussi la concurrence."""
    owner, codes = owner_codes
    taken = taken_codes(db, codes, exclude_owner=owner)
    if taken:
        raise ConflictError(
            "Un code-barres de cet élément est désormais utilisé par un autre élément actif : "
            "retirez-le avant de réactiver",
            code=error_code,
            extra={"codes": taken},
        )


class BarcodeService:
    """Codes-barres du catalogue (Lot 3-D, ADR-0042) : un code identifie UNE présentation
    (article en unité de base, ou conditionnement). Code principal : champ ``barcode`` de
    l'article (inchangé) ; codes supplémentaires et codes des conditionnements : ajoutés et
    retirés ici. Mêmes droits que l'article : consultation ``catalog.article.view``, gestion
    ``catalog.article.update`` (aucune permission nouvelle). Unicité commune au tenant parmi les
    présentations actives (registre + index unique) ; chaque ajout / retrait est audité."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def _require_update(self) -> None:
        if not self.ctx.has_permission(ARTICLE_UPDATE):
            raise ForbiddenError("Permission insuffisante", code="permission_denied")

    def search(self, article_id: uuid.UUID, params: PageParams) -> tuple[list[Barcode], int]:
        ArticleService(self.db, self.ctx).get(article_id)
        stmt = select(Barcode).where(Barcode.article_id == article_id)
        sortable = {"code": Barcode.code, "created_at": Barcode.created_at}
        stmt = apply_sort(stmt, params.sort, sortable, "created_at", Barcode.id)
        return paginate(self.db, stmt, params)

    def to_out(self, barcodes: list[Barcode]) -> list[BarcodeOut]:
        ids = {b.packaging_id for b in barcodes if b.packaging_id}
        names: dict[uuid.UUID, str] = {}
        if ids:
            rows = self.db.execute(
                select(Packaging.id, Packaging.name).where(Packaging.id.in_(ids))
            )
            names = {row.id: row.name for row in rows}
        return [
            BarcodeOut(
                id=b.id,
                article_id=b.article_id,
                packaging_id=b.packaging_id,
                packaging_name=names.get(b.packaging_id) if b.packaging_id else None,
                code=b.code,
                kind=b.kind,
                is_active=b.is_active,
                created_at=b.created_at,
            )
            for b in barcodes
        ]

    def _audit(self, action: str, barcode: Barcode, article: Article, name: str | None) -> None:
        added = action == "barcode_added"
        packaging = barcode.packaging_id is not None
        audit_action(
            self.db,
            self.ctx,
            f"{'packaging' if packaging else 'article'}.{action}",
            entity_type="packaging" if packaging else "article",
            entity_id=barcode.packaging_id if packaging else article.id,
            data={
                "reference": article.reference,
                **({"article_id": str(article.id), "name": name} if packaging else {}),
                "kind": barcode.kind,
                "barcode": {
                    "before": None if added else barcode.code,
                    "after": barcode.code if added else None,
                },
            },
        )

    def add(
        self, article_id: uuid.UUID, code: str, packaging_id: uuid.UUID | None = None
    ) -> Barcode:
        """Code supplémentaire de l'article (``packaging_id`` nul) ou code d'un conditionnement
        de cet article. Refusé s'il désigne déjà une présentation active du tenant."""
        self._require_update()
        article = ArticleService(self.db, self.ctx).get(article_id)
        packaging = None
        if packaging_id is not None:
            packaging = PackagingService(self.db, self.ctx).get(packaging_id)
            if packaging.article_id != article.id:
                raise NotFoundError("Conditionnement introuvable", code="packaging_not_found")
        # Code d'un conditionnement : réservé seulement si l'article ET le conditionnement sont
        # actifs (validation du Lot 3-D).
        active = article.is_active and (packaging is None or packaging.is_active)
        if active and taken_codes(self.db, [code]):
            raise ConflictError(
                "Ce code-barres est déjà utilisé par un élément actif",
                code="barcode_taken",
                extra={"codes": [code]},
            )
        barcode = Barcode(
            tenant_id=self.ctx.tenant_id,
            article_id=article.id,
            packaging_id=packaging_id,
            code=code,
            kind=BarcodeKind.PACKAGING if packaging is not None else BarcodeKind.ADDITIONAL,
            is_active=active,
        )
        self.db.add(barcode)
        _flush(self.db)
        self.db.refresh(barcode)
        self._audit("barcode_added", barcode, article, packaging.name if packaging else None)
        return barcode

    def remove(self, barcode_id: uuid.UUID) -> None:
        """Retire un code supplémentaire ou de conditionnement (le code principal se modifie sur
        l'article). Suppression de la ligne : l'ancienne valeur reste dans l'audit."""
        self._require_update()
        barcode = self.db.get(Barcode, barcode_id)
        if barcode is None:
            raise NotFoundError("Code-barres introuvable", code="barcode_not_found")
        if barcode.kind == BarcodeKind.PRIMARY:
            raise BusinessRuleError(
                "Le code-barres principal se modifie sur la fiche de l'article",
                code="barcode_primary",
            )
        article = ArticleService(self.db, self.ctx).get(barcode.article_id)
        name = (
            PackagingService(self.db, self.ctx).get(barcode.packaging_id).name
            if barcode.packaging_id
            else None
        )
        self._audit("barcode_removed", barcode, article, name)
        self.db.delete(barcode)
        _flush(self.db)


def _price_change(action: str, value: Any) -> tuple[Any, Any]:
    """(avant, après) d'un prix dans une entrée d'audit : à la création, valeur initiale ;
    à la modification, différence ``{"before", "after"}`` du journal."""
    if value is None:
        return None, None
    if action == "article.created":
        return None, value
    return value.get("before"), value.get("after")
