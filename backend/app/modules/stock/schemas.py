import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.stock.level_service import LevelState
from app.modules.stock.lot_service import LotState
from app.modules.stock.models import DocumentStatus, EntryKind, MovementType
from app.shared.schemas import Money, PositiveQuantity, Quantity, SignedQuantity, UnitCost
from app.shared.text import (
    Optional50,
    Optional100,
    Optional150,
    Optional500,
    Required100,
    Required150,
)

# --- Motifs de sortie ---------------------------------------------------------------------------


class ExitReasonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str | None
    label: str
    description: str | None
    is_system: bool
    is_active: bool


class ExitReasonInput(BaseModel):
    label: Required150
    description: Optional500 = None


# --- Documents ---------------------------------------------------------------------------------


class CancelInput(BaseModel):
    """Motif d'annulation obligatoire, 5 à 500 caractères (ENT-08)."""

    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def _min_length(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("le motif d'annulation doit contenir au moins 5 caractères")
        return value


class EntryLineInput(BaseModel):
    """``packaging_id`` (Lot 3-C) : conditionnement saisi ; absent = unité de base.
    ``quantity`` et ``unit_cost`` sont exprimés dans cette présentation (10 cartons à 12 000) ;
    le serveur calcule la quantité de base (240) et le coût par unité de base (500)."""

    article_id: uuid.UUID
    packaging_id: uuid.UUID | None = None
    quantity: PositiveQuantity
    unit_cost: Money
    # Lot 3-G (ADR-0045) : article suivi par lot — numéro obligatoire (sans distinction de casse,
    # espaces de bord retirés), péremption obligatoire si l'article est suivi en péremption,
    # fabrication facultative (≤ péremption). Interdits pour un article non suivi.
    lot_number: Optional50 = None
    lot_expiry_date: date | None = None
    lot_manufacturing_date: date | None = None


class EntryInput(BaseModel):
    kind: EntryKind = EntryKind.PURCHASE
    operation_date: date | None = None  # défaut : aujourd'hui (fuseau du tenant)
    supplier_id: uuid.UUID | None = None
    document_reference: Optional100 = None
    comment: Optional500 = None
    lines: list[EntryLineInput] = Field(default_factory=list, max_length=500)


class EntryCreate(EntryInput):
    # Facultatif si un site est sélectionné (X-Site-Id).
    site_id: uuid.UUID | None = None


class LotAllocationInput(BaseModel):
    """Choix manuel d'un lot (Lot 3-H-A) : ``quantity`` en UNITÉ DE BASE."""

    lot_id: uuid.UUID
    quantity: PositiveQuantity


class ExitLineInput(BaseModel):
    """``packaging_id`` (Lot 3-C) : conditionnement saisi ; absent = unité de base.
    ``lots`` (Lot 3-H-A, article suivi par lot) : répartition manuelle en unité de base,
    éventuellement incomplète dans le brouillon ; à la validation, la somme doit égaler la
    quantité de base de la ligne. Interdit pour un article non suivi."""

    article_id: uuid.UUID
    packaging_id: uuid.UUID | None = None
    quantity: PositiveQuantity
    lots: list[LotAllocationInput] = Field(default_factory=list, max_length=100)


class ExitInput(BaseModel):
    operation_date: date | None = None
    reason_id: uuid.UUID
    beneficiary: Optional150 = None
    reference: Optional100 = None
    comment: Optional500 = None
    lines: list[ExitLineInput] = Field(default_factory=list, max_length=500)


class ExitCreate(ExitInput):
    site_id: uuid.UUID | None = None


class LineLotOut(BaseModel):
    """Lot d'une ligne (Lot 3-H-A, 3-H-B1) : choix du brouillon (sortie, transfert) ou, pour un
    document validé, répartition réelle lue dans le journal des mouvements. Quantité en unité de
    base."""

    lot_id: uuid.UUID
    lot_number: str
    expiry_date: date | None = None
    state: LotState | None = None
    quantity: Quantity


class LineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    # Quantité dans la présentation saisie (Lot 3-C) ; ``base_quantity`` = unité de base.
    quantity: Quantity
    # Entrée : coût PAR PRÉSENTATION saisi ; sortie / transfert : CMUP par unité de base.
    unit_cost: UnitCost | None
    amount: Money | None
    packaging_id: uuid.UUID | None = None
    packaging_name: str | None = None
    packaging_conversion: Quantity | None = None
    base_quantity: Quantity
    # Lot 3-F (entrées, sorties) : emplacement COURANT de l'article sur le site du document,
    # à titre indicatif — jamais figé dans le document.
    location_name: str | None = None
    # Lot 3-G (entrées) : lot saisi ; ``lot_id`` résolu à la validation ; état de péremption
    # calculé (fuseau et seuil du tenant).
    lot_id: uuid.UUID | None = None
    lot_number: str | None = None
    lot_expiry_date: date | None = None
    lot_manufacturing_date: date | None = None
    lot_state: LotState | None = None
    # Lot 3-H-A (sorties), 3-H-B1 (transferts) : répartition par lot de la ligne.
    lots: list[LineLotOut] = Field(default_factory=list)


class DocumentOut(BaseModel):
    id: uuid.UUID
    number: str
    site_id: uuid.UUID
    site_name: str
    status: DocumentStatus
    operation_date: date
    comment: str | None
    total_amount: Money | None
    line_count: int
    created_at: datetime
    created_by_name: str | None
    validated_at: datetime | None
    validated_by_name: str | None
    cancelled_at: datetime | None
    cancelled_by_name: str | None
    cancellation_reason: str | None


class EntryOut(DocumentOut):
    kind: EntryKind
    supplier_id: uuid.UUID | None
    supplier_name: str | None
    document_reference: str | None
    lines: list[LineOut] = Field(default_factory=list)


class ExitOut(DocumentOut):
    reason_id: uuid.UUID
    reason_label: str
    beneficiary: str | None
    reference: str | None
    lines: list[LineOut] = Field(default_factory=list)


# --- Transferts inter-sites (Phase 2.5) -------------------------------------------------------


class TransferLineInput(BaseModel):
    """``packaging_id`` (Lot 3-C) : conditionnement saisi ; absent = unité de base.
    ``lots`` (Lot 3-H-B1, article suivi par lot) : répartition manuelle en unité de base, pris
    sur le site source et reçus sous le MÊME lot sur le site destination ; éventuellement
    incomplète dans le brouillon, somme exacte exigée à la validation. Interdit pour un article
    non suivi."""

    article_id: uuid.UUID
    packaging_id: uuid.UUID | None = None
    quantity: PositiveQuantity
    lots: list[LotAllocationInput] = Field(default_factory=list, max_length=100)


class TransferInput(BaseModel):
    """Brouillon : destination, date, commentaire et lignes (le site source est fixé à la
    création). Aucun coût : il est lu au CMUP du site source à la validation."""

    destination_site_id: uuid.UUID
    operation_date: date | None = None  # défaut : aujourd'hui (fuseau du tenant)
    comment: Optional500 = None
    lines: list[TransferLineInput] = Field(min_length=1, max_length=500)


class TransferCreate(TransferInput):
    # Facultatif si un site est sélectionné (X-Site-Id) : c'est alors lui.
    source_site_id: uuid.UUID | None = None


class TransferOut(BaseModel):
    id: uuid.UUID
    number: str
    source_site_id: uuid.UUID
    source_site_name: str
    destination_site_id: uuid.UUID
    destination_site_name: str
    status: DocumentStatus
    operation_date: date
    comment: str | None
    total_amount: Money | None  # valeur au CMUP du site source, connue après validation
    line_count: int
    created_at: datetime
    created_by_name: str | None
    validated_at: datetime | None
    validated_by_name: str | None
    cancelled_at: datetime | None
    cancelled_by_name: str | None
    cancellation_reason: str | None
    lines: list[LineOut] = Field(default_factory=list)


class MovementOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    site_id: uuid.UUID
    site_name: str
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    movement_type: MovementType
    quantity: SignedQuantity
    quantity_before: Quantity
    quantity_after: Quantity
    unit_cost: UnitCost | None
    average_cost_before: UnitCost
    average_cost_after: UnitCost
    source_type: str
    source_id: uuid.UUID
    document_number: str | None
    origin_movement_id: uuid.UUID | None
    user_name: str | None
    comment: str | None
    # Lot 3-C : présentation saisie (« 3 Carton 24 » pour −72) ; nulle en unité de base.
    packaging_name: str | None = None
    packaging_conversion: Quantity | None = None
    packaging_quantity: Quantity | None = None
    # Lot 3-G : lot du mouvement (réception, annulation de réception).
    lot_id: uuid.UUID | None = None
    lot_number: str | None = None
    lot_expiry_date: date | None = None


# --- Niveaux de stock et seuils ----------------------------------------------------------------


class LevelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    site_id: uuid.UUID
    site_name: str
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    category_name: str
    article_active: bool
    quantity: Quantity
    average_cost: UnitCost
    stock_value: Money
    min_stock: Quantity
    max_stock: Quantity | None
    min_override: Quantity | None
    max_override: Quantity | None
    state: LevelState
    # Lot 3-F : emplacement COURANT de l'article sur ce site (nul : non rangé).
    location_id: uuid.UUID | None = None
    location_name: str | None = None
    location_active: bool | None = None


class ThresholdInput(BaseModel):
    """Surcharges du site ; ``null`` = seuil par défaut de l'article."""

    min_stock: Quantity | None = None
    max_stock: Quantity | None = None


# --- Fiche fournisseur (Lot 3-E, ADR-0043) : lecture des réceptions VALIDÉES -------------------


class SupplierSummaryOut(BaseModel):
    """Synthèse des réceptions validées d'un fournisseur sur les sites visibles. ``received_total``
    (coût) : absent sans ``catalog.article.cost_view``."""

    supplier_id: uuid.UUID
    validated_count: int
    last_received_on: date | None
    received_total: Money


class SupplierArticleOut(BaseModel):
    """Article reçu d'un fournisseur (au moins une réception validée). ``last_unit_cost`` : coût
    par unité de base de la dernière réception validée — absent sans ``cost_view``."""

    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    article_active: bool
    receipt_count: int
    received_base_quantity: Quantity
    last_received_on: date
    last_entry_id: uuid.UUID
    last_entry_number: str
    last_unit_cost: UnitCost


# --- Emplacements physiques par site (Lot 3-F, ADR-0044) ----------------------------------------


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    site_id: uuid.UUID
    site_name: str
    name: str
    is_active: bool
    # Articles dont c'est l'emplacement courant sur ce site.
    article_count: int
    created_at: datetime
    updated_at: datetime


class LocationCreate(BaseModel):
    """``site_id`` facultatif si un site est sélectionné (``X-Site-Id``)."""

    site_id: uuid.UUID | None = None
    name: Required100


class LocationRename(BaseModel):
    name: Required100


class LocationAssign(BaseModel):
    """Emplacement courant de l'article sur le site ; ``null`` = non rangé."""

    location_id: uuid.UUID | None = None


# --- Lots et péremption (Lot 3-G, ADR-0045) ------------------------------------------------------


class LotOut(BaseModel):
    """Lot et son solde sur les sites visibles (ou le site filtré). Aucun coût (C1)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    number: str
    expiry_date: date | None
    manufacturing_date: date | None
    state: LotState
    quantity: Quantity
    site_count: int
    created_at: datetime


class LotBalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    site_id: uuid.UUID
    site_name: str
    quantity: Quantity


class LotDetailOut(LotOut):
    balances: list[LotBalanceOut] = Field(default_factory=list)


class StockSettingsOut(BaseModel):
    """Seuil « bientôt périmé » du tenant, en jours (défaut 30)."""

    expiry_warning_days: int


class StockSettingsInput(BaseModel):
    expiry_warning_days: int = Field(ge=0, le=365)


# --- Lots disponibles (Lot 3-H-A, H-D18) ---------------------------------------------------------


class AvailableLotOut(BaseModel):
    """Lot ayant un solde positif sur le site ; aucun coût. ``expired`` : périmé au sens de la
    vente (jamais consommé automatiquement) ; ordre = ordre de consommation du serveur."""

    lot_id: uuid.UUID
    number: str
    quantity: Quantity
    expiry_date: date | None
    manufacturing_date: date | None
    state: LotState
    expired: bool
    created_at: datetime


class AvailableLotsOut(BaseModel):
    article_id: uuid.UUID
    lot_tracked: bool
    expiry_tracked: bool
    lots: list[AvailableLotOut]
