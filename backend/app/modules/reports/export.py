"""Exports des rapports (palier R10, ADR-0055) : architecture commune ``app/platform/exports``
(Excel, CSV `;` UTF-8 BOM, PDF A4), périmètre EXACT du rapport affiché, garde-fou
``SM_EXPORT_MAX_ROWS``, audit ``export.generated``. Sans ``catalog.article.cost_view``, les
colonnes de coût et de marge sont ABSENTES du fichier (jamais remplacées par une valeur)."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from app.modules.reports.schemas import BreakdownRow, Dimension, Granularity
from app.modules.reports.service import ReportFilters, ReportService
from app.platform.exports import (
    ColumnKind,
    ExportColumn,
    ExportFormat,
    ExportTable,
    check_row_limit,
)
from app.shared.pagination import PageParams

REPORT_EXPORT_FORMATS = (ExportFormat.XLSX, ExportFormat.CSV, ExportFormat.PDF)


class ReportKind(StrEnum):
    SUMMARY = "summary"
    BREAKDOWN = "breakdown"
    PAYMENTS = "payments"


CHANNEL = {"BACKOFFICE": "Gestion", "POS": "Point de vente", "RESTAURANT": "Restauration"}
DIMENSION = {
    Dimension.SITE: "Site",
    Dimension.SELLER: "Vendeur",
    Dimension.CHANNEL: "Canal",
    Dimension.CUSTOMER: "Client",
    Dimension.ARTICLE: "Article",
    Dimension.CATEGORY: "Catégorie",
}
GRANULARITY = {Granularity.DAY: "jour", Granularity.WEEK: "semaine", Granularity.MONTH: "mois"}
MONEY = ColumnKind.MONEY


def _cost_columns(visible: bool, *columns: ExportColumn) -> list[ExportColumn]:
    return list(columns) if visible else []


def summary_table(service: ReportService, filters: ReportFilters, granularity: Granularity) -> Any:
    out = service.summary(filters, granularity)
    costs = service.costs_visible()
    columns = [
        ExportColumn("Période", ColumnKind.DATE, width=11),
        ExportColumn("Ventes", width=8),
        ExportColumn("Chiffre d'affaires", MONEY, width=14),
        ExportColumn("Remises", MONEY, width=12),
        *_cost_columns(
            costs, ExportColumn("Coût", MONEY, width=12), ExportColumn("Marge", MONEY, width=12)
        ),
    ]
    rows = [
        [
            p.period,
            p.sales_count,
            p.revenue,
            p.discounts,
            *([p.cost, p.margin] if costs else []),
        ]
        for p in out.series
    ]
    totals = out.totals
    rows.append(
        [
            "Total",
            totals.sales_count,
            totals.revenue,
            totals.discounts,
            *([totals.cost, totals.margin] if costs else []),
        ]
    )
    context = [f"Granularité : {GRANULARITY[granularity]}"]
    return _table(service, "Synthèse des ventes", "rapport-ventes", columns, rows, out, context)


def breakdown_table(
    service: ReportService, filters: ReportFilters, dimension: Dimension, max_rows: int
) -> Any:
    rows_out, count, _ = service.breakdown(
        filters, dimension, PageParams(limit=max_rows, offset=0, sort=None)
    )
    check_row_limit(count, max_rows)
    costs = service.costs_visible()
    is_article = dimension is Dimension.ARTICLE
    is_line = dimension in (Dimension.ARTICLE, Dimension.CATEGORY)
    columns = [
        ExportColumn(DIMENSION[dimension], width=22),
        *([ExportColumn("Référence", width=14)] if is_article else []),
        ExportColumn("Ventes", width=8),
        *(
            [
                ExportColumn("Quantité", ColumnKind.QUANTITY, width=10),
                ExportColumn("Unité", width=8),
            ]
            if is_article
            else []
        ),
        ExportColumn("Montant des lignes" if is_line else "Chiffre d'affaires", MONEY, width=14),
        *([] if is_line else [ExportColumn("Remises", MONEY, width=12)]),
        *_cost_columns(
            costs,
            ExportColumn("Coût", MONEY, width=12),
            ExportColumn("Marge", MONEY, width=12),
            ExportColumn("Taux de marge (%)", MONEY, width=10),
        ),
        ExportColumn("Part (%)", MONEY, width=9),
    ]

    def label(row: BreakdownRow) -> str:
        if dimension is Dimension.CHANNEL:
            return CHANNEL.get(row.key or "", row.key or "")
        return row.label or ("Ordinaire" if dimension is Dimension.CUSTOMER else "—")

    rows = [
        [
            label(r),
            *([r.reference] if is_article else []),
            r.sales_count,
            *([r.quantity, r.unit] if is_article else []),
            r.revenue,
            *([] if is_line else [r.discounts]),
            *([r.cost, r.margin, r.margin_rate] if costs else []),
            r.share,
        ]
        for r in rows_out
    ]
    out = service.summary(filters, Granularity.MONTH)
    context = [f"Répartition par {DIMENSION[dimension].lower()}"]
    if is_line:
        context.append("Montants des lignes avant remise globale ; coût des mouvements directs.")
    return _table(
        service,
        f"Ventes par {DIMENSION[dimension].lower()}",
        f"rapport-ventes-{dimension.value}",
        columns,
        rows,
        out,
        context,
    )


def payments_table(service: ReportService, filters: ReportFilters) -> Any:
    rows_out = service.payments(filters)
    columns = [
        ExportColumn("Moyen de paiement", width=22),
        ExportColumn("Paiements", width=10),
        ExportColumn("Montant", MONEY, width=14),
        ExportColumn("Part (%)", MONEY, width=9),
    ]
    rows = [[r.method_label, r.payment_count, r.amount, r.share] for r in rows_out]
    out = service.summary(filters, Granularity.MONTH)
    return _table(service, "Paiements par moyen", "rapport-paiements", columns, rows, out, [])


def _table(
    service: ReportService,
    title: str,
    stem: str,
    columns: list[ExportColumn],
    rows: list[list[Any]],
    out: Any,
    extra: list[str],
) -> ExportTable:
    ctx = service.ctx
    local_now: datetime = service.now.astimezone(ZoneInfo(ctx.tenant.timezone))
    context = [
        ctx.tenant.name,
        f"Généré le {local_now.strftime('%d/%m/%Y à %H:%M')} par {ctx.user.full_name}",
        f"Période : {out.date_from.strftime('%d/%m/%Y')} – {out.date_to.strftime('%d/%m/%Y')}",
        f"Sites : {len(out.site_ids)}",
        *extra,
    ]
    return ExportTable(
        title=title,
        file_stem=stem,
        columns=columns,
        rows=rows,
        timezone=ctx.tenant.timezone,
        context_lines=context,
    )
