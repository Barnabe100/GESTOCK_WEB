"""Schémas de l'API des recettes (palier R8, ADR-0053). Quantités et montants en chaînes JSON.

Champs de coût (``unit_cost``, ``line_cost``, ``portion_cost``, ``batch_cost``, ``margin``,
``margin_rate``) : retirés des réponses sans ``catalog.article.cost_view``
(``RECIPE_COST_FIELDS``)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.modules.restaurant_recipes.models import MAX_LINES
from app.shared.schemas import Money, PositiveQuantity, Quantity

YIELD_MAX = Decimal("9999")

# Coût (4 décimales comme le CMUP) et taux de marge, en chaînes.
Cost = Annotated[
    Decimal,
    Field(max_digits=18, decimal_places=4),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
SignedAmount = Annotated[
    Decimal,
    Field(max_digits=18, decimal_places=2),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class RecipeLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: uuid.UUID
    # Unité de base de l'ingrédient (ADR-0053, D3).
    quantity: PositiveQuantity
    note: str | None = Field(default=None, max_length=200)

    _note = field_validator("note")(_clean)


class RecipeVersionIn(BaseModel):
    """Composition d'une version : rendement (portions produites), instructions, ingrédients."""

    model_config = ConfigDict(extra="forbid")

    yield_quantity: PositiveQuantity = Decimal("1")
    instructions: str | None = Field(default=None, max_length=4000)
    lines: list[RecipeLineIn] = Field(min_length=1, max_length=MAX_LINES)

    _instructions = field_validator("instructions")(_clean)

    @field_validator("yield_quantity")
    @classmethod
    def _yield_bounded(cls, value: Decimal) -> Decimal:
        if value > YIELD_MAX:
            raise ValueError(f"rendement au plus {YIELD_MAX}")
        return value


class RecipeCreate(RecipeVersionIn):
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=500)

    _notes = field_validator("notes")(_clean)


class RecipeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)

    _notes = field_validator("notes")(_clean)


class RecipeLineOut(BaseModel):
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    quantity: Quantity
    note: str | None
    is_active: bool
    stock_managed: bool


class RecipeVersionOut(BaseModel):
    id: uuid.UUID
    number: int
    yield_quantity: Quantity
    instructions: str | None
    created_at: datetime
    created_by_name: str | None
    lines: list[RecipeLineOut]


class RecipeOut(BaseModel):
    id: uuid.UUID
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    article_active: bool
    packaging_id: uuid.UUID | None
    packaging_name: str | None
    packaging_conversion: Quantity | None
    # Prix de vente COURANT de la présentation (catalogue) ; ``None`` : non configuré.
    sale_price: Money | None
    is_active: bool
    notes: str | None
    version_count: int
    current_version: RecipeVersionOut | None
    created_at: datetime
    updated_at: datetime


class RecipeSummaryOut(BaseModel):
    """Ligne de la liste des recettes."""

    id: uuid.UUID
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    packaging_id: uuid.UUID | None
    packaging_name: str | None
    is_active: bool
    current_number: int | None
    yield_quantity: Quantity | None
    ingredient_count: int
    updated_at: datetime


class CostLineOut(BaseModel):
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    quantity: Quantity
    unit_cost: Cost
    line_cost: Cost
    has_cost: bool


class SiteCostOut(BaseModel):
    """Coût de revient d'une version sur UN site (CMUP courant des ingrédients)."""

    site_id: uuid.UUID
    site_name: str
    yield_quantity: Quantity
    batch_cost: Cost
    portion_cost: Cost
    sale_price: Money | None
    margin: SignedAmount | None
    margin_rate: SignedAmount | None
    complete: bool
    lines: list[CostLineOut]


class AvailabilityLineOut(BaseModel):
    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    required: Quantity  # par unité vendue
    available: Quantity
    in_assortment: bool


class SiteAvailabilityOut(BaseModel):
    """Unités de la présentation réalisables avec le stock du site (indicatif, D12)."""

    site_id: uuid.UUID
    site_name: str
    recipes_enabled: bool
    units: int
    limiting: list[str]
    lines: list[AvailabilityLineOut]


class SheetLineOut(BaseModel):
    reference: str
    designation: str
    unit: str
    quantity: Quantity
    note: str | None


class SheetOut(BaseModel):
    """Fiche technique mise à l'échelle d'une quantité vendue (cuisine, D13) — aucun coût."""

    recipe_id: uuid.UUID
    version_number: int
    designation: str
    packaging_name: str | None
    quantity: Quantity
    portions: Quantity
    instructions: str | None
    lines: list[SheetLineOut]


class ProfitabilityRowOut(BaseModel):
    """Rentabilité d'un plat sur la période (palier R10, ADR-0055) : coûts RÉELS des
    ingrédients consommés ; ``ingredient_cost``, ``portion_cost``, ``margin`` et
    ``margin_rate`` masqués sans ``catalog.article.cost_view``."""

    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    sales_count: int
    portions: Quantity
    revenue: Money
    ingredient_cost: Money
    portion_cost: Money | None
    margin: SignedAmount
    margin_rate: SignedAmount | None


class ProfitabilityPage(BaseModel):
    date_from: date
    date_to: date
    items: list[ProfitabilityRowOut]
    total: int
    limit: int
    offset: int
