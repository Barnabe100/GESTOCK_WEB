from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, Field, PlainSerializer

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# Montants et quantités : Decimal, sérialisés en chaînes dans l'API (jamais de float).
Money = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=2),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
Quantity = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=3),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]

# Quantité signée (mouvements : + entrée, − sortie).
SignedQuantity = Annotated[
    Decimal,
    Field(max_digits=18, decimal_places=3),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
PositiveQuantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=18, decimal_places=3),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
# Montant strictement positif (paiements).
PositiveMoney = Annotated[
    Decimal,
    Field(gt=0, max_digits=18, decimal_places=2),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
# Montant signé (ajustements d'inventaire : + excédent, − manquant).
SignedMoney = Annotated[
    Decimal,
    Field(max_digits=18, decimal_places=2),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
UnitCost = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=4),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]


class StatusFilter(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ALL = "all"
