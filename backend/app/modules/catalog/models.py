import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 3)


class Category(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "catalog_categories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # CAT-02 : nom unique par tenant, insensible à la casse.
        Index(
            "uq_catalog_categories_tenant_name", "tenant_id", func.lower(text("name")), unique=True
        ),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Article(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Article du catalogue. Ni stock ni CMUP ici (ART-11) : ils sont tenus par site."""

    __tablename__ = "catalog_articles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # ART-01 : référence unique par tenant, insensible à la casse.
        Index(
            "uq_catalog_articles_tenant_reference",
            "tenant_id",
            func.lower(text("reference")),
            unique=True,
        ),
        # ART-09 : code-barres unique parmi les articles ACTIFS du tenant.
        Index(
            "uq_catalog_articles_tenant_barcode_active",
            "tenant_id",
            "barcode",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            ["catalog_categories.tenant_id", "catalog_categories.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "main_supplier_id"],
            ["suppliers.tenant_id", "suppliers.id"],
            ondelete="RESTRICT",
        ),
        # ART-06 / ART-07
        CheckConstraint("purchase_price >= 0", name="purchase_price_positive"),
        CheckConstraint("sale_price >= 0", name="sale_price_positive"),
        CheckConstraint("min_stock >= 0", name="min_stock_positive"),
        CheckConstraint("max_stock IS NULL OR max_stock >= min_stock", name="max_stock_gte_min"),
    )

    reference: Mapped[str] = mapped_column(String(50), nullable=False)
    designation: Mapped[str] = mapped_column(String(255), nullable=False)
    category_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    main_supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    purchase_price: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"), nullable=False)
    min_stock: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"), nullable=False)
    max_stock: Mapped[Decimal | None] = mapped_column(QUANTITY)
    description: Mapped[str | None] = mapped_column(String(1000))
    barcode: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Lot 3-A (ADR-0039) : ``False`` = article / service vendu sans stock (aucun mouvement,
    # aucun contrôle de disponibilité, hors niveaux, seuils, alertes et inventaires).
    stock_managed: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    # Lot 3-B (ADR-0040) : ``False`` = quantités vendues entières seulement (pièce, carton…) ;
    # ``True`` = quantités décimales (kg, m, L). Contrôlé par le serveur à chaque vente.
    decimal_quantity_allowed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    category: Mapped[Category] = relationship(
        lazy="joined",
        primaryjoin="and_(Article.tenant_id == Category.tenant_id, "
        "foreign(Article.category_id) == Category.id)",
        viewonly=True,
    )


class Packaging(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Conditionnement de vente d'un article (Lot 3-B, ADR-0040) : nom libre, conversion vers
    l'unité de base (quantité de base = quantité × conversion), prix de vente propre. Jamais
    supprimé : désactivé. La conversion d'un conditionnement déjà utilisé par une vente est
    figée (nouveau conditionnement pour une autre conversion)."""

    __tablename__ = "catalog_packagings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Lot 3-D : cible des codes-barres d'un conditionnement (même article garanti).
        UniqueConstraint("tenant_id", "article_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            ondelete="RESTRICT",
        ),
        # Nom unique parmi les conditionnements ACTIFS de l'article (insensible à la casse).
        Index(
            "uq_catalog_packagings_article_name_active",
            "tenant_id",
            "article_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("is_active"),
        ),
        CheckConstraint("conversion > 0", name="conversion_positive"),
        CheckConstraint("sale_price >= 0", name="sale_price_positive"),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    conversion: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    # Lot 3-B (validation) : ``NULL`` = prix NON CONFIGURÉ (conditionnement créé sans
    # ``catalog.article.price_update``) — invendable tant qu'un habilité ne l'a pas fixé ;
    # ``0`` = prix réellement configuré à zéro.
    sale_price: Mapped[Decimal | None] = mapped_column(MONEY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class BarcodeKind:
    """Nature d'un code-barres (Lot 3-D, ADR-0042)."""

    PRIMARY = "PRIMARY"  # miroir de ``catalog_articles.barcode`` (tenu par déclencheur)
    ADDITIONAL = "ADDITIONAL"  # code supplémentaire de l'article (unité de base)
    PACKAGING = "PACKAGING"  # code d'un conditionnement (article + conditionnement)


class Barcode(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Code-barres identifiant UNE présentation d'un article (Lot 3-D, ADR-0042) : l'article en
    unité de base (code principal ou supplémentaire) ou un conditionnement de cet article.

    Registre unique des codes du tenant : un code ne désigne qu'une présentation parmi les
    éléments ACTIFS (index unique partiel) ; un élément désactivé libère ses codes, qui restent
    enregistrés. ``is_active`` (tenu par déclencheurs) : article actif pour ses codes en unité de
    base ; article ET conditionnement actifs pour un code de conditionnement (un article inactif
    libère aussi les codes de ses conditionnements — validation du Lot 3-D, migration 0031). Le
    code principal reste ``catalog_articles.barcode`` (miroir)."""

    __tablename__ = "catalog_barcodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "article_id", "packaging_id"],
            [
                "catalog_packagings.tenant_id",
                "catalog_packagings.article_id",
                "catalog_packagings.id",
            ],
            ondelete="RESTRICT",
        ),
        # Unicité commune au tenant parmi les présentations actives.
        Index(
            "uq_catalog_barcodes_tenant_code_active",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        # Un seul code principal par article.
        Index(
            "uq_catalog_barcodes_article_primary",
            "tenant_id",
            "article_id",
            unique=True,
            postgresql_where=text("kind = 'PRIMARY'"),
        ),
        CheckConstraint("kind IN ('PRIMARY', 'ADDITIONAL', 'PACKAGING')", name="kind_valid"),
        CheckConstraint(
            "(kind = 'PACKAGING') = (packaging_id IS NOT NULL)", name="packaging_consistent"
        ),
        CheckConstraint("code = btrim(code) AND code <> ''", name="code_not_blank"),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
