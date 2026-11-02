"""Requêtes de rapport du module Ventes (palier R10, ADR-0055), exposées par ``sales.api``.

Le module Ventes reste la SEULE source des montants : les rapports (module ``reports``)
filtrent, regroupent et agrègent ces requêtes sans jamais lire les modèles des ventes. Seules les
ventes VALIDÉES comptent (un brouillon n'est pas une vente, une vente annulée est exclue avec
tous ses mouvements). Le chiffre d'affaires est le ``total`` (remise globale déduite) ; les
montants des lignes sont AVANT remise globale (ADR-0054). Les paiements comptés sont les
paiements ``COMPLETED``.

Le tenant est assuré par la RLS (rôle applicatif) et par la condition ``tenant_id`` explicite.
"""

import uuid
from typing import Any

from sqlalchemy import func, select

from app.modules.sales.models import (
    Payment,
    PaymentStatus,
    Sale,
    SaleLine,
    SaleStatus,
)
from app.modules.sales.service import SOURCE_TYPE

#: Type de document des mouvements de stock d'une vente (``stock_movements.source_type``).
SALE_SOURCE_TYPE = SOURCE_TYPE


def report_sales_query(tenant_id: uuid.UUID) -> Any:
    """Une ligne par vente VALIDÉE : ``sale_id``, ``site_id``, ``sale_date`` (date métier,
    fuseau du tenant), ``seller_id`` (auteur), ``channel``, ``customer_id``, ``subtotal``,
    ``discount_amount``, ``total``, ``paid_amount`` (paiements effectués)."""
    paid = (
        select(func.coalesce(func.sum(Payment.amount), 0).label("paid"))
        .where(
            Payment.tenant_id == Sale.tenant_id,
            Payment.sale_id == Sale.id,
            Payment.status == PaymentStatus.COMPLETED,
        )
        .scalar_subquery()
    )
    return select(
        Sale.id.label("sale_id"),
        Sale.site_id.label("site_id"),
        Sale.sale_date.label("sale_date"),
        Sale.created_by.label("seller_id"),
        Sale.channel.label("channel"),
        Sale.customer_id.label("customer_id"),
        Sale.subtotal.label("subtotal"),
        Sale.discount_amount.label("discount_amount"),
        Sale.total.label("total"),
        paid.label("paid_amount"),
    ).where(Sale.tenant_id == tenant_id, Sale.status == SaleStatus.VALIDATED)


def report_lines_query(tenant_id: uuid.UUID) -> Any:
    """Une ligne par ligne de vente VALIDÉE : ``sale_id``, ``line_id``, ``site_id``,
    ``sale_date``, ``seller_id``, ``channel``, ``article_id``, ``base_quantity`` (unité de
    base), ``line_total`` (avant remise globale)."""
    return (
        select(
            Sale.id.label("sale_id"),
            SaleLine.id.label("line_id"),
            Sale.site_id.label("site_id"),
            Sale.sale_date.label("sale_date"),
            Sale.created_by.label("seller_id"),
            Sale.channel.label("channel"),
            SaleLine.article_id.label("article_id"),
            SaleLine.base_quantity.label("base_quantity"),
            SaleLine.line_total.label("line_total"),
        )
        .join(SaleLine, (SaleLine.tenant_id == Sale.tenant_id) & (SaleLine.sale_id == Sale.id))
        .where(Sale.tenant_id == tenant_id, Sale.status == SaleStatus.VALIDATED)
    )


def report_payments_query(tenant_id: uuid.UUID) -> Any:
    """Paiements EFFECTUÉS des ventes VALIDÉES : ``payment_id``, ``sale_id``, ``site_id``,
    ``sale_date``, ``channel``, ``method`` (type), ``method_label`` (libellé figé),
    ``amount``, ``paid_at``."""
    return (
        select(
            Payment.id.label("payment_id"),
            Sale.id.label("sale_id"),
            Sale.site_id.label("site_id"),
            Sale.sale_date.label("sale_date"),
            Sale.channel.label("channel"),
            Payment.method.label("method"),
            Payment.method_label.label("method_label"),
            Payment.amount.label("amount"),
            Payment.paid_at.label("paid_at"),
        )
        .join(Payment, (Payment.tenant_id == Sale.tenant_id) & (Payment.sale_id == Sale.id))
        .where(
            Sale.tenant_id == tenant_id,
            Sale.status == SaleStatus.VALIDATED,
            Payment.status == PaymentStatus.COMPLETED,
        )
    )
