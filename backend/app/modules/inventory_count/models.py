import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum

QUANTITY = Numeric(18, 3)  # précision des quantités de stock
UNIT_COST = Numeric(18, 4)  # précision du CMUP
MONEY = Numeric(18, 2)


class InventoryStatus(StrEnum):
    DRAFT = "DRAFT"  # préparation : site, type, articles ; aucun comptage
    COUNTING = "COUNTING"  # saisie des quantités physiques
    READY_TO_VALIDATE = "READY_TO_VALIDATE"  # comptage terminé, en attente de validation
    VALIDATED = "VALIDATED"  # ajustements appliqués au stock : immuable
    CANCELLED = "CANCELLED"  # abandonné avant validation, sans effet sur le stock


class InventoryType(StrEnum):
    FULL = "FULL"  # tous les articles actifs gérés sur le site
    TARGETED = "TARGETED"  # articles choisis


class Inventory(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Inventaire d'un site. Jamais supprimé ; le stock ne change qu'à la validation."""

    __tablename__ = "inventories"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        CheckConstraint(
            "status NOT IN ('COUNTING', 'READY_TO_VALIDATE', 'VALIDATED') "
            "OR started_at IS NOT NULL",
            name="started_has_date",
        ),
        CheckConstraint(
            "status NOT IN ('READY_TO_VALIDATE', 'VALIDATED') OR completed_at IS NOT NULL",
            name="completed_has_date",
        ),
        CheckConstraint(
            "status <> 'VALIDATED' OR validated_at IS NOT NULL", name="validated_has_date"
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR (cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="cancelled_has_reason",
        ),
        Index("ix_inventories_tenant_created", "tenant_id", "created_at"),
    )

    number: Mapped[str] = mapped_column(String(20), nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    status: Mapped[InventoryStatus] = mapped_column(
        str_enum(InventoryStatus, "inventory_status"),
        default=InventoryStatus.DRAFT,
        nullable=False,
    )
    inventory_type: Mapped[InventoryType] = mapped_column(
        str_enum(InventoryType, "inventory_type"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))


class InventoryLine(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Article d'un inventaire : stock théorique initial (information), quantité physique
    comptée, puis, à la validation, stock courant relu, écart et valeur de l'ajustement."""

    __tablename__ = "inventory_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "inventory_id"],
            ["inventories.tenant_id", "inventories.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("inventory_id", "article_id"),
        CheckConstraint("stock_theoretical_initial >= 0", name="initial_non_negative"),
        CheckConstraint(
            "stock_theoretical_at_validation IS NULL OR stock_theoretical_at_validation >= 0",
            name="at_validation_non_negative",
        ),
        CheckConstraint(
            "quantity_physical IS NULL OR quantity_physical >= 0", name="physical_non_negative"
        ),
        CheckConstraint(
            "quantity_physical IS NULL OR counted_at IS NOT NULL", name="counted_has_date"
        ),
        # Écart figé à la validation = quantité physique − stock courant relu.
        CheckConstraint(
            "quantity_variance IS NULL OR (stock_theoretical_at_validation IS NOT NULL "
            "AND quantity_physical IS NOT NULL "
            "AND quantity_variance = quantity_physical - stock_theoretical_at_validation)",
            name="variance_consistent",
        ),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_non_negative"),
    )

    inventory_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    stock_theoretical_initial: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    stock_theoretical_at_validation: Mapped[Decimal | None] = mapped_column(QUANTITY)
    quantity_physical: Mapped[Decimal | None] = mapped_column(QUANTITY)
    quantity_variance: Mapped[Decimal | None] = mapped_column(QUANTITY)
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    adjustment_value: Mapped[Decimal | None] = mapped_column(MONEY)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
