"""Rapports des ventes (palier R10, ADR-0055) : consultation seule, aucune table.

Sources (règle d'architecture 10, aucun modèle d'un autre module importé) :

- ventes VALIDÉES, lignes et paiements effectués : ``sales.api`` (``report_*_query``) ;
- coût des sorties de stock d'une vente : ``stock.api.outflow_costs_query`` (CMUP du site au
  moment de la sortie, ingrédients des recettes compris) ;
- libellés : ``catalog.api.articles_view``, ``customers.api.customers_view``, sites et
  utilisateurs de la plateforme.

Formules (documentées dans ``docs/architecture/REPORTS.md``) :

- chiffre d'affaires (CA) = Σ ``total`` des ventes validées (remise globale déduite) ;
- coût = Σ (−quantité × coût unitaire) des mouvements ``SALE`` de ces ventes ;
- marge = CA − coût ; taux de marge = marge / CA × 100 (nul si CA nul) ;
- panier moyen = CA / nombre de ventes ;
- par article / catégorie : montants des lignes AVANT remise globale, coût des mouvements
  DIRECTS de la ligne (un plat à recette : voir la rentabilité des recettes).

Périmètre : sites visibles du membre où il détient ``reports.report.view`` (module effectif sur
le site) ; période = dates MÉTIER des ventes (``sale_date``, fuseau du tenant, ADR-0028).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import Date, String, cast, func, literal, select
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError
from app.modules.catalog.api import articles_view
from app.modules.customers.api import customers_view
from app.modules.reports.permissions import REPORT_VIEW
from app.modules.reports.schemas import (
    BreakdownRow,
    Dimension,
    Granularity,
    PaymentMethodRow,
    SalesFigures,
    SalesSummaryOut,
    SeriesPoint,
)
from app.modules.sales.api import (
    SALE_SOURCE_TYPE,
    report_lines_query,
    report_payments_query,
    report_sales_query,
)
from app.modules.stock.api import outflow_costs_query, tenant_today
from app.platform.context import RequestContext
from app.platform.costs import COST_VIEW
from app.platform.identity.models import User
from app.platform.tenancy.models import Site
from app.shared.pagination import PageParams, apply_sort, paginate_rows, text_sort

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
#: Période maximale d'un rapport (garde-fou de charge) : deux ans et un jour.
MAX_PERIOD_DAYS = 732


def money(value: Any) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def rate(part: Decimal, whole: Decimal) -> Decimal | None:
    return money(part * 100 / whole) if whole else None


@dataclass(frozen=True)
class ReportFilters:
    date_from: date | None = None
    date_to: date | None = None
    site_id: uuid.UUID | None = None
    channel: str | None = None


@dataclass(frozen=True)
class Period:
    start: date
    end: date


class ReportService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now
        self.tenant_id = ctx.tenant.id

    # --- Périmètre --------------------------------------------------------------------------

    def site_ids(self, site_id: uuid.UUID | None) -> list[uuid.UUID]:
        """Sites lisibles : visibles du membre, restreints au filtre, avec ``REPORT_VIEW``."""
        visible = (
            {self.ctx.site.id}
            if self.ctx.site is not None
            else set(self.ctx.capabilities.accessible_site_ids)
        )
        if site_id is not None:
            visible &= {site_id}
        return sorted(s for s in visible if self.ctx.has_site_permission(s, REPORT_VIEW))

    def period(self, filters: ReportFilters) -> Period:
        """Période demandée ; par défaut le mois en cours (jour du tenant)."""
        today = tenant_today(self.ctx, self.now)
        start = filters.date_from or today.replace(day=1)
        end = filters.date_to or today
        if start > end:
            raise BusinessRuleError(
                "La date de début doit précéder la date de fin", code="report_period_invalid"
            )
        if (end - start).days + 1 > MAX_PERIOD_DAYS:
            raise BusinessRuleError(
                "Période trop longue : deux ans au plus",
                code="report_period_too_long",
                extra={"max_days": MAX_PERIOD_DAYS},
            )
        return Period(start, end)

    def costs_visible(self) -> bool:
        return COST_VIEW in self.ctx.capabilities.permissions

    def _scoped(self, query: Any, filters: ReportFilters, period: Period) -> Any:
        sub = query.subquery()
        stmt = select(sub).where(
            sub.c.site_id.in_(self.site_ids(filters.site_id)),
            sub.c.sale_date >= period.start,
            sub.c.sale_date <= period.end,
        )
        if filters.channel:
            stmt = stmt.where(sub.c.channel == filters.channel)
        return stmt.subquery()

    def _sales(self, filters: ReportFilters, period: Period) -> Any:
        """Ventes du périmètre avec leur coût (``cost``)."""
        sales = self._scoped(report_sales_query(self.tenant_id), filters, period)
        movements = outflow_costs_query(self.tenant_id, SALE_SOURCE_TYPE).subquery()
        per_sale = (
            select(movements.c.source_id, func.sum(movements.c.cost).label("cost"))
            .group_by(movements.c.source_id)
            .subquery()
        )
        return (
            select(sales, func.coalesce(per_sale.c.cost, 0).label("cost"))
            .outerjoin(per_sale, per_sale.c.source_id == sales.c.sale_id)
            .subquery("report_sales")
        )

    def _lines(self, filters: ReportFilters, period: Period) -> Any:
        """Lignes du périmètre avec le coût de leurs mouvements DIRECTS (``cost``)."""
        lines = self._scoped(report_lines_query(self.tenant_id), filters, period)
        movements = outflow_costs_query(self.tenant_id, SALE_SOURCE_TYPE).subquery()
        return (
            select(lines, func.coalesce(movements.c.cost, 0).label("cost"))
            .outerjoin(
                movements,
                (movements.c.source_id == lines.c.sale_id)
                & (movements.c.source_line_id == lines.c.line_id),
            )
            .subquery("report_lines")
        )

    # --- Synthèse ---------------------------------------------------------------------------

    def summary(self, filters: ReportFilters, granularity: Granularity) -> SalesSummaryOut:
        period = self.period(filters)
        sales = self._sales(filters, period)
        row = self.db.execute(
            select(
                func.count(sales.c.sale_id),
                func.coalesce(func.sum(sales.c.total), 0),
                func.coalesce(func.sum(sales.c.subtotal), 0),
                func.coalesce(func.sum(sales.c.discount_amount), 0),
                func.coalesce(func.sum(sales.c.paid_amount), 0),
                func.coalesce(func.sum(sales.c.cost), 0),
            )
        ).one()
        count, revenue, subtotal, discounts, paid, cost = (
            int(row[0]),
            money(row[1]),
            money(row[2]),
            money(row[3]),
            money(row[4]),
            money(row[5]),
        )
        totals = SalesFigures(
            sales_count=count,
            revenue=revenue,
            subtotal=subtotal,
            discounts=discounts,
            average_basket=money(revenue / count) if count else ZERO,
            paid=paid,
            remaining=money(revenue - paid),
            cost=cost,
            margin=revenue - cost,
            margin_rate=rate(revenue - cost, revenue),
        )
        bucket = cast(func.date_trunc(granularity.value, sales.c.sale_date), Date).label("period")
        series = {
            r.period: r
            for r in self.db.execute(
                select(
                    bucket,
                    func.count(sales.c.sale_id).label("sales_count"),
                    func.sum(sales.c.total).label("revenue"),
                    func.sum(sales.c.discount_amount).label("discounts"),
                    func.sum(sales.c.cost).label("cost"),
                )
                .group_by(bucket)
                .order_by(bucket)
            )
        }
        points = []
        for start in _buckets(period, granularity):
            found = series.get(start)
            revenue_p = money(found.revenue if found else 0)
            cost_p = money(found.cost if found else 0)
            points.append(
                SeriesPoint(
                    period=start,
                    sales_count=int(found.sales_count) if found else 0,
                    revenue=revenue_p,
                    discounts=money(found.discounts if found else 0),
                    cost=cost_p,
                    margin=revenue_p - cost_p,
                )
            )
        return SalesSummaryOut(
            date_from=period.start,
            date_to=period.end,
            granularity=granularity,
            site_ids=self.site_ids(filters.site_id),
            totals=totals,
            series=points,
        )

    # --- Répartitions -----------------------------------------------------------------------

    def breakdown(
        self, filters: ReportFilters, dimension: Dimension, params: PageParams
    ) -> tuple[list[BreakdownRow], int, Decimal]:
        """Lignes regroupées par ``dimension`` ; renvoie aussi le CA total du périmètre."""
        period = self.period(filters)
        if dimension in (Dimension.ARTICLE, Dimension.CATEGORY):
            stmt, sortable, tiebreak, total = self._by_line_dimension(filters, period, dimension)
        else:
            stmt, sortable, tiebreak, total = self._by_sale_dimension(filters, period, dimension)
        if not self.costs_visible():
            # Aucun tri qui révèlerait l'ordre des coûts sans la permission de les voir.
            sortable = {k: v for k, v in sortable.items() if k not in ("cost", "margin")}
        stmt = apply_sort(stmt, params.sort, sortable, "-revenue", tiebreak)
        rows, count = paginate_rows(self.db, stmt, params)
        return [self._row(r, dimension, total) for r in rows], count, total

    def _row(self, r: Any, dimension: Dimension, total: Decimal) -> BreakdownRow:
        revenue, cost = money(r.revenue), money(r.cost)
        return BreakdownRow(
            key=None if r.key is None else str(r.key),
            label=r.label,
            reference=getattr(r, "reference", None),
            unit=getattr(r, "unit", None),
            sales_count=int(r.sales_count),
            quantity=getattr(r, "quantity", None),
            revenue=revenue,
            discounts=money(r.discounts) if dimension not in _LINE_DIMENSIONS else None,
            cost=cost,
            margin=revenue - cost,
            margin_rate=rate(revenue - cost, revenue),
            share=rate(revenue, total),
        )

    def _by_sale_dimension(
        self, filters: ReportFilters, period: Period, dimension: Dimension
    ) -> tuple[Any, dict[str, Any], Any, Decimal]:
        sales = self._sales(filters, period)
        total = money(self.db.scalar(select(func.sum(sales.c.total))))
        base: Any
        if dimension is Dimension.SITE:
            base = select(sales, Site.name).join(Site, Site.id == sales.c.site_id).subquery()
            key, label_col = base.c.site_id, base.c.name
        elif dimension is Dimension.SELLER:
            base = (
                select(sales, User.full_name)
                .outerjoin(User, User.id == sales.c.seller_id)
                .subquery()
            )
            key, label_col = base.c.seller_id, base.c.full_name
        elif dimension is Dimension.CUSTOMER:
            customers = customers_view()
            base = (
                select(sales, customers.c.name.label("customer_name"))
                .outerjoin(customers, customers.c.id == sales.c.customer_id)
                .subquery()
            )
            key, label_col = base.c.customer_id, base.c.customer_name
        else:  # canal : code stable, libellé traduit par l'interface
            base = sales
            key, label_col = sales.c.channel, cast(sales.c.channel, String)
        revenue = func.sum(base.c.total).label("revenue")
        cost = func.sum(base.c.cost).label("cost")
        label_expr = func.max(label_col).label("label")
        stmt = select(
            key.label("key"),
            label_expr,
            func.count(base.c.sale_id).label("sales_count"),
            revenue,
            func.sum(base.c.discount_amount).label("discounts"),
            cost,
        ).group_by(key)
        sortable = {
            "revenue": revenue,
            "sales_count": func.count(base.c.sale_id),
            "cost": cost,
            "margin": revenue - cost,
            "label": text_sort(label_expr),
        }
        return stmt, sortable, key, total

    def _by_line_dimension(
        self, filters: ReportFilters, period: Period, dimension: Dimension
    ) -> tuple[Any, dict[str, Any], Any, Decimal]:
        lines = self._lines(filters, period)
        articles = articles_view()
        joined = (
            select(
                lines,
                articles.c.reference,
                articles.c.designation,
                articles.c.unit,
                articles.c.category_id,
                articles.c.category_name,
            )
            .join(articles, articles.c.id == lines.c.article_id)
            .subquery()
        )
        total = money(self.db.scalar(select(func.sum(joined.c.line_total))))
        revenue = func.sum(joined.c.line_total).label("revenue")
        cost = func.sum(joined.c.cost).label("cost")
        sales_count = func.count(func.distinct(joined.c.sale_id))
        if dimension is Dimension.ARTICLE:
            key = joined.c.article_id
            label_expr = func.max(joined.c.designation).label("label")
            stmt = select(
                key.label("key"),
                label_expr,
                func.max(joined.c.reference).label("reference"),
                func.max(joined.c.unit).label("unit"),
                sales_count.label("sales_count"),
                func.sum(joined.c.base_quantity).label("quantity"),
                revenue,
                literal(None).label("discounts"),
                cost,
            ).group_by(key)
            extra = {"quantity": func.sum(joined.c.base_quantity)}
        else:
            key = joined.c.category_id
            label_expr = func.max(joined.c.category_name).label("label")
            stmt = select(
                key.label("key"),
                label_expr,
                sales_count.label("sales_count"),
                revenue,
                literal(None).label("discounts"),
                cost,
            ).group_by(key)
            extra = {}
        sortable = {
            "revenue": revenue,
            "sales_count": sales_count,
            "cost": cost,
            "margin": revenue - cost,
            "label": text_sort(label_expr),
            **extra,
        }
        return stmt, sortable, key, total

    # --- Paiements --------------------------------------------------------------------------

    def payments(self, filters: ReportFilters) -> list[PaymentMethodRow]:
        """Paiements effectués des ventes de la période, par moyen (type et libellé figé)."""
        period = self.period(filters)
        payments = self._scoped(report_payments_query(self.tenant_id), filters, period)
        rows = self.db.execute(
            select(
                payments.c.method,
                payments.c.method_label,
                func.count(payments.c.payment_id).label("payment_count"),
                func.sum(payments.c.amount).label("amount"),
            )
            .group_by(payments.c.method, payments.c.method_label)
            .order_by(func.sum(payments.c.amount).desc(), payments.c.method_label)
        ).all()
        total = sum((money(r.amount) for r in rows), ZERO)
        return [
            PaymentMethodRow(
                method=str(getattr(r.method, "value", r.method)),
                method_label=r.method_label,
                payment_count=int(r.payment_count),
                amount=money(r.amount),
                share=rate(money(r.amount), total),
            )
            for r in rows
        ]


_LINE_DIMENSIONS = (Dimension.ARTICLE, Dimension.CATEGORY)


def _buckets(period: Period, granularity: Granularity) -> list[date]:
    """Débuts de période couvrant ``period`` (série complète, périodes vides comprises)."""
    if granularity is Granularity.DAY:
        start = period.start
    elif granularity is Granularity.WEEK:
        start = period.start - timedelta(days=period.start.weekday())
    else:
        start = period.start.replace(day=1)
    points: list[date] = []
    current = start
    while current <= period.end:
        points.append(current)
        if granularity is Granularity.DAY:
            current += timedelta(days=1)
        elif granularity is Granularity.WEEK:
            current += timedelta(days=7)
        else:
            current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return points


__all__ = ["ReportFilters", "ReportService"]
