"""API publique du module Clients pour les futurs modules (ventes, créances, POS…), qui
n'importent jamais ses modèles directement (règle d'architecture 10)."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

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


@dataclass(frozen=True)
class CustomerCredit:
    """Données de crédit d'un client (contrôle de la limite de crédit, créances)."""

    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    # Nul = limite non configurée (aucune limite explicite), et non « crédit interdit ».
    credit_limit: Decimal | None


def get_customer_credit(
    db: Session, customer_id: uuid.UUID, *, lock: bool = False
) -> CustomerCredit | None:
    """Client du tenant actif avec sa limite de crédit. ``lock`` : verrou de la ligne client
    (``FOR NO KEY UPDATE``) jusqu'à la fin de la transaction : les contrôles d'exposition d'un
    même client s'exécutent l'un après l'autre, et une modification concurrente de la limite
    attend. Ce verrou ne bloque ni la création de ventes ni les paiements du client (les
    clés étrangères ne prennent qu'un verrou ``KEY SHARE``, compatible)."""
    stmt = select(Customer).where(Customer.id == customer_id)
    if lock:
        stmt = stmt.with_for_update(key_share=True).execution_options(populate_existing=True)
    customer = db.scalars(stmt).one_or_none()
    if customer is None:
        return None
    return CustomerCredit(
        id=customer.id,
        code=customer.code,
        name=customer.name,
        is_active=customer.is_active,
        credit_limit=customer.credit_limit,
    )
