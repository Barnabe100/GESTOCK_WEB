import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.modules.suppliers.models import Supplier
from app.modules.suppliers.schemas import SupplierCreate, SupplierUpdate
from app.platform.audit.service import audit_action, changes
from app.platform.context import RequestContext
from app.shared.pagination import PageParams, apply_sort, paginate, search_filter, text_sort
from app.shared.schemas import StatusFilter

SORTABLE = {
    "name": text_sort(Supplier.name),
    "city": text_sort(Supplier.city),
    "created_at": Supplier.created_at,
}


class SupplierService:
    """Règles SUP-01 à SUP-06. Ne valide pas la transaction (ADR-0008)."""

    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def search(
        self, params: PageParams, search: str | None, status: StatusFilter
    ) -> tuple[list[Supplier], int]:
        stmt = select(Supplier)
        condition = search_filter(
            search,
            Supplier.name,
            Supplier.contact_name,
            Supplier.city,
            Supplier.email,
            Supplier.phone,
        )
        if condition is not None:
            stmt = stmt.where(condition)
        if status is not StatusFilter.ALL:
            stmt = stmt.where(Supplier.is_active.is_(status is StatusFilter.ACTIVE))
        stmt = apply_sort(stmt, params.sort, SORTABLE, "name", Supplier.id)
        return paginate(self.db, stmt, params)

    def get(self, supplier_id: uuid.UUID) -> Supplier:
        supplier = self.db.get(Supplier, supplier_id)
        if supplier is None:
            raise NotFoundError("Fournisseur introuvable", code="supplier_not_found")
        return supplier

    def create(self, data: SupplierCreate) -> Supplier:
        supplier = Supplier(tenant_id=self.ctx.tenant_id, **data.model_dump())
        self.db.add(supplier)
        self.db.flush()
        audit_action(
            self.db,
            self.ctx,
            "supplier.created",
            entity_type="supplier",
            entity_id=supplier.id,
            data={"name": supplier.name},
        )
        return supplier

    def update(self, supplier_id: uuid.UUID, data: SupplierUpdate) -> Supplier:
        supplier = self.get(supplier_id)
        updates = data.model_dump(exclude_unset=True)
        if "name" in updates and updates["name"] is None:
            raise AppError("Le nom est obligatoire", code="validation_error")
        before = {key: getattr(supplier, key) for key in updates}
        for key, value in updates.items():
            setattr(supplier, key, value)
        self.db.flush()
        diff = changes(before, updates)
        if diff:
            audit_action(
                self.db,
                self.ctx,
                "supplier.updated",
                entity_type="supplier",
                entity_id=supplier.id,
                data=diff,
            )
        return supplier

    def set_active(self, supplier_id: uuid.UUID, active: bool) -> Supplier:
        supplier = self.get(supplier_id)
        if supplier.is_active != active:
            supplier.is_active = active
            audit_action(
                self.db,
                self.ctx,
                "supplier.activated" if active else "supplier.deactivated",
                entity_type="supplier",
                entity_id=supplier.id,
                data={"name": supplier.name},
            )
        return supplier
