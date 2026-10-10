"""Reçu de vente (palier POS) : ``/api/v1/sales/{sale_id}/receipt``.

Le reçu est construit par le serveur à partir de la vente PERSISTÉE (jamais du panier) : en-tête
de l'entreprise (identité documentaire du tenant, ADR-0027), site, numéro, date, caissier,
lignes telles que vendues (présentation et prix figés), total, paiements effectués, montant
reçu, monnaie rendue, reste dû, crédit. Aucun coût, aucune donnée interne.

Accès (contrôlés ici, l'interface ne fait que masquer) :

- consultation : ``sales.sale.view`` et sa portée (ses ventes, ou toutes avec ``view_all``) ;
- première impression : ``sales.sale.receipt_print`` sur le site de la vente ;
- impression suivante (réimpression) : ``sales.sale.reprint`` sur le site de la vente.

Chaque impression est journalisée (``sale.receipt_printed``, numéro d'impression) : le journal
d'audit fait foi pour distinguer impression et réimpression, sans colonne ni migration. Le
détail des paiements n'est inclus qu'avec ``sales.payment.view`` sur le site.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError
from app.modules.sales.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    Sale,
    SalePaymentStatus,
    SaleStatus,
)
from app.modules.sales.payment_service import ZERO, paid_amounts, payment_status
from app.modules.sales.scope import site_can
from app.modules.sales.service import SaleService
from app.platform.audit.models import AuditLog
from app.platform.audit.service import audit_action
from app.platform.context import DbSession, NowDep, RequestContext, require_permission
from app.platform.tenancy.identity import document_identity
from app.shared.schemas import Money, Quantity

RECEIPT_PRINT = "sales.sale.receipt_print"
REPRINT = "sales.sale.reprint"
PAYMENT_VIEW = "sales.payment.view"
PRINTED = "sale.receipt_printed"

router = APIRouter(prefix="/{sale_id}/receipt")

View = Annotated[RequestContext, Depends(require_permission("sales.sale.view"))]


class ReceiptIdentityLineOut(BaseModel):
    kind: str
    value: str


class ReceiptIssuerOut(BaseModel):
    """En-tête : identité documentaire du tenant (lignes absentes omises)."""

    name: str
    trade_name: str | None
    logo_url: str | None
    contact: list[ReceiptIdentityLineOut]
    identifiers: list[ReceiptIdentityLineOut]


class ReceiptLineOut(BaseModel):
    """Ligne telle que vendue : présentation (unité de base ou conditionnement) et prix figés."""

    designation: str
    unit: str
    quantity: Quantity
    packaging_name: str | None
    packaging_conversion: Quantity | None
    unit_price: Money
    line_total: Money
    # Prix unitaire moyen pondéré (ligne regroupant plusieurs prix figés) : le total fait foi.
    average_unit_price: bool = False


class ReceiptPaymentOut(BaseModel):
    """Paiement effectué. Espèces : ``amount`` imputé, ``amount_received`` remis,
    ``change_given`` monnaie rendue (calculée par le serveur à l'encaissement)."""

    method: PaymentMethod
    method_label: str
    amount: Money
    amount_received: Money | None
    change_given: Money | None
    paid_at: datetime


class ReceiptOut(BaseModel):
    sale_id: uuid.UUID
    number: str
    site_name: str
    issued_at: datetime
    cashier_name: str | None
    customer_name: str | None
    issuer: ReceiptIssuerOut
    lines: list[ReceiptLineOut]
    total: Money
    paid_amount: Money
    remaining_amount: Money
    payment_status: SalePaymentStatus
    # Vente (partiellement) à crédit : reste dû positif à la validation ou après.
    is_credit: bool
    # ``None`` sans ``sales.payment.view`` : ni détail, ni montant reçu, ni monnaie rendue.
    payments: list[ReceiptPaymentOut] | None
    amount_received: Money | None
    change_given: Money | None
    # Impressions déjà journalisées : 0 = la prochaine est la première impression.
    print_count: int


def _print_count(db: Session, tenant_id: uuid.UUID, sale_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.action == PRINTED,
                AuditLog.entity_type == "sale",
                AuditLog.entity_id == str(sale_id),
            )
        )
        or 0
    )


def _receipt(db: Session, ctx: RequestContext, now: datetime, sale: Sale) -> ReceiptOut:
    if sale.status is not SaleStatus.VALIDATED or sale.number is None:
        raise ConflictError(
            "Reçu disponible pour une vente validée seulement", code="receipt_unavailable"
        )
    identity = document_identity(db, ctx.tenant)
    # Même lecture que la fiche vente : lignes (présentation et prix figés), noms, site.
    out = SaleService(db, ctx, now).to_out([sale], with_lines=True)[0]
    paid = paid_amounts(db, {sale.id}).get(sale.id, ZERO)
    remaining = max(sale.total - paid, ZERO)
    payments: list[ReceiptPaymentOut] | None = None
    received: Decimal | None = None
    change: Decimal | None = None
    if site_can(ctx, sale.site_id, PAYMENT_VIEW):
        rows = list(
            db.scalars(
                select(Payment)
                .where(Payment.sale_id == sale.id, Payment.status == PaymentStatus.COMPLETED)
                .order_by(Payment.paid_at, Payment.number)
            )
        )
        payments = [
            ReceiptPaymentOut(
                method=p.method,
                method_label=p.method_label,
                amount=p.amount,
                amount_received=p.amount_received,
                change_given=p.change_given,
                paid_at=p.paid_at,
            )
            for p in rows
        ]
        # Montant reçu : espèces remises (avant monnaie) + montants des autres moyens.
        received = sum(
            (p.amount_received if p.amount_received is not None else p.amount for p in rows),
            ZERO,
        )
        change = sum((p.change_given or ZERO for p in rows), ZERO)
    return ReceiptOut(
        sale_id=sale.id,
        number=sale.number,
        site_name=out.site_name,
        issued_at=sale.validated_at or sale.created_at,
        cashier_name=out.validated_by_name or out.created_by_name,
        customer_name=out.customer_name,
        issuer=ReceiptIssuerOut(
            name=identity.name,
            trade_name=identity.trade_name,
            logo_url=identity.logo_url,
            contact=[ReceiptIdentityLineOut(kind=x.kind, value=x.value) for x in identity.contact],
            identifiers=[
                ReceiptIdentityLineOut(kind=x.kind, value=x.value) for x in identity.identifiers
            ],
        ),
        lines=[
            ReceiptLineOut(
                designation=line.article_designation,
                unit=line.unit,
                quantity=line.quantity,
                packaging_name=line.packaging_name,
                packaging_conversion=line.packaging_conversion,
                unit_price=line.unit_price,
                line_total=line.line_total,
                average_unit_price=line.average_unit_price,
            )
            for line in sorted(out.lines, key=lambda x: x.line_no)
        ],
        total=sale.total,
        paid_amount=paid,
        remaining_amount=remaining,
        payment_status=payment_status(sale.total, paid),
        is_credit=sale.is_credit or remaining > ZERO,
        payments=payments,
        amount_received=received,
        change_given=change,
        print_count=_print_count(db, ctx.tenant_id, sale.id),
    )


@router.get("", response_model=ReceiptOut)
def get_receipt(sale_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> ReceiptOut:
    """Reçu de la vente persistée (consultation : portée de ``sales.sale.view``)."""
    sale = SaleService(db, ctx, now).get(sale_id)
    return _receipt(db, ctx, now, sale)


@router.post("/print", response_model=ReceiptOut)
def print_receipt(sale_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> ReceiptOut:
    """Autorise et journalise une impression, puis renvoie le reçu à imprimer. Première
    impression : ``receipt_print`` ; suivantes : ``reprint`` (sur le site de la vente). Verrou de
    la vente : deux impressions simultanées sont numérotées l'une après l'autre."""
    sale = SaleService(db, ctx, now).get(sale_id, lock=True)
    count = _print_count(db, ctx.tenant_id, sale.id)
    required = RECEIPT_PRINT if count == 0 else REPRINT
    if not site_can(ctx, sale.site_id, required):
        raise ForbiddenError(
            "Permission insuffisante",
            code="receipt_reprint_denied" if count else "receipt_print_denied",
        )
    receipt = _receipt(db, ctx, now, sale)
    audit_action(
        db,
        ctx,
        PRINTED,
        entity_type="sale",
        entity_id=sale.id,
        data={"number": sale.number, "print_number": count + 1, "reprint": count > 0},
        site_id=sale.site_id,
    )
    db.commit()
    return receipt.model_copy(update={"print_count": count + 1})
