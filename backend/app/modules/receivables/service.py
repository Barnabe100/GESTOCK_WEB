"""Créances / comptes clients (Phase 2.8, ADR-0021) : couche de consultation et de contrôle.

Aucune table : une créance ouverte est une vente VALIDÉE dont le reste dû (total − paiements
EFFECTUÉS) est strictement positif. Les calculs viennent du module Ventes (``sales.api``,
agrégations SQL) : une seule définition du « payé », aucune seconde source de vérité. Ce
service ne modifie rien (ni vente, ni paiement, ni stock).

Périmètre : les créances d'un site suivent l'accès à ses ventes (sites visibles du membre).
L'exposition consolidée d'un client (tous sites) n'est communiquée qu'à un membre qui voit tous
les sites ; c'est elle que contrôle la validation d'une vente (``SaleService``).
"""

import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.modules.customers.api import (
    CustomerCredit,
    customers_view,
    get_customer_credit,
    get_customer_refs,
)
from app.modules.receivables.schemas import (
    CreditExposureOut,
    ReceivableDetail,
    ReceivableOut,
    ReceivableSummary,
)
from app.modules.sales.api import (
    PaymentService,
    balances_query,
    customer_exposure,
    open_receivables_query,
    payment_status,
)
from app.modules.stock.api import (
    ensure_document_site,
    filter_site_ids,
    sees_all_sites,
    visible_site_ids,
)
from app.platform.context import RequestContext
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, search_filter, text_sort

ZERO = Decimal("0.00")
NOT_FOUND = "receivable_not_found"


class ReceivableStatus(StrEnum):
    """État d'encaissement d'une créance ouverte (une vente payée n'en est plus une)."""

    UNPAID = "UNPAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"


@dataclass(frozen=True)
class ReceivableFilters:
    search: str | None = None
    customer_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_amount: Decimal | None = None  # reste dû minimal
    max_amount: Decimal | None = None  # reste dû maximal
    status: ReceivableStatus | None = None


class ReceivableService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    # --- Listes et indicateurs ----------------------------------------------------------------

    def _query(self, filters: ReceivableFilters) -> tuple[Select[Any], dict[str, Any]]:
        receivables = open_receivables_query().subquery("receivables")
        customers = customers_view()
        stmt: Select[Any] = (
            select(receivables)
            .outerjoin(customers, customers.c.id == receivables.c.customer_id)
            # Sites visibles du membre, restreints au site demandé en filtre.
            .where(receivables.c.site_id.in_(filter_site_ids(self.ctx, filters.site_id)))
        )
        by_number = search_filter(filters.search, receivables.c.number)
        if by_number is not None:
            by_customer = search_filter(
                filters.search, customers.c.code, customers.c.name, customers.c.phone
            )
            stmt = stmt.where(or_(by_number, by_customer) if by_customer is not None else by_number)
        conditions = [
            receivables.c.customer_id == filters.customer_id if filters.customer_id else None,
            receivables.c.sale_date >= filters.date_from if filters.date_from else None,
            receivables.c.sale_date <= filters.date_to if filters.date_to else None,
            receivables.c.remaining_amount >= filters.min_amount
            if filters.min_amount is not None
            else None,
            receivables.c.remaining_amount <= filters.max_amount
            if filters.max_amount is not None
            else None,
        ]
        if filters.status is ReceivableStatus.UNPAID:
            conditions.append(receivables.c.paid_amount <= 0)
        elif filters.status is ReceivableStatus.PARTIALLY_PAID:
            conditions.append(receivables.c.paid_amount > 0)
        for condition in conditions:
            if condition is not None:
                stmt = stmt.where(condition)
        sortable = {
            "sale_date": receivables.c.sale_date,
            "sale_number": receivables.c.number,
            "total": receivables.c.total,
            "paid_amount": receivables.c.paid_amount,
            "remaining_amount": receivables.c.remaining_amount,
            "customer_name": text_sort(customers.c.name),
        }
        return stmt, {"sortable": sortable, "receivables": receivables}

    def search(
        self, params: PageParams, filters: ReceivableFilters
    ) -> tuple[list[ReceivableOut], int]:
        stmt, meta = self._query(filters)
        # Défaut : les plus anciennes d'abord (recouvrement).
        stmt = apply_sort(
            stmt, params.sort, meta["sortable"], "sale_date", meta["receivables"].c.sale_id
        )
        rows, total = paginate_rows(self.db, stmt, params)
        return self._to_out(rows), total

    def summary(self, filters: ReceivableFilters) -> ReceivableSummary:
        stmt, _ = self._query(filters)
        page = stmt.subquery("filtered")
        amount, count, debtors = self.db.execute(
            select(
                func.coalesce(func.sum(page.c.remaining_amount), ZERO),
                func.count(),
                func.count(page.c.customer_id.distinct()),
            )
        ).one()
        return ReceivableSummary(
            total_receivables=amount, receivables_count=count, debtor_customers_count=debtors
        )

    def customer_receivables(
        self, customer_id: uuid.UUID, params: PageParams, filters: ReceivableFilters
    ) -> tuple[list[ReceivableOut], int]:
        self._customer(customer_id)
        return self.search(params, replace(filters, customer_id=customer_id))

    # --- Détail et exposition -----------------------------------------------------------------

    def detail(self, sale_id: uuid.UUID) -> ReceivableDetail:
        """Solde d'une vente VALIDÉE et ses paiements ; brouillon, vente annulée ou d'un site
        non accessible : introuvable."""
        balances = balances_query().subquery("sale_balances")
        row = self.db.execute(select(balances).where(balances.c.sale_id == sale_id)).one_or_none()
        if row is None:
            raise NotFoundError("Créance introuvable", code=NOT_FOUND)
        ensure_document_site(self.ctx, row.site_id, NOT_FOUND)
        base = self._to_out([row])[0]
        payments = PaymentService(self.db, self.ctx, self.now).history_out(sale_id)
        return ReceivableDetail(
            **base.model_dump(), is_open=row.remaining_amount > 0, payments=payments
        )

    def credit_exposure(self, customer_id: uuid.UUID) -> CreditExposureOut:
        credit = self._customer(customer_id)
        consolidated = sees_all_sites(self.ctx)
        exposure, count = customer_exposure(
            self.db, customer_id, None if consolidated else visible_site_ids(self.ctx)
        )
        limit = credit.credit_limit
        known = limit is not None and consolidated
        return CreditExposureOut(
            customer_id=credit.id,
            customer_code=credit.code,
            customer_name=credit.name,
            customer_is_active=credit.is_active,
            credit_limit=limit,
            limit_configured=limit is not None,
            current_exposure=exposure,
            available_credit=max(limit - exposure, ZERO) if known and limit is not None else None,
            # Inconnu (nul) sans vue consolidée ; faux sans limite configurée.
            over_limit=(limit is not None and exposure > limit) if consolidated else None,
            open_receivables_count=count,
            consolidated=consolidated,
        )

    # --- Outils -------------------------------------------------------------------------------

    def _customer(self, customer_id: uuid.UUID) -> CustomerCredit:
        # Client du tenant (RLS) ; un client désactivé reste consultable (historique conservé).
        credit = get_customer_credit(self.db, customer_id)
        if credit is None:
            raise NotFoundError("Client introuvable", code="customer_not_found")
        return credit

    def _to_out(self, rows: list[Any]) -> list[ReceivableOut]:
        customers = get_customer_refs(self.db, {r.customer_id for r in rows if r.customer_id})
        site_ids = {r.site_id for r in rows}
        sites = (
            {
                row[0]: row[1]
                for row in self.db.execute(select(Site.id, Site.name).where(Site.id.in_(site_ids)))
            }
            if site_ids
            else {}
        )
        result = []
        for r in rows:
            customer = customers.get(r.customer_id) if r.customer_id else None
            result.append(
                ReceivableOut(
                    sale_id=r.sale_id,
                    sale_number=r.number,
                    sale_date=r.sale_date,
                    validated_at=r.validated_at,
                    site_id=r.site_id,
                    site_name=sites.get(r.site_id, ""),
                    customer_id=r.customer_id,
                    customer_code=customer.code if customer else None,
                    customer_name=customer.name if customer else None,
                    customer_is_active=customer.is_active if customer else None,
                    total=r.total,
                    paid_amount=r.paid_amount,
                    remaining_amount=max(r.remaining_amount, ZERO),
                    payment_status=payment_status(r.total, r.paid_amount),
                )
            )
        return result
