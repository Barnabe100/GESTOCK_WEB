"""API publique du module Catalogue pour les autres modules (stock, ventes…). Ils n'importent
jamais ses modèles directement (règle d'architecture 10)."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Subquery, select
from sqlalchemy.orm import Session

from app.modules.catalog.models import Article, Category


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
        )
        for a in db.scalars(select(Article).where(Article.id.in_(ids)))
    }


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
