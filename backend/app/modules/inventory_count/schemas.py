import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.inventory_count.models import InventoryStatus, InventoryType
from app.modules.stock.api import LotState
from app.shared.schemas import Money, Quantity, SignedMoney, SignedQuantity, UnitCost
from app.shared.text import Optional500

MAX_TARGETED_ARTICLES = 1000
MAX_COUNTS_PER_REQUEST = 500


class InventoryCreate(BaseModel):
    """Site (défaut : site sélectionné), type ; articles choisis pour un inventaire ciblé.
    Un inventaire complet reprend lui-même les articles gérés sur le site."""

    site_id: uuid.UUID | None = None
    inventory_type: InventoryType
    article_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_TARGETED_ARTICLES)
    comment: Optional500 = None


class InventoryUpdate(BaseModel):
    """Brouillon seulement : commentaire ; articles ajoutés / retirés d'un inventaire ciblé
    (interdits pour un inventaire complet). Les lignes conservées gardent leur instantané."""

    comment: Optional500 = None
    add_article_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_TARGETED_ARTICLES)
    remove_article_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_TARGETED_ARTICLES
    )


class CountFields(BaseModel):
    """Comptage : en unité de base (``quantity_physical`` ; ``None`` efface), OU dans un
    conditionnement (Lot 3-C) : ``packaging_quantity`` conditionnements + ``unit_quantity``
    unités de base en vrac (8 cartons + 5 bouteilles) — le serveur calcule la quantité physique
    en unité de base (8 × 24 + 5 = 197)."""

    quantity_physical: Quantity | None = None
    packaging_id: uuid.UUID | None = None
    packaging_quantity: Quantity | None = None
    unit_quantity: Quantity | None = None

    @model_validator(mode="after")
    def _one_form(self) -> "CountFields":
        if self.packaging_id is None:
            if self.packaging_quantity is not None or self.unit_quantity is not None:
                raise ValueError("packaging_quantity / unit_quantity exigent packaging_id")
        elif self.packaging_quantity is None or self.quantity_physical is not None:
            raise ValueError(
                "comptage en conditionnement : packaging_quantity obligatoire, "
                "quantity_physical calculée par le serveur"
            )
        return self


class CountInput(CountFields):
    """Comptage d'une ligne d'article NON suivi par lot."""

    line_id: uuid.UUID


class CountsInput(BaseModel):
    counts: list[CountInput] = Field(min_length=1, max_length=MAX_COUNTS_PER_REQUEST)


class LotCountInput(CountFields):
    """Comptage d'un lot d'une ligne d'article suivi par lot (Lot 3-H)."""

    lot_row_id: uuid.UUID


class LotCountsInput(BaseModel):
    """Comptage COMPLET d'une ligne suivie par lot (remplacement) : un lot attendu absent de la
    liste (ou sans quantité) compte pour 0 (O-5). Liste vide : ligne comptée, aucun lot présent."""

    counts: list[LotCountInput] = Field(default_factory=list, max_length=MAX_COUNTS_PER_REQUEST)


class DiscoveredLotInput(CountFields):
    """Lot trouvé physiquement et absent de la liste (Lot 3-H, T-4) : règles de saisie 3-G
    (numéro, péremption si l'article la suit, fabrication ≤ péremption, lot connu avec SA
    péremption) ; rattaché au lot existant de l'article s'il est connu, sinon créé à la
    validation seulement. Comptage facultatif dans la même requête."""

    lot_number: str = Field(min_length=1, max_length=50)
    expiry_date: date | None = None
    manufacturing_date: date | None = None

    @field_validator("lot_number")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("numéro de lot obligatoire")
        return value


class CancelInput(BaseModel):
    """Motif obligatoire, 5 à 500 caractères (comme les autres documents)."""

    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def _min_length(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("le motif d'annulation doit contenir au moins 5 caractères")
        return value


class LineState(StrEnum):
    """Filtre des lignes : comptage et sens de l'écart (définitif après validation, sinon
    calculé sur le stock courant)."""

    ALL = "all"
    COUNTED = "counted"
    UNCOUNTED = "uncounted"
    SURPLUS = "surplus"
    SHORTAGE = "shortage"
    NO_VARIANCE = "no_variance"


class InventorySummary(BaseModel):
    """Résumé des écarts. Avant validation : calculé sur le stock courant et le CMUP courant
    (ce que la validation appliquerait maintenant) ; après : valeurs figées."""

    lines: int
    counted: int
    surplus: int
    shortage: int
    no_variance: int
    surplus_value: Money
    shortage_value: Money
    adjustment_value: SignedMoney
    final: bool


class InventoryOut(BaseModel):
    id: uuid.UUID
    number: str
    site_id: uuid.UUID
    site_name: str
    status: InventoryStatus
    inventory_type: InventoryType
    comment: str | None
    line_count: int
    counted_count: int
    variance_count: int
    # Lot 3-H : lignes suivies par lot (mode figé au démarrage) — action « Actualiser les lots ».
    lot_tracked_count: int = 0
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None
    started_at: datetime | None
    started_by_name: str | None
    completed_at: datetime | None
    completed_by_name: str | None
    validated_at: datetime | None
    validated_by_name: str | None
    cancelled_at: datetime | None
    cancelled_by_name: str | None
    cancellation_reason: str | None
    summary: InventorySummary | None = None


class CountPackagingOut(BaseModel):
    """Conditionnement actif de l'article, proposé pour le comptage (Lot 3-C)."""

    id: uuid.UUID
    name: str
    conversion: Quantity


class InventoryLotOut(BaseModel):
    """Lot d'une ligne suivie par lot (Lot 3-H). Aucun coût (C1). ``stock_current`` : solde
    COURANT du lot sur le site (avant validation) ; ``quantity_variance`` : physique (0 si non
    saisi) − solde courant, figé à la validation."""

    id: uuid.UUID
    lot_id: uuid.UUID | None
    lot_number: str
    expiry_date: date | None
    manufacturing_date: date | None
    state: LotState | None
    discovered: bool
    stock_theoretical_initial: Quantity
    stock_current: Quantity | None
    stock_theoretical_at_validation: Quantity | None
    quantity_physical: Quantity | None
    quantity_variance: SignedQuantity | None
    counted_at: datetime | None
    counted_by_name: str | None
    count_packaging_id: uuid.UUID | None = None
    count_packaging_name: str | None = None
    count_packaging_conversion: Quantity | None = None
    count_packaging_quantity: Quantity | None = None
    count_unit_quantity: Quantity | None = None


class InventoryLineOut(BaseModel):
    id: uuid.UUID
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    category_name: str
    article_active: bool
    stock_theoretical_initial: Quantity
    # Stock courant du site (avant validation seulement) : base de l'écart qui sera appliqué.
    stock_current: Quantity | None
    stock_theoretical_at_validation: Quantity | None
    quantity_physical: Quantity | None
    # Écart indicatif = physique − théorique initial (information pendant le comptage).
    indicative_variance: SignedQuantity | None
    # Écart appliqué ou applicable = physique − stock courant (figé à la validation).
    quantity_variance: SignedQuantity | None
    unit_cost: UnitCost | None
    adjustment_value: SignedMoney | None
    counted_at: datetime | None
    counted_by_name: str | None
    # Lot 3-C : présentation du comptage (nulle : saisi en unité de base) et conditionnements
    # actifs de l'article proposés à la saisie.
    count_packaging_id: uuid.UUID | None = None
    count_packaging_name: str | None = None
    count_packaging_conversion: Quantity | None = None
    count_packaging_quantity: Quantity | None = None
    count_unit_quantity: Quantity | None = None
    # Lot 3-F : emplacement COURANT de l'article sur le site (nul : non rangé), jamais figé.
    location_name: str | None = None
    packagings: list[CountPackagingOut] = Field(default_factory=list)
    # Lot 3-H : ligne suivie par lot (mode figé au démarrage) et ses lots.
    lot_tracked: bool = False
    lots: list[InventoryLotOut] = Field(default_factory=list)


class CountsOut(BaseModel):
    lines: list[InventoryLineOut]
    summary: InventorySummary


class CandidateOut(BaseModel):
    """Article proposable pour un inventaire du site : stock courant (0 si jamais géré)."""

    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    category_name: str
    stocked: bool
    quantity: Quantity
