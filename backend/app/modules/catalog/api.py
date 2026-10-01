"""API publique du module Catalogue pour les autres modules (stock, ventes…). Ils n'importent
jamais ses modèles directement (règle d'architecture 10)."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Subquery, select
from sqlalchemy.orm import Session

from app.modules.catalog.models import Article, Category
from app.modules.catalog.stock_port import register_stocked_sites

__all__ = [
    "ArticleRef",
    "articles_view",
    "find_active_article_by_barcode",
    "get_article_refs",
    "lock_stock_managed",
    "register_stocked_sites",
]


@dataclass(frozen=True)
class ArticleRef:
    id: uuid.UUID
    reference: str
    designation: str
    unit: str
    is_active: bool
    min_stock: Decimal
    max_stock: Decimal | None
    sale_price: Decimal
    # Lot 3-A : ``False`` = vendu sans stock (aucun mouvement, aucun contrôle).
    stock_managed: bool = True


def get_article_refs(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, ArticleRef]:
    """Articles du tenant actif (filtrés par tenant) ; les identifiants inconnus sont absents."""
    if not ids:
        return {}
    return {
        a.id: ArticleRef(
            id=a.id,
            reference=a.reference,
            designation=a.designation,
            unit=a.unit,
            is_active=a.is_active,
            min_stock=a.min_stock,
            max_stock=a.max_stock,
            sale_price=a.sale_price,
            stock_managed=a.stock_managed,
        )
        for a in db.scalars(select(Article).where(Article.id.in_(ids)))
    }


def lock_stock_managed(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, bool]:
    """« Géré en stock » des articles, lu sous verrou PARTAGÉ (``FOR SHARE``, ordre des
    identifiants) jusqu'à la fin de la transaction : toute opération de stock prend ce verrou,
    et le passage « géré » → « non géré » prend le verrou exclusif de l'article avant de
    vérifier son stock — l'un attend l'autre, jamais de stock sur un article non géré."""
    if not ids:
        return {}
    rows = db.execute(
        select(Article.id, Article.stock_managed)
        .where(Article.id.in_(ids))
        .order_by(Article.id)
        .with_for_update(read=True)
    ).all()
    return {row[0]: row[1] for row in rows}


def find_active_article_by_barcode(db: Session, barcode: str) -> uuid.UUID | None:
    """Scan (Lot 3-A) : égalité EXACTE sur le code-barres, article ACTIF du tenant (RLS) ;
    jamais de recherche partielle, ni sur la référence ou la désignation."""
    code = barcode.strip()
    if not code:
        return None
    return db.scalars(
        select(Article.id).where(Article.barcode == code, Article.is_active.is_(True))
    ).one_or_none()


def articles_view() -> Subquery:
    """Vue en lecture des articles (colonnes publiques) pour les jointures d'autres modules.
    Le filtrage par tenant reste assuré par la RLS et la condition ``tenant_id`` de l'appelant."""
    return (
        select(
            Article.id,
            Article.tenant_id,
            Article.reference,
            Article.designation,
            Article.unit,
            Article.barcode,
            Article.is_active,
            Article.stock_managed,
            Article.min_stock,
            Article.max_stock,
            Article.category_id,
            Category.name.label("category_name"),
        )
        .join(
            Category,
            (Category.id == Article.category_id) & (Category.tenant_id == Article.tenant_id),
        )
        .subquery("articles_view")
    )
