"""Empreinte des ventes sur un site (palier D) : lecture seule, tables du module seulement."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.sales.models import Payment, PaymentMethodSite, Sale, SaleStatus
from app.platform.footprint import SiteFootprint, capped_count


def sales_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    """Ventes validées ou annulées et paiements (historique, numéros émis) ; brouillons (ouverts,
    toujours compatibles : les ventes sont proposées par tous les profils)."""

    def sales(status: SaleStatus) -> int:
        return capped_count(
            session, select(Sale.id).where(Sale.site_id == site_id, Sale.status == status)
        )

    return SiteFootprint.of(
        history={
            "sales_validated": sales(SaleStatus.VALIDATED),
            "sales_cancelled": sales(SaleStatus.CANCELLED),
            "payments": capped_count(session, select(Payment.id).where(Payment.site_id == site_id)),
        },
        open={"sales_draft": sales(SaleStatus.DRAFT)},
        info={
            "payment_methods": capped_count(
                session,
                select(PaymentMethodSite.payment_method_id).where(
                    PaymentMethodSite.site_id == site_id
                ),
            )
        },
    )
