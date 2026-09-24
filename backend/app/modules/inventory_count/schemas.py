import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.modules.inventory_count.models import InventoryStatus, InventoryType
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


class CountInput(BaseModel):
    line_id: uuid.UUID
    # Quantité physique constatée (≥ 0) ; ``None`` efface le comptage de la ligne.
    quantity_physical: Quantity | None


class CountsInput(BaseModel):
    counts: list[CountInput] = Field(min_length=1, max_length=MAX_COUNTS_PER_REQUEST)


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
