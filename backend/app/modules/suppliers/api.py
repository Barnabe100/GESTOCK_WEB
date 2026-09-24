"""API publique du module Fournisseurs, pour les autres modules (ils n'importent jamais ses
modèles directement — règle d'architecture 10)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.suppliers.models import Supplier


@dataclass(frozen=True)
class SupplierRef:
    id: uuid.UUID
    name: str
    is_active: bool


def get_supplier_ref(db: Session, supplier_id: uuid.UUID) -> SupplierRef | None:
    """Fournisseur du tenant actif (filtré par tenant), ou None."""
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        return None
    return SupplierRef(id=supplier.id, name=supplier.name, is_active=supplier.is_active)


def supplier_names(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = db.execute(select(Supplier.id, Supplier.name).where(Supplier.id.in_(ids)))
    return {row.id: row.name for row in rows}
