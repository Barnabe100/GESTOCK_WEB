import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum
from app.shared.clock import utcnow

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 3)
UNIT_COST = Numeric(18, 4)  # CMUP et coûts unitaires valorisés (Q6)


def _site_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
    )


def _article_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "article_id"],
        ["catalog_articles.tenant_id", "catalog_articles.id"],
        ondelete="RESTRICT",
    )


# --- Niveaux de stock --------------------------------------------------------------------------


class StockLevel(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Stock, CMUP et surcharges de seuils d'un article sur un site. Modifié UNIQUEMENT par
    ``StockService`` (quantité, CMUP) et le service des seuils (surcharges)."""

    __tablename__ = "stock_levels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "article_id"),
        _site_fk(),
        _article_fk(),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        CheckConstraint("average_cost >= 0", name="average_cost_non_negative"),
        CheckConstraint("min_stock IS NULL OR min_stock >= 0", name="min_stock_non_negative"),
        CheckConstraint(
            "max_stock IS NULL OR min_stock IS NULL OR max_stock >= min_stock",
            name="max_stock_gte_min",
        ),
        CheckConstraint("max_stock IS NULL OR max_stock >= 0", name="max_stock_non_negative"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"), nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(UNIT_COST, default=Decimal("0"), nullable=False)
    # Surcharges par site (Q2) ; nulles = seuils par défaut de l'article.
    min_stock: Mapped[Decimal | None] = mapped_column(QUANTITY)
    max_stock: Mapped[Decimal | None] = mapped_column(QUANTITY)


# --- Journal des mouvements --------------------------------------------------------------------


class MovementType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    CANCELLATION = "CANCELLATION"
    # Réservés aux sous-phases suivantes (inventaire, transferts, ventes).
    ADJUSTMENT = "ADJUSTMENT"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    SALE = "SALE"


class StockMovement(IdMixin, TenantScopedMixin, Base):
    """Journal immuable (STK-07) : le rôle SQL applicatif n'a que SELECT et INSERT."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        _site_fk(),
        _article_fk(),
        # Garde anti double application : un seul mouvement d'un type donné par ligne source.
        UniqueConstraint("tenant_id", "source_line_id", "movement_type"),
        CheckConstraint("quantity <> 0", name="quantity_not_zero"),
        CheckConstraint("quantity_after = quantity_before + quantity", name="balance"),
        CheckConstraint("quantity_after >= 0", name="never_negative"),
        Index("ix_stock_movements_tenant_site_occurred", "tenant_id", "site_id", "occurred_at"),
        Index("ix_stock_movements_source", "tenant_id", "source_id"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    movement_type: Mapped[MovementType] = mapped_column(
        str_enum(MovementType, "movement_type"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)  # signée
    quantity_before: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    quantity_after: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    average_cost_before: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False)
    average_cost_after: Mapped[Decimal] = mapped_column(UNIT_COST, nullable=False)
    # Document d'origine (polymorphe : entrée, sortie, et demain vente, transfert, inventaire).
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_line_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    origin_movement_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stock_movements.id", ondelete="RESTRICT")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    comment: Mapped[str | None] = mapped_column(String(500))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


# --- Motifs de sortie --------------------------------------------------------------------------


class ExitReason(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "stock_exit_reasons"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "code"),
        Index(
            "uq_stock_exit_reasons_tenant_label",
            "tenant_id",
            func.lower(text("label")),
            unique=True,
        ),
        CheckConstraint("NOT is_system OR code IS NOT NULL", name="system_has_code"),
    )

    # Code stable des motifs système (CONSOMMATION_INTERNE…), nul pour un motif du tenant.
    code: Mapped[str | None] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --- Documents ---------------------------------------------------------------------------------


class DocumentStatus(StrEnum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    CANCELLED = "CANCELLED"


class EntryKind(StrEnum):
    PURCHASE = "PURCHASE"  # réception fournisseur
    INITIAL_STOCK = "INITIAL_STOCK"  # stock initial d'un site (Q5)


class _DocumentMixin(IdMixin, TenantScopedMixin, TimestampMixin):
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, "document_status"), default=DocumentStatus.DRAFT, nullable=False
    )
    operation_date: Mapped[date] = mapped_column(Date, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))


class StockEntry(_DocumentMixin, Base):
    __tablename__ = "stock_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "id"),
        _site_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "supplier_id"],
            ["suppliers.tenant_id", "suppliers.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind <> 'PURCHASE' OR supplier_id IS NOT NULL", name="purchase_has_supplier"
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancellation_reason IS NOT NULL", name="cancel_has_reason"
        ),
    )

    kind: Mapped[EntryKind] = mapped_column(str_enum(EntryKind, "entry_kind"), nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    document_reference: Mapped[str | None] = mapped_column(String(100))

    lines: Mapped[list["StockEntryLine"]] = relationship(
        cascade="all, delete-orphan", order_by="StockEntryLine.line_no", lazy="selectin"
    )


class StockEntryLine(IdMixin, TenantScopedMixin, Base):
    __tablename__ = "stock_entry_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "entry_id"],
            ["stock_entries.tenant_id", "stock_entries.id"],
            ondelete="CASCADE",
        ),
        _article_fk(),
        UniqueConstraint("entry_id", "article_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)


class StockExit(_DocumentMixin, Base):
    __tablename__ = "stock_exits"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "id"),
        _site_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "reason_id"],
            ["stock_exit_reasons.tenant_id", "stock_exit_reasons.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR cancellation_reason IS NOT NULL", name="cancel_has_reason"
        ),
    )

    reason_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    beneficiary: Mapped[str | None] = mapped_column(String(150))
    reference: Mapped[str | None] = mapped_column(String(100))

    lines: Mapped[list["StockExitLine"]] = relationship(
        cascade="all, delete-orphan", order_by="StockExitLine.line_no", lazy="selectin"
    )


class StockExitLine(IdMixin, TenantScopedMixin, Base):
    __tablename__ = "stock_exit_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "exit_id"],
            ["stock_exits.tenant_id", "stock_exits.id"],
            ondelete="CASCADE",
        ),
        _article_fk(),
        UniqueConstraint("exit_id", "article_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_non_negative"),
    )

    exit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    # Figés à la validation : CMUP du site et montant (SOR-03).
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
