import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.customers.models import CustomerType
from app.modules.customers.schemas import CustomerCreate, CustomerOut, CustomerUpdate
from app.modules.customers.service import CustomerService
from app.platform.context import DbSession, RequestContext, require_permission
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["customers"])

View = Annotated[RequestContext, Depends(require_permission("customers.customer.view"))]
Create = Annotated[RequestContext, Depends(require_permission("customers.customer.create"))]
Update = Annotated[RequestContext, Depends(require_permission("customers.customer.update"))]
Status = Annotated[RequestContext, Depends(require_permission("customers.customer.status"))]
Paging = Annotated[PageParams, Depends(page_params)]


@router.get("", response_model=Page[CustomerOut])
def list_customers(
    ctx: View,
    db: DbSession,
    paging: Paging,
    search: str | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = StatusFilter.ALL,
    customer_type: Annotated[CustomerType | None, Query(alias="type")] = None,
) -> Page[CustomerOut]:
    items, total = CustomerService(db, ctx).search(paging, search, status_filter, customer_type)
    return Page(
        items=[CustomerOut.model_validate(c) for c in items],
        total=total,
        limit=paging.limit,
        offset=paging.offset,
    )


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(body: CustomerCreate, ctx: Create, db: DbSession) -> CustomerOut:
    customer = CustomerService(db, ctx).create(body)
    db.commit()
    return CustomerOut.model_validate(customer)


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: uuid.UUID, ctx: View, db: DbSession) -> CustomerOut:
    return CustomerOut.model_validate(CustomerService(db, ctx).get(customer_id))


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, ctx: Update, db: DbSession
) -> CustomerOut:
    customer = CustomerService(db, ctx).update(customer_id, body)
    db.commit()
    return CustomerOut.model_validate(customer)


@router.post("/{customer_id}/activate", response_model=CustomerOut)
def activate_customer(customer_id: uuid.UUID, ctx: Status, db: DbSession) -> CustomerOut:
    customer = CustomerService(db, ctx).set_active(customer_id, True)
    db.commit()
    return CustomerOut.model_validate(customer)


@router.post("/{customer_id}/deactivate", response_model=CustomerOut)
def deactivate_customer(customer_id: uuid.UUID, ctx: Status, db: DbSession) -> CustomerOut:
    customer = CustomerService(db, ctx).set_active(customer_id, False)
    db.commit()
    return CustomerOut.model_validate(customer)
