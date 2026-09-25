import uuid

from pydantic import BaseModel

from app.shared.schemas import Money, Quantity


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
