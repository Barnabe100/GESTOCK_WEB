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


class StatusFilter(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ALL = "all"
