"""API publique du module Clients pour les futurs modules (ventes, créances, POS…), qui
n'importent jamais ses modèles directement (règle d'architecture 10)."""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.customers.models import Customer


@dataclass(frozen=True)
class CustomerRef:
    id: uuid.UUID
    code: str
    name: str
    # Une nouvelle opération commerciale exige un client actif.
    is_active: bool


def get_customer_ref(db: Session, customer_id: uuid.UUID) -> CustomerRef | None:
    """Client du tenant actif (RLS + filtre ORM), ou None s'il n'existe pas pour ce tenant."""
    customer = db.get(Customer, customer_id)
    if customer is None:
        return None
    return CustomerRef(
        id=customer.id, code=customer.code, name=customer.name, is_active=customer.is_active
    )
