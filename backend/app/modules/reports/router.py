"""Rapports (palier R10, ADR-0055) : consultation et exports, aucune écriture métier."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.errors import ForbiddenError
from app.modules.reports.export import (
    REPORT_EXPORT_FORMATS,
    ReportKind,
    breakdown_table,
    payments_table,
    summary_table,
)
from app.modules.reports.permissions import REPORT_EXPORT, REPORT_VIEW
from app.modules.reports.schemas import (
    BreakdownRow,
    Dimension,
    Granularity,
    PaymentMethodRow,
    SalesSummaryOut,
)
from app.modules.reports.service import ReportFilters, ReportService
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    SettingsDep,
    require_permission,
)
from app.platform.costs import REPORT_COST_FIELDS, cost_masking_route
from app.platform.exports import ExportFormat, audit_export, check_format, export_response
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Money, Page

router = APIRouter(tags=["reports"], route_class=cost_masking_route(REPORT_COST_FIELDS))

View = Annotated[RequestContext, Depends(require_permission(REPORT_VIEW))]
Export = Annotated[RequestContext, Depends(require_permission(REPORT_EXPORT))]
Paging = Annotated[PageParams, Depends(page_params)]


def report_filters(
    date_from: date | None = None,
    date_to: date | None = None,
    site_id: uuid.UUID | None = None,
    channel: Annotated[str | None, Query(pattern=r"^[A-Z_]{1,20}$")] = None,
) -> ReportFilters:
    return ReportFilters(date_from=date_from, date_to=date_to, site_id=site_id, channel=channel)


Filters = Annotated[ReportFilters, Depends(report_filters)]


@router.get("/sales/summary", response_model=SalesSummaryOut)
def sales_summary(
    ctx: View,
    db: DbSession,
    now: NowDep,
    filters: Filters,
    granularity: Granularity = Granularity.DAY,
) -> SalesSummaryOut:
    """Indicateurs de la période (CA, remises, panier moyen, encaissé, coût, marge) et série
    par jour / semaine / mois (périodes vides comprises)."""
    return ReportService(db, ctx, now).summary(filters, granularity)


class BreakdownPage(Page[BreakdownRow]):
    revenue_total: Money  # CA (ou montant des lignes) du périmètre entier


@router.get("/sales/breakdown", response_model=BreakdownPage)
def sales_breakdown(
    ctx: View,
    db: DbSession,
    now: NowDep,
    filters: Filters,
    paging: Paging,
    dimension: Dimension,
) -> BreakdownPage:
    """Répartition par site, vendeur, canal, client, article ou catégorie (tri serveur)."""
    items, total, revenue = ReportService(db, ctx, now).breakdown(filters, dimension, paging)
    return BreakdownPage(
        items=items,
        total=total,
        limit=paging.limit,
        offset=paging.offset,
        revenue_total=revenue,
    )


@router.get("/payments", response_model=list[PaymentMethodRow])
def payments_by_method(
    ctx: View, db: DbSession, now: NowDep, filters: Filters
) -> list[PaymentMethodRow]:
    """Paiements effectués des ventes de la période, par moyen."""
    return ReportService(db, ctx, now).payments(filters)


@router.get("/export")
def export_report(
    ctx: Export,
    db: DbSession,
    now: NowDep,
    settings: SettingsDep,
    filters: Filters,
    report: ReportKind,
    export_format: Annotated[ExportFormat, Query(alias="format")],
    granularity: Granularity = Granularity.DAY,
    dimension: Dimension | None = None,
) -> Response:
    """Export du rapport affiché (même périmètre, mêmes filtres) ; audité."""
    if not ctx.has_permission(REPORT_VIEW):
        raise ForbiddenError("Permission insuffisante", code="permission_denied")
    check_format(export_format, REPORT_EXPORT_FORMATS)
    service = ReportService(db, ctx, now)
    if report is ReportKind.SUMMARY:
        table = summary_table(service, filters, granularity)
    elif report is ReportKind.PAYMENTS:
        table = payments_table(service, filters)
    else:
        table = breakdown_table(
            service, filters, dimension or Dimension.ARTICLE, settings.export_max_rows
        )
    used = {
        k: str(v)
        for k, v in {
            "report": report.value,
            "date_from": filters.date_from,
            "date_to": filters.date_to,
            "site_id": filters.site_id,
            "channel": filters.channel,
            "granularity": granularity.value if report is ReportKind.SUMMARY else None,
            "dimension": (dimension or Dimension.ARTICLE).value
            if report is ReportKind.BREAKDOWN
            else None,
        }.items()
        if v is not None
    }
    audit_export(
        db,
        ctx,
        feature=f"reports.{report.value}",
        fmt=export_format,
        filters=used,
        row_count=len(table.rows),
        site_id=filters.site_id,
    )
    db.commit()
    return export_response(table, export_format, now)
