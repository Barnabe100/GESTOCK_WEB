import uuid

from pydantic import BaseModel, Field

from app.shared.schemas import Money, Quantity


class PosPackagingOut(BaseModel):
    """Conditionnement ACTIF au prix CONFIGURÉ proposé au point de vente (Lot 3-B) ; quantité de
    base = quantité × ``conversion`` (indicatif : le serveur relit tout à l'encaissement)."""

    id: uuid.UUID
    name: str
    conversion: Quantity
    sale_price: Money


class PosArticleOut(BaseModel):
    """Article proposé au point de vente : prix du catalogue et stock du site (indicatifs : le
    serveur relit tout à l'encaissement)."""

    article_id: uuid.UUID
    reference: str
    designation: str
    unit: str
    category_name: str | None
    sale_price: Money
    quantity: Quantity
    is_active: bool
    # Lot 3-A : ``False`` = vendu sans stock (``quantity`` sans objet, toujours 0).
    stock_managed: bool
    # Lot 3-B : unité de base toujours vendable ; conditionnements actifs en plus.
    decimal_quantity_allowed: bool
    packagings: list[PosPackagingOut] = Field(default_factory=list)
