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
    func,
    text,
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

    category: Mapped[Category] = relationship(
        lazy="joined",
        primaryjoin="and_(Article.tenant_id == Category.tenant_id, "
        "foreign(Article.category_id) == Category.id)",
        viewonly=True,
    )
