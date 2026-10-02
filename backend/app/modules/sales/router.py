import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.errors import ForbiddenError
from app.modules.sales.export import (
    SALES_EXPORT_FEATURE,
    SALES_EXPORT_FORMATS,
    sales_history_table,
)
from app.modules.sales.filters import SaleFilters, sale_filters
from app.modules.sales.payment_router import router as payment_router
from app.modules.sales.schemas import (
    SaleCancel,
    SaleCreate,
    SaleEventOut,
    SaleInput,
    SaleOut,
    SaleValidate,
    SellerOut,
)
from app.modules.sales.service import SaleService
from app.modules.stock.api import AvailableLotsOut, available_lots_out, operation_site
from app.platform.audit.service import entity_history
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    SettingsDep,
    require_permission,
)
from app.platform.exports import ExportFormat, audit_export, check_format, export_response
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(tags=["sales"])

VIEW = "sales.sale.view"
View = Annotated[RequestContext, Depends(require_permission(VIEW))]
AuditView = Annotated[RequestContext, Depends(require_permission("audit.log.view"))]
Export = Annotated[RequestContext, Depends(require_permission("sales.sale.export"))]
Filters = Annotated[SaleFilters, Depends(sale_filters)]
Create = Annotated[RequestContext, Depends(require_permission("sales.sale.create"))]
Update = Annotated[RequestContext, Depends(require_permission("sales.sale.update"))]
Validate = Annotated[RequestContext, Depends(require_permission("sales.sale.validate"))]
Cancel = Annotated[RequestContext, Depends(require_permission("sales.sale.cancel"))]
Paging = Annotated[PageParams, Depends(page_params)]


@router.get("", response_model=Page[SaleOut])
def list_sales(
    ctx: View, db: DbSession, now: NowDep, paging: Paging, filters: Filters
) -> Page[SaleOut]:
    service = SaleService(db, ctx, now)
    items, total = service.search(paging, filters)
    return Page(items=service.to_out(items), total=total, limit=paging.limit, offset=paging.offset)


@router.get("/sellers", response_model=list[SellerOut])
def list_sellers(ctx: View, db: DbSession, now: NowDep) -> list[SellerOut]:
    """Vendeurs / opérateurs du filtre : auteurs des ventes visibles par l'utilisateur."""
    return [SellerOut(id=i, name=n) for i, n in SaleService(db, ctx, now).sellers()]


@router.get("/export")
def export_sales(
    ctx: Export,
    db: DbSession,
    now: NowDep,
    settings: SettingsDep,
    filters: Filters,
    export_format: Annotated[ExportFormat, Query(alias="format")],
    sort: Annotated[str | None, Query(max_length=50)] = None,
) -> Response:
    """Export de l'historique : exactement le périmètre de la liste (mêmes filtres, même
    portée « ses ventes » / ``view_all``, mêmes sites), au format choisi ; audité."""
    if not ctx.has_permission(VIEW):
        raise ForbiddenError("Permission insuffisante", code="permission_denied")
    check_format(export_format, SALES_EXPORT_FORMATS)
    table = sales_history_table(db, ctx, now, filters, sort, settings.export_max_rows)
    audit_export(
        db,
        ctx,
        feature=SALES_EXPORT_FEATURE,
        fmt=export_format,
        filters=filters.used(),
        row_count=len(table.rows),
        site_id=filters.site_id,
    )
    db.commit()
    return export_response(table, export_format, now)


@router.post("", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def create_sale(body: SaleCreate, ctx: Create, db: DbSession, now: NowDep) -> SaleOut:
    service = SaleService(db, ctx, now)
    sale = service.create(body)
    db.commit()
    return service.to_out([sale], with_lines=True)[0]


@router.get("/articles/{article_id}/lots", response_model=AvailableLotsOut)
def sale_available_lots(
    article_id: uuid.UUID,
    ctx: Validate,
    db: DbSession,
    now: NowDep,
    site_id: uuid.UUID | None = None,
) -> AvailableLotsOut:
    """Lots disponibles d'un article sur le site de vente (Lot 3-H-A, H-D18) : ordre de
    consommation du serveur (FEFO / FIFO), lots périmés signalés (jamais consommés sans
    dérogation explicite). Aucun coût."""
    site = operation_site(ctx, site_id)
    return available_lots_out(db, ctx, now, site, article_id)


@router.get("/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: uuid.UUID, ctx: View, db: DbSession, now: NowDep) -> SaleOut:
    service = SaleService(db, ctx, now)
    return service.to_out([service.get(sale_id)], with_lines=True)[0]


@router.get("/{sale_id}/history", response_model=list[SaleEventOut])
def sale_history(
    sale_id: uuid.UUID, ctx: AuditView, db: DbSession, now: NowDep
) -> list[SaleEventOut]:
    """Chronologie de la vente (Lot 2) : évènements réellement journalisés de la vente et de ses
    paiements. Exige ``audit.log.view`` ET une vente visible (portée de ``sales.sale.view``)."""
    if not ctx.has_permission(VIEW):
        raise ForbiddenError("Permission insuffisante", code="permission_denied")
    sale = SaleService(db, ctx, now).get(sale_id)
    return [
        SaleEventOut(
            id=log.id,
            occurred_at=log.occurred_at,
            action=log.action,
            user_name=user_name,
            data=log.data,
        )
        for log, user_name in entity_history(
            db, ctx.tenant_id, "sale", sale.id, linked_type="payment", link_key="sale_id"
        )
    ]


@router.put("/{sale_id}", response_model=SaleOut)
def update_sale(
    sale_id: uuid.UUID, body: SaleInput, ctx: Update, db: DbSession, now: NowDep
) -> SaleOut:
    service = SaleService(db, ctx, now)
    sale = service.update(sale_id, body)
    db.commit()
    return service.to_out([sale], with_lines=True)[0]


@router.post("/{sale_id}/validate", response_model=SaleOut)
def validate_sale(
    sale_id: uuid.UUID,
    ctx: Validate,
    db: DbSession,
    now: NowDep,
    body: SaleValidate | None = None,
) -> SaleOut:
    payments = body.payments if body else []
    # Encaisser à la validation exige aussi le droit d'encaisser.
    if payments and not ctx.has_permission("sales.payment.create"):
        raise ForbiddenError("Permission insuffisante", code="permission_denied")
    service = SaleService(db, ctx, now)
    sale = service.validate(
        sale_id,
        payments,
        body.credit_override if body else None,
        body.expired_lot_override if body else None,
    )
    db.commit()
    return service.to_out([sale], with_lines=True)[0]


@router.post("/{sale_id}/cancel", response_model=SaleOut)
def cancel_sale(
    sale_id: uuid.UUID, body: SaleCancel, ctx: Cancel, db: DbSession, now: NowDep
) -> SaleOut:
    service = SaleService(db, ctx, now)
    sale = service.cancel(sale_id, body.reason)
    db.commit()
    return service.to_out([sale], with_lines=True)[0]


# Paiements : sous-ressource de la vente (/sales/{sale_id}/payments).
router.include_router(payment_router)
