import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.stock.level_service import LevelState
from app.modules.stock.models import DocumentStatus, EntryKind, MovementType
from app.shared.schemas import Money, PositiveQuantity, Quantity, SignedQuantity, UnitCost
from app.shared.text import Optional100, Optional150, Optional500, Required150

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
    article_id: uuid.UUID
    quantity: PositiveQuantity
    unit_cost: Money


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


class ExitLineInput(BaseModel):
    article_id: uuid.UUID
    quantity: PositiveQuantity


class ExitInput(BaseModel):
    operation_date: date | None = None
    reason_id: uuid.UUID
    beneficiary: Optional150 = None
    reference: Optional100 = None
    comment: Optional500 = None
    lines: list[ExitLineInput] = Field(default_factory=list, max_length=500)


class ExitCreate(ExitInput):
    site_id: uuid.UUID | None = None


class LineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    quantity: Quantity
    unit_cost: UnitCost | None
    amount: Money | None


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


class ThresholdInput(BaseModel):
    """Surcharges du site ; ``null`` = seuil par défaut de l'article."""

    min_stock: Quantity | None = None
    max_stock: Quantity | None = None
