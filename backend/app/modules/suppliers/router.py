import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.suppliers.schemas import SupplierCreate, SupplierOut, SupplierUpdate
from app.modules.suppliers.service import SupplierService
from app.platform.context import DbSession, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["suppliers"])

View = Annotated[RequestContext, Depends(require_permission("suppliers.supplier.view"))]
Create = Annotated[RequestContext, Depends(require_permission("suppliers.supplier.create"))]
Update = Annotated[RequestContext, Depends(require_permission("suppliers.supplier.update"))]
Status = Annotated[RequestContext, Depends(require_permission("suppliers.supplier.status"))]
Paging = Annotated[PageParams, Depends(page_params)]


@router.get("", response_model=Page[SupplierOut])
def list_suppliers(
    ctx: View,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = StatusFilter.ALL,
) -> Page[SupplierOut]:
    items, total = SupplierService(db, ctx).search(paging, search, status_filter)
    return Page(
        items=[SupplierOut.model_validate(s) for s in items],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.post("", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(body: SupplierCreate, ctx: Create, db: DbSession) -> SupplierOut:
    supplier = SupplierService(db, ctx).create(body)
    db.commit()
    return SupplierOut.model_validate(supplier)


@router.get("/{supplier_id}", response_model=SupplierOut)
def get_supplier(supplier_id: uuid.UUID, ctx: View, db: DbSession) -> SupplierOut:
    return SupplierOut.model_validate(SupplierService(db, ctx).get(supplier_id))


@router.patch("/{supplier_id}", response_model=SupplierOut)
def update_supplier(
    supplier_id: uuid.UUID, body: SupplierUpdate, ctx: Update, db: DbSession
) -> SupplierOut:
    supplier = SupplierService(db, ctx).update(supplier_id, body)
    db.commit()
    return SupplierOut.model_validate(supplier)


@router.post("/{supplier_id}/activate", response_model=SupplierOut)
def activate_supplier(supplier_id: uuid.UUID, ctx: Status, db: DbSession) -> SupplierOut:
    supplier = SupplierService(db, ctx).set_active(supplier_id, True)
    db.commit()
    return SupplierOut.model_validate(supplier)


@router.post("/{supplier_id}/deactivate", response_model=SupplierOut)
def deactivate_supplier(supplier_id: uuid.UUID, ctx: Status, db: DbSession) -> SupplierOut:
    supplier = SupplierService(db, ctx).set_active(supplier_id, False)
    db.commit()
    return SupplierOut.model_validate(supplier)
