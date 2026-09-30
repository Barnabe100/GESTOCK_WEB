"""Moyens de paiement configurables : ``/api/v1/payment-methods`` (Lot 1, ADR-0037)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.sales.payment_methods import PaymentMethodService
from app.modules.sales.schemas import (
    PaymentMethodCreate,
    PaymentMethodOut,
    PaymentMethodSiteUpdate,
    PaymentMethodUpdate,
)
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    require_any_permission,
    require_permission,
)

router = APIRouter(tags=["payment_methods"])

MANAGE = "sales.payment_method.manage"
View = Annotated[
    RequestContext,
    Depends(require_any_permission("sales.payment.view", "sales.payment.create", MANAGE)),
]
Manage = Annotated[RequestContext, Depends(require_permission(MANAGE))]


@router.get("", response_model=list[PaymentMethodOut])
def list_payment_methods(
    ctx: View, db: DbSession, now: NowDep, site_id: uuid.UUID | None = None
) -> list[PaymentMethodOut]:
    """Moyens configurés ; avec ``site_id`` (site accessible) : ``available`` sur ce site."""
    if site_id is not None and site_id not in ctx.capabilities.accessible_site_ids:
        raise NotFoundError("Site introuvable", code="site_not_found")
    service = PaymentMethodService(db, ctx, now)
    return service.to_out(service.all(), site_id)


@router.post("", response_model=PaymentMethodOut, status_code=status.HTTP_201_CREATED)
def create_payment_method(
    body: PaymentMethodCreate, ctx: Manage, db: DbSession, now: NowDep
) -> PaymentMethodOut:
    service = PaymentMethodService(db, ctx, now)
    method = service.create(body)
    db.commit()
    return service.to_out([method])[0]


@router.patch("/{method_id}", response_model=PaymentMethodOut)
def update_payment_method(
    method_id: uuid.UUID, body: PaymentMethodUpdate, ctx: Manage, db: DbSession, now: NowDep
) -> PaymentMethodOut:
    service = PaymentMethodService(db, ctx, now)
    method = service.update(method_id, body)
    db.commit()
    return service.to_out([method])[0]


@router.put("/{method_id}/sites/{site_id}", response_model=PaymentMethodOut)
def set_payment_method_site(
    method_id: uuid.UUID,
    site_id: uuid.UUID,
    body: PaymentMethodSiteUpdate,
    ctx: Manage,
    db: DbSession,
    now: NowDep,
) -> PaymentMethodOut:
    """Disponibilité sur un site accessible ; permission revérifiée pour CE site."""
    if site_id not in ctx.capabilities.accessible_site_ids:
        raise NotFoundError("Site introuvable", code="site_not_found")
    if not ctx.has_site_permission(site_id, MANAGE):
        raise ForbiddenError("Permission insuffisante sur ce site", code="permission_denied")
    service = PaymentMethodService(db, ctx, now)
    method = service.set_site(method_id, site_id, body.enabled)
    db.commit()
    return service.to_out([method], site_id)[0]
