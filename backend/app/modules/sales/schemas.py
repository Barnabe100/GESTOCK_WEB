import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.modules.sales.models import SaleStatus
from app.shared.schemas import Money, PositiveQuantity, Quantity
from app.shared.text import Optional500


class SaleLineInput(BaseModel):
    """Ni prix ni montant : le prix vient du catalogue, les montants sont calculés."""

    article_id: uuid.UUID
    quantity: PositiveQuantity


class SaleInput(BaseModel):
    sale_date: date | None = None  # défaut : aujourd'hui (fuseau du tenant)
    customer_id: uuid.UUID | None = None  # vente comptant anonyme si absent
    notes: Optional500 = None
    lines: list[SaleLineInput] = Field(min_length=1, max_length=500)


class SaleCreate(SaleInput):
    # Facultatif si un site est sélectionné (X-Site-Id).
    site_id: uuid.UUID | None = None


class SaleCancel(BaseModel):
    """Motif obligatoire, 5 à 500 caractères."""

    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def _min_length(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("le motif d'annulation doit contenir au moins 5 caractères")
        return value


class SaleLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    article_id: uuid.UUID
    article_reference: str
    article_designation: str
    unit: str
    quantity: Quantity
    unit_price: Money
    line_total: Money


class SaleOut(BaseModel):
    id: uuid.UUID
    number: str
    site_id: uuid.UUID
    site_name: str
    customer_id: uuid.UUID | None
    customer_code: str | None
    customer_name: str | None
    status: SaleStatus
    sale_date: date
    notes: str | None
    subtotal: Money
    total: Money
    line_count: int
    created_at: datetime
    updated_at: datetime
    created_by_name: str | None
    validated_at: datetime | None
    validated_by_name: str | None
    cancelled_at: datetime | None
    cancelled_by_name: str | None
    cancellation_reason: str | None
    lines: list[SaleLineOut] = Field(default_factory=list)
