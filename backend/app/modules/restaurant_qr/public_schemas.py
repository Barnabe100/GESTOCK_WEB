"""Réponses PUBLIQUES (palier R7, ADR-0052) : uniquement ce qu'il faut au client pour commander
— jamais de stock, de coût, de marge, de référence interne, d'identifiant d'article, de membre ni
de donnée d'un autre client."""

import uuid

from pydantic import BaseModel

from app.modules.restaurant_qr.models import QrPointKind
from app.shared.schemas import Money


class PublicMenuItem(BaseModel):
    id: uuid.UUID
    name: str
    packaging_name: str | None
    description: str | None
    price: Money
    # Épuisé (manuel) : visible mais non commandable.
    available: bool


class PublicMenuSection(BaseModel):
    id: uuid.UUID
    name: str
    items: list[PublicMenuItem]


class PublicPoint(BaseModel):
    kind: QrPointKind
    # Table (libellé) ou comptoir (libellé du point).
    label: str


class PublicMenuOut(BaseModel):
    establishment: str
    site_name: str
    point: PublicPoint
    currency: str
    sections: list[PublicMenuSection]
