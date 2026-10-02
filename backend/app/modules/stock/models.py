import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

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


def _packaging_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "packaging_id"],
        ["catalog_packagings.tenant_id", "catalog_packagings.id"],
        ondelete="RESTRICT",
    )


def _presentation_args(
    table: str, document_column: str, *distinct_by: Any, suffix: str = ""
) -> tuple[Any, ...]:
    """Lot 3-C (ADR-0041) : ligne d'un document de stock saisie dans une PRÉSENTATION (unité
    de base ou conditionnement de l'article). Une ligne par présentation ; instantané du
    conditionnement complet ou absent ; quantité de base = quantité × conversion (unité de
    base : 1), sans arrondi. ``distinct_by`` (Lot 3-G, entrées) : une ligne par présentation ET
    par lot."""
    return (
        _packaging_fk(),
        Index(
            f"uq_{table}_article_base{suffix}",
            document_column,
            "article_id",
            *distinct_by,
            unique=True,
            postgresql_where=text("packaging_id IS NULL"),
        ),
        Index(
            f"uq_{table}_packaging{suffix}",
            document_column,
            "packaging_id",
            *distinct_by,
            unique=True,
            postgresql_where=text("packaging_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(packaging_id IS NULL) = (packaging_name IS NULL) "
            "AND (packaging_id IS NULL) = (packaging_conversion IS NULL)",
            name="packaging_snapshot_complete",
        ),
        CheckConstraint(
            "packaging_conversion IS NULL OR packaging_conversion > 0",
            name="packaging_conversion_positive",
        ),
        CheckConstraint(
            "base_quantity = quantity * COALESCE(packaging_conversion, 1)",
            name="base_quantity_consistent",
        ),
    )


class _PresentationMixin:
    """Présentation saisie (Lot 3-C) : ``quantity`` est exprimée dans la présentation choisie
    (10 cartons) ; ``base_quantity`` (240 bouteilles) est celle qui touche le stock. Instantané
    du conditionnement figé : l'historique reste fidèle quoi qu'il advienne du conditionnement."""

    packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    packaging_name: Mapped[str | None] = mapped_column(String(50))
    packaging_conversion: Mapped[Decimal | None] = mapped_column(QUANTITY)
    base_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)


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


# --- Emplacements physiques par site (Lot 3-F, ADR-0044) ---------------------------------------


class StockLocation(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Emplacement physique d'un site (rayon, étagère, réserve…). Appartient TOUJOURS à un
    site : un même nom sur deux sites désigne deux emplacements distincts. Jamais supprimé
    (désactivation) ; un emplacement inactif n'est plus affectable."""

    __tablename__ = "stock_locations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible de la FK composite des affectations : l'emplacement d'un autre site est
        # techniquement inaffectable.
        UniqueConstraint("tenant_id", "site_id", "id"),
        _site_fk(),
        # Nom unique par site, insensible à la casse (actifs et inactifs).
        Index(
            "uq_stock_locations_site_name",
            "tenant_id",
            "site_id",
            func.lower(text("name")),
            unique=True,
        ),
        CheckConstraint("btrim(name) = name AND name <> ''", name="name_trimmed"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StockArticleLocation(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Emplacement COURANT d'un article sur un site (au plus un, facultatif). Information de
    localisation seulement : le stock reste tenu par (site, article) dans ``stock_levels`` ;
    aucune quantité par emplacement. Aucun instantané dans les documents (décision D8)."""

    __tablename__ = "stock_article_locations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "article_id"),
        _site_fk(),
        _article_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "location_id"],
            ["stock_locations.tenant_id", "stock_locations.site_id", "stock_locations.id"],
            ondelete="RESTRICT",
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    location_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)


# --- Lots (Lot 3-G, ADR-0045) -----------------------------------------------------------------


def _lot_fk() -> ForeignKeyConstraint:
    """Lot DU MÊME article (et du même tenant) : un lot d'un autre article est inaffectable."""
    return ForeignKeyConstraint(
        ["tenant_id", "article_id", "lot_id"],
        ["stock_lots.tenant_id", "stock_lots.article_id", "stock_lots.id"],
        ondelete="RESTRICT",
    )


class StockLot(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Lot d'un article (référentiel du tenant). Identité : (article, numéro sans distinction de
    casse) — le fournisseur n'en fait pas partie (D3). Créé à la validation d'une réception ;
    numéro et dates figés ensuite (D11) ; jamais supprimé. Aucun coût (D12, C1)."""

    __tablename__ = "stock_lots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible des FK composites (lignes, soldes, mouvements) : lot du même article garanti.
        UniqueConstraint("tenant_id", "article_id", "id"),
        _article_fk(),
        Index(
            "uq_stock_lots_article_number",
            "tenant_id",
            "article_id",
            func.lower(text("number")),
            unique=True,
        ),
        CheckConstraint("btrim(number) = number AND number <> ''", name="number_trimmed"),
        CheckConstraint(
            "manufacturing_date IS NULL OR expiry_date IS NULL "
            "OR manufacturing_date <= expiry_date",
            name="dates_ordered",
        ),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    manufacturing_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class StockLotLevel(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Solde d'un lot sur un site (D1, variante B) : ventilation du stock (site, article) —
    Σ soldes des lots = stock du site pour un article suivi. Modifié UNIQUEMENT par
    ``StockService``, sous les verrous du niveau (ordre global site, article, lot)."""

    __tablename__ = "stock_lot_levels"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", "lot_id"),
        _site_fk(),
        _lot_fk(),
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        Index("ix_stock_lot_levels_tenant_lot", "tenant_id", "lot_id"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    lot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("0"), nullable=False)


class StockSettings(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Réglages du module Stock pour le tenant (Lot 3-G, D16) : seuil « bientôt périmé » en
    jours. Ligne absente = valeurs par défaut."""

    __tablename__ = "stock_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id"),
        CheckConstraint(
            "expiry_warning_days >= 0 AND expiry_warning_days <= 365",
            name="expiry_warning_days_range",
        ),
    )

    expiry_warning_days: Mapped[int] = mapped_column(Integer, nullable=False)


# --- Journal des mouvements --------------------------------------------------------------------


class MovementType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    CANCELLATION = "CANCELLATION"
    TRANSFER_OUT = "TRANSFER_OUT"  # transfert inter-sites : sortie du site source (2.5)
    TRANSFER_IN = "TRANSFER_IN"  # transfert inter-sites : entrée sur le site destination (2.5)
    SALE = "SALE"  # vente validée (2.4)
    # Ajustement d'inventaire (2.6) : + excédent, − manquant, au CMUP courant (sans recalcul).
    ADJUSTMENT = "ADJUSTMENT"


class StockMovement(IdMixin, TenantScopedMixin, Base):
    """Journal immuable (STK-07) : le rôle SQL applicatif n'a que SELECT et INSERT."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        _site_fk(),
        _article_fk(),
        # Garde anti double application : un seul mouvement d'un type donné par ligne source,
        # par site (un transfert touche deux sites : son annulation en inverse un sur chacun) et
        # par lot (Lot 3-H, M1 : une ligne répartie produit un mouvement par lot). ``NULLS NOT
        # DISTINCT`` : sans lot (article non suivi), toujours un seul mouvement par ligne.
        UniqueConstraint(
            "tenant_id",
            "source_line_id",
            "movement_type",
            "site_id",
            "lot_id",
            name="uq_stock_movements_line_type_site_lot",
            postgresql_nulls_not_distinct=True,
        ),
        _packaging_fk(),
        CheckConstraint("quantity <> 0", name="quantity_not_zero"),
        CheckConstraint("quantity_after = quantity_before + quantity", name="balance"),
        # Lot 3-C : présentation de l'opération (« 3 cartons → 72 bouteilles ») ; instantané
        # complet ou absent, cohérent avec la quantité (en unité de base) du mouvement.
        CheckConstraint(
            "(packaging_id IS NULL) = (packaging_name IS NULL) "
            "AND (packaging_id IS NULL) = (packaging_conversion IS NULL) "
            "AND (packaging_id IS NULL) = (packaging_quantity IS NULL)",
            name="packaging_snapshot_complete",
        ),
        CheckConstraint(
            "packaging_quantity IS NULL OR (packaging_quantity > 0 "
            "AND abs(quantity) = packaging_quantity * packaging_conversion)",
            name="packaging_quantity_consistent",
        ),
        CheckConstraint("quantity_after >= 0", name="never_negative"),
        # Lot 3-G : lot du mouvement (réception, annulation de réception) — du même article.
        _lot_fk(),
        Index("ix_stock_movements_tenant_lot", "tenant_id", "lot_id"),
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
    # Numéro lisible du document source (ENT-…, SOR-…, VENT-…) : le journal l'affiche sans
    # connaître les modules sources. Nul pour les mouvements antérieurs (résolus par jointure).
    source_number: Mapped[str | None] = mapped_column(String(30))
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
    # Lot 3-C (ADR-0041) : présentation saisie par l'utilisateur (nulle : unité de base, ou
    # mouvement antérieur / ajustement d'inventaire) — « 3 Carton 24 » pour −72.
    packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    packaging_name: Mapped[str | None] = mapped_column(String(50))
    packaging_conversion: Mapped[Decimal | None] = mapped_column(QUANTITY)
    packaging_quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    # Lot 3-G (ADR-0045) : lot reçu (ou dont la réception est annulée) ; nul sinon.
    lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


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
        # Lot 3-E : fiche fournisseur (réceptions, synthèse, articles reçus).
        Index("ix_stock_entries_tenant_supplier", "tenant_id", "supplier_id"),
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


class StockEntryLine(_PresentationMixin, IdMixin, TenantScopedMixin, Base):
    __tablename__ = "stock_entry_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "entry_id"],
            ["stock_entries.tenant_id", "stock_entries.id"],
            ondelete="CASCADE",
        ),
        _article_fk(),
        # Lot 3-G : une ligne par présentation ET par lot (numéro sans distinction de casse).
        *_presentation_args(
            "stock_entry_lines",
            "entry_id",
            func.coalesce(func.lower(text("lot_number")), ""),
            suffix="_lot",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
        _lot_fk(),
        CheckConstraint(
            "lot_number IS NULL OR (btrim(lot_number) = lot_number AND lot_number <> '')",
            name="lot_number_trimmed",
        ),
        CheckConstraint("lot_id IS NULL OR lot_number IS NOT NULL", name="lot_has_number"),
        CheckConstraint(
            "lot_number IS NOT NULL OR (lot_expiry_date IS NULL "
            "AND lot_manufacturing_date IS NULL)",
            name="lot_dates_need_number",
        ),
        CheckConstraint(
            "lot_manufacturing_date IS NULL OR lot_expiry_date IS NULL "
            "OR lot_manufacturing_date <= lot_expiry_date",
            name="lot_dates_ordered",
        ),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Lot 3-G (ADR-0045) : lot saisi dans le brouillon (article suivi par lot) ; le lot du
    # référentiel est résolu ou créé à la validation (``lot_id``).
    lot_number: Mapped[str | None] = mapped_column(String(50))
    lot_expiry_date: Mapped[date | None] = mapped_column(Date)
    lot_manufacturing_date: Mapped[date | None] = mapped_column(Date)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


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


class StockExitLine(_PresentationMixin, IdMixin, TenantScopedMixin, Base):
    __tablename__ = "stock_exit_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "exit_id"],
            ["stock_exits.tenant_id", "stock_exits.id"],
            ondelete="CASCADE",
        ),
        _article_fk(),
        *_presentation_args("stock_exit_lines", "exit_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_non_negative"),
        # Lot 3-H-A : cible de la FK composite des choix de lots (lot du MÊME article).
        UniqueConstraint("tenant_id", "id", "article_id"),
    )

    exit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    # Figés à la validation : CMUP du site et montant (SOR-03).
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)

    lots: Mapped[list["StockExitLineLot"]] = relationship(
        cascade="all, delete-orphan", order_by="StockExitLineLot.position", lazy="selectin"
    )


class StockExitLineLot(IdMixin, TenantScopedMixin, Base):
    """Choix manuel d'un lot sur une ligne de sortie (Lot 3-H-A, H-D1, H-D8, O-4) : quantité en
    UNITÉ DE BASE. Donnée de BROUILLON — supprimée et recréée avec la ligne à chaque
    enregistrement, éventuellement incomplète, revalidée intégralement à la validation ; la
    traçabilité définitive est le journal des mouvements (un mouvement par lot, M1)."""

    __tablename__ = "stock_exit_line_lots"
    __table_args__ = (
        # Ligne du même tenant ET du même article ; lot du même article (FK composites).
        ForeignKeyConstraint(
            ["tenant_id", "exit_line_id", "article_id"],
            [
                "stock_exit_lines.tenant_id",
                "stock_exit_lines.id",
                "stock_exit_lines.article_id",
            ],
            ondelete="CASCADE",
        ),
        _lot_fk(),
        UniqueConstraint("exit_line_id", "lot_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    exit_line_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    lot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)


# --- Transferts inter-sites (Phase 2.5, fonctionnalité de plan ``stock.transfers``) -----------


class StockTransfer(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Transfert d'articles d'un site source vers un site destination du même tenant. Le stock
    ne change qu'à la validation (et à l'annulation d'un transfert validé), via ``StockService``."""

    __tablename__ = "stock_transfers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number"),
        UniqueConstraint("tenant_id", "id"),
        # FK composites : les deux sites appartiennent forcément au tenant du transfert.
        ForeignKeyConstraint(
            ["tenant_id", "source_site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "destination_site_id"],
            ["sites.tenant_id", "sites.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("source_site_id <> destination_site_id", name="distinct_sites"),
        CheckConstraint(
            "status <> 'VALIDATED' OR validated_at IS NOT NULL", name="validated_has_date"
        ),
        CheckConstraint(
            "status <> 'CANCELLED' OR (cancelled_at IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="cancelled_has_reason",
        ),
        Index("ix_stock_transfers_tenant_date", "tenant_id", "operation_date"),
    )

    number: Mapped[str] = mapped_column(String(20), nullable=False)
    source_site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    destination_site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
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

    lines: Mapped[list["StockTransferLine"]] = relationship(
        cascade="all, delete-orphan", order_by="StockTransferLine.line_no", lazy="selectin"
    )


class StockTransferLine(_PresentationMixin, IdMixin, TenantScopedMixin, Base):
    __tablename__ = "stock_transfer_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "transfer_id"],
            ["stock_transfers.tenant_id", "stock_transfers.id"],
            ondelete="CASCADE",
        ),
        _article_fk(),
        *_presentation_args("stock_transfer_lines", "transfer_id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="unit_cost_non_negative"),
    )

    transfer_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    # Figés à la validation : CMUP du site source (coût de sortie ET d'entrée) et valeur.
    unit_cost: Mapped[Decimal | None] = mapped_column(UNIT_COST)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
