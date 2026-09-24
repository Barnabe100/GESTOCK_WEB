import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.shared.schemas import Money, Quantity
from app.shared.text import (
    Optional50,
    Optional1000,
    Required20,
    Required50,
    Required100,
    Required255,
)


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CategoryInput(BaseModel):
    name: Required100


class ArticleOut(BaseModel):
    id: uuid.UUID
    reference: str
    designation: str
    category_id: uuid.UUID
    category_name: str
    unit: str
    main_supplier_id: uuid.UUID | None
    main_supplier_name: str | None
    purchase_price: Money
    sale_price: Money
    min_stock: Quantity
    max_stock: Quantity | None
    description: str | None
    barcode: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ArticleCreate(BaseModel):
    reference: Required50
    designation: Required255
    category_id: uuid.UUID
    unit: Required20
    main_supplier_id: uuid.UUID | None = None
    purchase_price: Money
    sale_price: Money
    min_stock: Quantity = Decimal("0")
    max_stock: Quantity | None = None
    description: Optional1000 = None
    barcode: Optional50 = None


class ArticleUpdate(BaseModel):
    """Champs absents : inchangés. Aucun champ de stock ni de coût moyen (ART-11)."""

    reference: Required50 | None = None
    designation: Required255 | None = None
    category_id: uuid.UUID | None = None
    unit: Required20 | None = None
    main_supplier_id: uuid.UUID | None = None
    purchase_price: Money | None = None
    sale_price: Money | None = None
    min_stock: Quantity | None = None
    max_stock: Quantity | None = None
    description: Optional1000 = None
    barcode: Optional50 = None
