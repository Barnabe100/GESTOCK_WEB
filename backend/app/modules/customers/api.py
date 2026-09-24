"""API publique du module Clients pour les futurs modules (ventes, créances, POS…), qui
n'importent jamais ses modèles directement (règle d'architecture 10)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import Subquery, select
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


def get_customer_refs(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, CustomerRef]:
    """Clients du tenant actif ; les identifiants inconnus (ou d'un autre tenant) sont absents."""
    if not ids:
        return {}
    return {
        c.id: CustomerRef(id=c.id, code=c.code, name=c.name, is_active=c.is_active)
        for c in db.scalars(select(Customer).where(Customer.id.in_(ids)))
    }


def customers_view() -> Subquery:
    """Colonnes publiques (recherche, jointures) ; le tenant est filtré par la RLS et par la
    condition ``tenant_id`` de l'appelant."""
    return select(
        Customer.id, Customer.tenant_id, Customer.code, Customer.name, Customer.phone
    ).subquery("customers_view")
