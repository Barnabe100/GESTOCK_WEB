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
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
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
        # Lot 3-C (ADR-0041) : comptage saisi dans un conditionnement (« 8 cartons + 5
        # bouteilles ») ; ``quantity_physical`` reste la quantité en unité de base qui sert à
        # l'écart. Instantané complet ou absent, cohérent avec la quantité physique.
        ForeignKeyConstraint(
            ["tenant_id", "count_packaging_id"],
            ["catalog_packagings.tenant_id", "catalog_packagings.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(count_packaging_id IS NULL) = (count_packaging_name IS NULL) "
            "AND (count_packaging_id IS NULL) = (count_packaging_conversion IS NULL) "
            "AND (count_packaging_id IS NULL) = (count_packaging_quantity IS NULL) "
            "AND (count_packaging_id IS NULL) = (count_unit_quantity IS NULL)",
            name="count_packaging_complete",
        ),
        CheckConstraint(
            "count_packaging_id IS NULL OR (count_packaging_conversion > 0 "
            "AND count_packaging_quantity >= 0 AND count_unit_quantity >= 0 "
            "AND quantity_physical = count_packaging_quantity * count_packaging_conversion "
            "+ count_unit_quantity)",
            name="count_packaging_consistent",
        ),
        # Lot 3-H (inventaires par lot) : cible de la FK composite des lots de la ligne.
        UniqueConstraint("tenant_id", "id", "article_id"),
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
    # Lot 3-C : présentation du comptage (nulle : saisi directement en unité de base).
    count_packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    count_packaging_name: Mapped[str | None] = mapped_column(String(50))
    count_packaging_conversion: Mapped[Decimal | None] = mapped_column(QUANTITY)
    count_packaging_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    count_unit_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # Lot 3-H : mode de suivi par lot de l'article FIGÉ au démarrage du comptage ; relu sous
    # verrou à la validation (``409 inventory_lot_mode_changed`` s'il a changé). Ligne suivie :
    # comptage par lot (``inventory_line_lots``), ``quantity_physical`` = Σ des lots.
    lot_tracked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class InventoryLineLot(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Lot d'une ligne d'inventaire d'un article suivi par lot (Lot 3-H, ADR-0045).

    - **Attendu** : lot ayant un solde non nul sur le site au démarrage (``lot_id`` connu,
      ``stock_theoretical_initial`` = son solde) ; non saisi = physique 0 (O-5).
    - **Découvert** (``discovered``) : lot trouvé physiquement, saisi pendant le comptage
      (numéro, péremption, fabrication) ; ``lot_id`` renseigné s'il existe déjà (rattaché, jamais
      dupliqué), sinon créé à la validation seulement (``resolve_lots``).
    - Validation : ``stock_theoretical_at_validation`` = solde COURANT du lot relu sous verrou,
      ``quantity_variance`` = physique − solde courant → un ``ADJUSTMENT`` par lot avec écart.
    Aucun coût par lot (C1)."""

    __tablename__ = "inventory_line_lots"
    __table_args__ = (
        # Ligne du même tenant ET du même article ; lot du même article (FK composites).
        ForeignKeyConstraint(
            ["tenant_id", "inventory_line_id", "article_id"],
            ["inventory_lines.tenant_id", "inventory_lines.id", "inventory_lines.article_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "article_id", "lot_id"],
            ["stock_lots.tenant_id", "stock_lots.article_id", "stock_lots.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "count_packaging_id"],
            ["catalog_packagings.tenant_id", "catalog_packagings.id"],
            ondelete="RESTRICT",
        ),
        # Un lot une seule fois par ligne ; un lot découvert NOUVEAU une seule fois par numéro
        # (sans distinction de casse) — index partiel dans la migration.
        UniqueConstraint("inventory_line_id", "lot_id"),
        Index(
            "uq_inventory_line_lots_new_number",
            "inventory_line_id",
            text("lower(lot_number)"),
            unique=True,
            postgresql_where=text("lot_id IS NULL"),
        ),
        CheckConstraint("lot_id IS NOT NULL OR discovered", name="expected_has_lot"),
        CheckConstraint("discovered = (lot_number IS NOT NULL)", name="discovered_has_number"),
        CheckConstraint(
            "discovered OR (expiry_date IS NULL AND manufacturing_date IS NULL)",
            name="dates_only_discovered",
        ),
        CheckConstraint(
            "manufacturing_date IS NULL OR expiry_date IS NULL "
            "OR manufacturing_date <= expiry_date",
            name="dates_ordered",
        ),
        CheckConstraint("stock_theoretical_initial >= 0", name="initial_non_negative"),
        CheckConstraint(
            "stock_theoretical_at_validation IS NULL OR stock_theoretical_at_validation >= 0",
            name="at_validation_non_negative",
        ),
        CheckConstraint(
            "quantity_physical IS NULL OR quantity_physical >= 0", name="physical_non_negative"
        ),
        CheckConstraint(
            "quantity_variance IS NULL OR (stock_theoretical_at_validation IS NOT NULL "
            "AND quantity_physical IS NOT NULL "
            "AND quantity_variance = quantity_physical - stock_theoretical_at_validation)",
            name="variance_consistent",
        ),
        CheckConstraint(
            "(count_packaging_id IS NULL) = (count_packaging_name IS NULL) "
            "AND (count_packaging_id IS NULL) = (count_packaging_conversion IS NULL) "
            "AND (count_packaging_id IS NULL) = (count_packaging_quantity IS NULL) "
            "AND (count_packaging_id IS NULL) = (count_unit_quantity IS NULL)",
            name="count_packaging_complete",
        ),
        CheckConstraint(
            "count_packaging_id IS NULL OR (count_packaging_conversion > 0 "
            "AND count_packaging_quantity >= 0 AND count_unit_quantity >= 0 "
            "AND quantity_physical = count_packaging_quantity * count_packaging_conversion "
            "+ count_unit_quantity)",
            name="count_packaging_consistent",
        ),
    )

    inventory_line_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    discovered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Lot découvert : saisie (règles 3-G) ; nuls pour un lot attendu (lu dans le référentiel).
    lot_number: Mapped[str | None] = mapped_column(String(50))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    manufacturing_date: Mapped[date | None] = mapped_column(Date)
    stock_theoretical_initial: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    stock_theoretical_at_validation: Mapped[Decimal | None] = mapped_column(QUANTITY)
    quantity_physical: Mapped[Decimal | None] = mapped_column(QUANTITY)
    quantity_variance: Mapped[Decimal | None] = mapped_column(QUANTITY)
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    count_packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    count_packaging_name: Mapped[str | None] = mapped_column(String(50))
    count_packaging_conversion: Mapped[Decimal | None] = mapped_column(QUANTITY)
    count_packaging_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    count_unit_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
