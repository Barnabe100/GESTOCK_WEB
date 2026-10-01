"""Point de vente (Phase 3.0, ADR-0023).

Le POS n'a AUCUNE logique de vente propre : il orchestre les services existants.

- Recherche d'articles : niveaux de stock du site (``stock.api.list_levels``, recherche
  serveur sur référence / désignation / code-barres, limitée) + prix du catalogue
  (``catalog.api``).
- Encaissement : ``SaleService.checkout`` (module Ventes) — création, validation (StockService,
  limite de crédit), paiements immédiats (PaymentService ; caisse pour les espèces seulement),
  audit, dans une seule transaction, idempotent.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.modules.catalog.api import (
    active_packagings,
    find_active_article_by_barcode,
    get_article_refs,
)
from app.modules.pos.schemas import PosArticleOut, PosPackagingOut
from app.modules.stock.api import LevelRow, list_levels, operation_site
from app.platform.context import RequestContext
from app.shared.pagination import PageParams


def search_articles(
    db: Session,
    ctx: RequestContext,
    site_id: uuid.UUID | None,
    search: str | None,
    limit: int,
) -> list[PosArticleOut]:
    """Articles du site de vente (site sélectionné, ou site demandé et accessible), actifs
    d'abord ; un article inactif est renvoyé (information) mais ne peut pas être vendu."""
    site = operation_site(ctx, site_id)
    rows, _ = list_levels(
        db,
        ctx.tenant_id,
        {site},
        PageParams(limit=limit, offset=0, sort="designation"),
        search=search,
        include_inactive=True,
        include_unmanaged=True,
    )
    return _to_out(db, sorted(rows, key=lambda r: not r.article_active))


def article_by_barcode(
    db: Session, ctx: RequestContext, site_id: uuid.UUID | None, barcode: str
) -> PosArticleOut:
    """Scan (Lot 3-A, ADR-0039) : égalité EXACTE sur le code-barres d'un article ACTIF du
    tenant ; aucune recherche partielle, ni sur la référence ou la désignation. Inconnu :
    ``404 barcode_unknown`` (rien n'est ajouté au panier)."""
    site = operation_site(ctx, site_id)
    article_id = find_active_article_by_barcode(db, barcode)
    rows = (
        list_levels(
            db,
            ctx.tenant_id,
            {site},
            PageParams(limit=1, offset=0, sort="designation"),
            article_ids={article_id},
            include_unmanaged=True,
        )[0]
        if article_id is not None
        else []
    )
    found = _to_out(db, rows)
    if not found:
        raise NotFoundError("Code-barres inconnu", code="barcode_unknown")
    return found[0]


def _to_out(db: Session, rows: list[LevelRow]) -> list[PosArticleOut]:
    ids = {r.article_id for r in rows}
    refs = get_article_refs(db, ids)
    packagings = active_packagings(db, ids)
    return [
        PosArticleOut(
            article_id=r.article_id,
            reference=r.reference,
            designation=r.designation,
            unit=r.unit,
            category_name=r.category_name,
            sale_price=refs[r.article_id].sale_price,
            quantity=r.quantity,
            is_active=r.article_active,
            stock_managed=refs[r.article_id].stock_managed,
            decimal_quantity_allowed=refs[r.article_id].decimal_quantity_allowed,
            packagings=[
                PosPackagingOut(
                    id=p.id, name=p.name, conversion=p.conversion, sale_price=p.sale_price
                )
                for p in packagings.get(r.article_id, [])
            ],
        )
        for r in rows
        if r.article_id in refs
    ]
