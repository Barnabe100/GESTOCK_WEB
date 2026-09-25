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

from app.modules.catalog.api import get_article_refs
from app.modules.pos.schemas import PosArticleOut
from app.modules.stock.api import list_levels, operation_site
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
    )
    prices = get_article_refs(db, {r.article_id for r in rows})
    ordered = sorted(rows, key=lambda r: not r.article_active)
    return [
        PosArticleOut(
            article_id=r.article_id,
            reference=r.reference,
            designation=r.designation,
            unit=r.unit,
            category_name=r.category_name,
            sale_price=prices[r.article_id].sale_price,
            quantity=r.quantity,
            is_active=r.article_active,
        )
        for r in ordered
        if r.article_id in prices
    ]
