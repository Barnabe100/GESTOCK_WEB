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
    sale_price: Money
    min_stock: Quantity
    max_stock: Quantity | None
    description: str | None
    barcode: str | None
    is_active: bool
    stock_managed: bool
    created_at: datetime
    updated_at: datetime
    # Coût interne (Lot 3-A) : présent SEULEMENT avec ``catalog.article.cost_view`` — sinon le
    # champ est absent de la réponse (jamais remplacé par une valeur fictive).
    purchase_price: Money | None = None


class ArticleCreate(BaseModel):
    reference: Required50
    designation: Required255
    category_id: uuid.UUID
    unit: Required20
    main_supplier_id: uuid.UUID | None = None
    # Prix : définis à la création seulement avec ``catalog.article.price_update`` (sinon 0).
    purchase_price: Money = Decimal("0")
    sale_price: Money = Decimal("0")
    min_stock: Quantity = Decimal("0")
    max_stock: Quantity | None = None
    description: Optional1000 = None
    barcode: Optional50 = None
    stock_managed: bool = True


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
    stock_managed: bool | None = None


class PriceChangeOut(BaseModel):
    """Changement de prix catalogue d'un article : UNE entrée du journal d'audit existant
    (création ou modification), aucune table dédiée (Lot 3-A). Prix de vente : ``sale_price_*``
    (nul si inchangé) ; prix d'achat (coût interne) : ``purchase_price_*``, présents SEULEMENT
    avec ``catalog.article.cost_view``."""

    id: uuid.UUID
    occurred_at: datetime
    user_name: str | None
    sale_price_before: Money | None
    sale_price_after: Money | None
    purchase_price_before: Money | None = None
    purchase_price_after: Money | None = None
