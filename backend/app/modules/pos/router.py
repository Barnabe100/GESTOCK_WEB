import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.errors import ForbiddenError
from app.modules.pos.schemas import PosArticleOut
from app.modules.pos.service import article_by_barcode, search_articles
from app.modules.sales.api import CheckoutOut, PaymentService, SaleCheckout, SaleService
from app.modules.stock.api import AvailableLotsOut, available_lots_out, operation_site
from app.platform.context import DbSession, NowDep, RequestContext, require_permission

router = APIRouter(tags=["pos"])

Use = Annotated[RequestContext, Depends(require_permission("pos.terminal.use"))]


def _require(ctx: RequestContext, *codes: str) -> None:
    """Le POS n'ouvre aucun droit : il exige aussi les permissions des opérations de vente."""
    missing = [c for c in codes if not ctx.has_permission(c)]
    if missing:
        restricted = any(c in ctx.capabilities.restricted_permissions for c in missing)
        raise ForbiddenError(
            "Action indisponible avec le statut actuel de l'abonnement"
            if restricted
            else "Permission insuffisante",
            code="subscription_restricted" if restricted else "permission_denied",
            extra={"permissions": missing},
        )


@router.get("/articles", response_model=list[PosArticleOut])
def pos_articles(
    ctx: Use,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    site_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 24,
) -> list[PosArticleOut]:
    return search_articles(db, ctx, site_id, search, limit)


@router.get("/articles/by-barcode", response_model=PosArticleOut)
def pos_article_by_barcode(
    ctx: Use,
    db: DbSession,
    barcode: Annotated[str, Query(min_length=1, max_length=50)],
    site_id: uuid.UUID | None = None,
) -> PosArticleOut:
    """Scan d'un code-barres : correspondance EXACTE (article actif) ou ``404 barcode_unknown``."""
    return article_by_barcode(db, ctx, site_id, barcode)


@router.get("/articles/{article_id}/lots", response_model=AvailableLotsOut)
def pos_article_lots(
    article_id: uuid.UUID,
    ctx: Use,
    db: DbSession,
    now: NowDep,
    site_id: uuid.UUID | None = None,
) -> AvailableLotsOut:
    """Lots disponibles au point de vente (Lot 3-H-A, H-D18) — point d'accès DÉDIÉ (pas la
    page générale des lots) : site de vente, solde positif, péremption et état, ordre FEFO /
    FIFO du moteur ; lots périmés signalés (dérogation explicite seulement). Aucun coût."""
    site = operation_site(ctx, site_id)
    return available_lots_out(db, ctx, now, site, article_id)


@router.post("/checkout", response_model=CheckoutOut, status_code=status.HTTP_201_CREATED)
def checkout(
    body: SaleCheckout, ctx: Use, db: DbSession, now: NowDep, response: Response
) -> CheckoutOut:
    _require(ctx, "sales.sale.create", "sales.sale.validate")
    if body.payments:
        _require(ctx, "sales.payment.create")
    sales = SaleService(db, ctx, now)
    sale, replayed = sales.checkout(body)
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK
    payments = PaymentService(db, ctx, now).history_out(sale.id)
    return CheckoutOut(
        sale=sales.to_out([sale], with_lines=True)[0], payments=payments, replayed=replayed
    )
