"""Paiements des ventes : ``/api/v1/sales/{sale_id}/payments`` (Phase 2.7, ADR-0020)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.modules.sales.payment_service import PaymentService
from app.modules.sales.schemas import PaymentCreate, PaymentOut, SaleCancel, SalePaymentsOut
from app.platform.context import DbSession, NowDep, RequestContext, require_permission

router = APIRouter(prefix="/{sale_id}/payments")

View = Annotated[RequestContext, Depends(require_permission("sales.payment.view"))]
Create = Annotated[RequestContext, Depends(require_permission("sales.payment.create"))]
Cancel = Annotated[RequestContext, Depends(require_permission("sales.payment.cancel"))]


@router.get("", response_model=SalePaymentsOut)
def list_payments(sale_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> SalePaymentsOut:
    """Historique complet (paiements annulés compris) et solde calculé de la vente."""
    service = PaymentService(db, ctx, now)
    sale, payments = service.history(sale_id)
    return SalePaymentsOut(
        sale_id=sale.id,
        sale_status=sale.status,
        summary=service.summary(sale),
        items=service.to_out(sale, payments),
    )


@router.post("", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
def create_payment(
    sale_id: uuid.UUID,
    body: PaymentCreate,
    ctx: Create,
    db: DbSession,
    now: NowDep,
    response: Response,
) -> PaymentOut:
    service = PaymentService(db, ctx, now)
    payment, replayed = service.create(sale_id, body)
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK  # même clé d'idempotence : aucun doublon
    sale, payment = service.get(sale_id, payment.id)
    return service.to_out(sale, [payment])[0]


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(
    sale_id: uuid.UUID, payment_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep
) -> PaymentOut:
    service = PaymentService(db, ctx, now)
    sale, payment = service.get(sale_id, payment_id)
    return service.to_out(sale, [payment])[0]


@router.post("/{payment_id}/cancel", response_model=PaymentOut)
def cancel_payment(
    sale_id: uuid.UUID,
    payment_id: uuid.UUID,
    body: SaleCancel,
    ctx: Cancel,
    db: DbSession,
    now: NowDep,
) -> PaymentOut:
    service = PaymentService(db, ctx, now)
    payment = service.cancel(sale_id, payment_id, body.reason)
    db.commit()
    sale, payment = service.get(sale_id, payment.id)
    return service.to_out(sale, [payment])[0]
