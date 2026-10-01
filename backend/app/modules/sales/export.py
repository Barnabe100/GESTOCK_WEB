"""Export de l'historique des ventes (Lot 2) : Excel, CSV et PDF A4.

Les ventes exportées sont celles de ``SaleService.query`` (mêmes filtres, même portée, mêmes
sites que la liste) ; ce module ne fait que les mettre en table."""

import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog.api import get_article_refs
from app.modules.customers.api import get_customer_refs
from app.modules.sales.filters import SaleFilters
from app.modules.sales.models import CreditStatus, SaleChannel, SalePaymentStatus, SaleStatus
from app.modules.sales.service import SaleService
from app.platform.context import RequestContext
from app.platform.exports import ColumnKind, ExportColumn, ExportFormat, ExportTable
from app.platform.identity.models import User
from app.platform.tenancy.models import Site

SALES_EXPORT_FEATURE = "sales.history"
# L'historique propose les trois formats : données (Excel, CSV) et rapport imprimable (PDF).
SALES_EXPORT_FORMATS = (ExportFormat.XLSX, ExportFormat.CSV, ExportFormat.PDF)

STATUS = {
    SaleStatus.DRAFT: "Brouillon",
    SaleStatus.VALIDATED: "Validée",
    SaleStatus.CANCELLED: "Annulée",
}
PAYMENT = {
    SalePaymentStatus.UNPAID: "Non payée",
    SalePaymentStatus.PARTIALLY_PAID: "Partiellement payée",
    SalePaymentStatus.PAID: "Payée",
}
CREDIT = {
    CreditStatus.OPEN: "Crédit ouvert",
    CreditStatus.PARTIAL: "Crédit partiellement réglé",
    CreditStatus.PAID: "Crédit soldé",
    CreditStatus.CANCELLED: "Crédit annulé",
}
CHANNEL = {SaleChannel.BACKOFFICE: "Gestion", SaleChannel.POS: "Point de vente"}

COLUMNS = (
    ExportColumn("Numéro", width=20),
    ExportColumn("Date de vente", ColumnKind.DATE, width=11),
    ExportColumn("Site", width=14),
    ExportColumn("Client", width=18),
    ExportColumn("Vendeur", width=16),
    ExportColumn("Canal", width=11),
    ExportColumn("Statut", width=10),
    ExportColumn("Total", ColumnKind.MONEY, width=12),
    ExportColumn("Payé", ColumnKind.MONEY, width=12),
    ExportColumn("Reste dû", ColumnKind.MONEY, width=12),
    ExportColumn("Encaissement", width=14),
    ExportColumn("Crédit", width=14),
    ExportColumn("Validée le", ColumnKind.DATETIME, width=14),
)


def sales_history_table(
    db: Session,
    ctx: RequestContext,
    now: datetime,
    filters: SaleFilters,
    sort: str | None,
    max_rows: int,
) -> ExportTable:
    service = SaleService(db, ctx, now)
    sales = service.to_out(service.export(filters, sort, max_rows))
    rows: list[list[Any]] = [
        [
            sale.number or "Non numérotée",
            sale.sale_date,
            sale.site_name,
            sale.customer_name or "Ordinaire",
            sale.created_by_name,
            CHANNEL.get(sale.channel, sale.channel.value),
            STATUS.get(sale.status, sale.status.value),
            sale.total,
            sale.paid_amount,
            sale.remaining_amount,
            PAYMENT.get(sale.payment_status) if sale.payment_status else None,
            CREDIT.get(sale.credit_status) if sale.credit_status else None,
            sale.validated_at,
        ]
        for sale in sales
    ]
    local_now = now.astimezone(ZoneInfo(ctx.tenant.timezone))
    context = [
        ctx.tenant.name,
        f"Généré le {local_now.strftime('%d/%m/%Y à %H:%M')} par {ctx.user.full_name}",
        f"Filtres : {_describe(db, filters)}",
        f"Nombre de ventes : {len(rows)}",
    ]
    return ExportTable(
        title="Historique des ventes",
        file_stem="ventes",
        columns=COLUMNS,
        rows=rows,
        timezone=ctx.tenant.timezone,
        context_lines=context,
    )


def _describe(db: Session, f: SaleFilters) -> str:
    """Résumé lisible des filtres réellement appliqués (en-tête du PDF)."""
    parts: list[str] = []
    if f.search:
        parts.append(f"recherche « {f.search.strip()} »")
    if f.date_from or f.date_to:
        start = f.date_from.strftime("%d/%m/%Y") if f.date_from else "…"
        end = f.date_to.strftime("%d/%m/%Y") if f.date_to else "…"
        parts.append(f"période {start} – {end}")
    if f.status:
        parts.append(f"statut {STATUS[f.status]}")
    if f.payment_status:
        parts.append(f"encaissement {PAYMENT[f.payment_status]}")
    if f.channel:
        parts.append(f"canal {CHANNEL[f.channel]}")
    if f.site_id:
        parts.append(f"site {_name(db, Site, f.site_id)}")
    if f.customer_id:
        ref = get_customer_refs(db, {f.customer_id}).get(f.customer_id)
        parts.append(f"client {ref.name if ref else '—'}")
    if f.seller_id:
        parts.append(f"vendeur {_name(db, User, f.seller_id, User.full_name)}")
    if f.mine:
        parts.append("mes ventes")
    if f.article_id:
        article = get_article_refs(db, {f.article_id}).get(f.article_id)
        parts.append(f"article {article.reference if article else '—'}")
    if f.article_reference and f.article_reference.strip():
        parts.append(f"référence article « {f.article_reference.strip()} »")
    if f.payment_reference and f.payment_reference.strip():
        parts.append(f"référence paiement « {f.payment_reference.strip()} »")
    return ", ".join(parts) if parts else "aucun"


def _name(db: Session, model: Any, entity_id: uuid.UUID, column: Any = None) -> str:
    value = db.scalar(
        select(column if column is not None else model.name).where(model.id == entity_id)
    )
    return str(value) if value else "—"
