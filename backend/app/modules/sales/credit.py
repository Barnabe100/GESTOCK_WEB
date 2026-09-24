"""Soldes des ventes validées, créances ouvertes et exposition crédit (Phase 2.8, ADR-0021).

Aucune table de créances : les ventes et les paiements sont l'unique source de vérité.

```text
payé   = somme des paiements EFFECTUÉS (COMPLETED) de la vente
reste  = total de la vente − payé
créance ouverte = vente VALIDÉE et reste > 0
exposition d'un client = somme des restes de ses créances ouvertes (tous sites)
```

Les paiements annulés ne comptent jamais ; un brouillon ou une vente annulée n'est jamais une
créance. Même définition du « payé » que l'état d'encaissement des ventes (Phase 2.7).
"""

import uuid
from collections.abc import Collection
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.modules.sales.models import Payment, PaymentStatus, Sale, SaleStatus

ZERO = Decimal("0.00")


def balances_query() -> Any:
    """Solde de chaque vente VALIDÉE (colonnes ``sale_id``, ``number``, ``sale_date``,
    ``validated_at``, ``site_id``, ``customer_id``, ``total``, ``paid_amount``,
    ``remaining_amount``), à filtrer, trier et paginer par l'appelant.

    Montant payé : sous-requête ``LATERAL`` par vente (index ``(tenant_id, sale_id)`` des
    paiements), évaluée une fois par ligne — aucune requête par vente côté application.
    """
    paid = (
        select(func.coalesce(func.sum(Payment.amount), ZERO).label("paid"))
        .where(Payment.sale_id == Sale.id, Payment.status == PaymentStatus.COMPLETED)
        .lateral("sale_paid")
    )
    return (
        select(
            Sale.id.label("sale_id"),
            Sale.number.label("number"),
            Sale.sale_date.label("sale_date"),
            Sale.validated_at.label("validated_at"),
            Sale.site_id.label("site_id"),
            Sale.customer_id.label("customer_id"),
            Sale.total.label("total"),
            paid.c.paid.label("paid_amount"),
            (Sale.total - paid.c.paid).label("remaining_amount"),
        )
        .join(paid, true())
        .where(Sale.status == SaleStatus.VALIDATED)
    )


def open_receivables_query() -> Any:
    """Créances ouvertes : ventes validées dont le reste dû est strictement positif."""
    balances = balances_query().subquery("sale_balances")
    return select(balances).where(balances.c.remaining_amount > 0)


def customer_exposure(
    db: Session, customer_id: uuid.UUID, site_ids: Collection[uuid.UUID] | None = None
) -> tuple[Decimal, int]:
    """Exposition d'un client (somme des restes dus de ses créances ouvertes) et nombre de
    créances ouvertes. ``site_ids`` nul : tous les sites du tenant (contrôle de la limite)."""
    receivables = open_receivables_query().subquery("open_receivables")
    stmt = select(
        func.coalesce(func.sum(receivables.c.remaining_amount), ZERO), func.count()
    ).where(receivables.c.customer_id == customer_id)
    if site_ids is not None:
        stmt = stmt.where(receivables.c.site_id.in_(site_ids))
    exposure, count = db.execute(stmt).one()
    return Decimal(exposure), int(count)
