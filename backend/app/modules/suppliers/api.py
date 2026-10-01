"""API publique du module Fournisseurs, pour les autres modules (ils n'importent jamais ses
modèles directement — règle d'architecture 10)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.modules.suppliers.models import Supplier
from app.shared.pagination import escape_like


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


def suppliers_named(term: str | None) -> Select[tuple[uuid.UUID]] | None:
    """Sous-requête des fournisseurs dont le NOM contient ``term`` (insensible à la casse) :
    recherche des réceptions par fournisseur (Lot 3-E), sans exposer le modèle. ``None`` si la
    recherche est vide. Le filtrage par tenant reste assuré par la RLS."""
    cleaned = (term or "").strip()
    if not cleaned:
        return None
    return select(Supplier.id).where(Supplier.name.ilike(f"%{escape_like(cleaned)}%", escape="\\"))
