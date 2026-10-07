"""Point de vente (Phase 3.0, ADR-0023).

Le POS n'a AUCUNE logique de vente propre : il orchestre les services existants.

- Recherche d'articles : niveaux de stock du site (``stock.api.list_levels``, recherche
  serveur sur référence / désignation / code-barres, limitée) + prix du catalogue
  (``catalog.api``) — l'assortiment ACTIF du site seulement (Recette, étape 1, ADR-0046) : un
  article hors assortiment n'est jamais proposé à la caisse.
- Encaissement : ``SaleService.checkout`` (module Ventes) — création, validation (StockService,
  limite de crédit), paiements immédiats (PaymentService ; caisse pour les espèces seulement),
  audit, dans une seule transaction, idempotent.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleError, NotFoundError
from app.modules.catalog.api import (
    active_packagings,
    ensure_in_assortment,
    get_article_refs,
    resolve_barcode,
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
    """Articles de l'assortiment ACTIF du site de vente (site sélectionné, ou site demandé et
    accessible), actifs d'abord ; un article inactif est renvoyé (information) mais ne peut pas
    être vendu. Hors assortiment : jamais proposé, même avec du stock résiduel (ADR-0046)."""
    site = operation_site(ctx, site_id)
    rows, _ = list_levels(
        db,
        ctx.tenant_id,
        {site},
        PageParams(limit=limit, offset=0, sort="designation"),
        search=search,
        include_inactive=True,
        include_unmanaged=True,
        assortment_only=True,
    )
    return _to_out(db, sorted(rows, key=lambda r: not r.article_active))


def article_by_barcode(
    db: Session, ctx: RequestContext, site_id: uuid.UUID | None, barcode: str
) -> PosArticleOut:
    """Scan (Lot 3-A, Lot 3-D — ADR-0039, ADR-0042) : égalité EXACTE sur un code du registre
    (principal, supplémentaire, conditionnement) porté par une présentation ACTIVE ; aucune
    recherche partielle, ni sur la référence ou la désignation. Inconnu : ``404
    barcode_unknown`` (rien n'est ajouté au panier). Code d'un conditionnement :
    ``scanned_packaging_id`` — le panier ajoute 1 conditionnement (jamais N unités de base) ;
    prix non configuré : ``422 packaging_price_not_set`` (invendable, Lot 3-B). Article
    connu hors de l'assortiment ACTIF du site : ``422 article_not_in_site_assortment``
    (ADR-0046, D3) — rien n'est ajouté au panier."""
    site = operation_site(ctx, site_id)
    match = resolve_barcode(db, barcode)
    if match is not None:
        ensure_in_assortment(db, site, {match.article_id})
    rows = (
        list_levels(
            db,
            ctx.tenant_id,
            {site},
            PageParams(limit=1, offset=0, sort="designation"),
            article_ids={match.article_id},
            include_unmanaged=True,
            assortment_only=True,
        )[0]
        if match is not None
        else []
    )
    found = _to_out(db, rows)
    if not found or match is None:
        raise NotFoundError("Code-barres inconnu", code="barcode_unknown")
    article = found[0]
    if match.packaging_id is not None:
        if not any(p.id == match.packaging_id for p in article.packagings):
            name = next(
                p.name
                for p in active_packagings(db, {match.article_id}, priced_only=False).get(
                    match.article_id, []
                )
                if p.id == match.packaging_id
            )
            raise BusinessRuleError(
                "Le prix de cette présentation n'est pas configuré : elle ne peut pas être vendue",
                code="packaging_price_not_set",
                extra={"packagings": [name]},
            )
        article.scanned_packaging_id = match.packaging_id
    return article


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
                if p.sale_price is not None
            ],
        )
        for r in rows
        if r.article_id in refs
    ]
