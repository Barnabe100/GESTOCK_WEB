"""API publique du module Catalogue pour les autres modules (stock, ventes…). Ils n'importent
jamais ses modèles directement (règle d'architecture 10)."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Subquery, select
from sqlalchemy.orm import Session

from app.modules.catalog.models import Article, Category, Packaging
from app.modules.catalog.sales_port import register_packaging_usage
from app.modules.catalog.stock_port import register_stocked_sites

__all__ = [
    "ArticleRef",
    "PackagingRef",
    "active_packagings",
    "articles_view",
    "find_active_article_by_barcode",
    "get_article_refs",
    "is_whole",
    "lock_packagings",
    "lock_stock_managed",
    "register_packaging_usage",
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
    # Lot 3-B : ``False`` = quantités vendues entières seulement.
    decimal_quantity_allowed: bool = False


@dataclass(frozen=True)
class PackagingRef:
    """Conditionnement de vente (Lot 3-B) : quantité de base = quantité × ``conversion``."""

    id: uuid.UUID
    article_id: uuid.UUID
    name: str
    conversion: Decimal
    sale_price: Decimal
    is_active: bool


def is_whole(value: Decimal) -> bool:
    """Quantité entière (2, 2.000) — jamais de ``float``."""
    return value == value.to_integral_value()


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
            decimal_quantity_allowed=a.decimal_quantity_allowed,
        )
        for a in db.scalars(select(Article).where(Article.id.in_(ids)))
    }


def _packaging_ref(p: Packaging) -> PackagingRef:
    return PackagingRef(
        id=p.id,
        article_id=p.article_id,
        name=p.name,
        conversion=p.conversion,
        sale_price=p.sale_price,
        is_active=p.is_active,
    )


def lock_packagings(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, PackagingRef]:
    """Conditionnements du tenant (RLS), lus sous verrou PARTAGÉ (``FOR SHARE``, ordre des
    identifiants) jusqu'à la fin de la transaction : la modification de la conversion prend le
    verrou exclusif puis vérifie qu'aucune vente n'utilise le conditionnement — une vente en
    cours se termine d'abord (elle est alors vue), aucune ne lit une conversion en cours de
    modification. Plusieurs ventes simultanées ne s'attendent pas entre elles."""
    if not ids:
        return {}
    rows = db.scalars(
        select(Packaging)
        .where(Packaging.id.in_(ids))
        .order_by(Packaging.id)
        .with_for_update(read=True)
    )
    return {p.id: _packaging_ref(p) for p in rows}


def active_packagings(
    db: Session, article_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[PackagingRef]]:
    """Conditionnements ACTIFS des articles (point de vente), triés par conversion puis nom."""
    if not article_ids:
        return {}
    result: dict[uuid.UUID, list[PackagingRef]] = {}
    for p in db.scalars(
        select(Packaging)
        .where(Packaging.article_id.in_(article_ids), Packaging.is_active.is_(True))
        .order_by(Packaging.conversion, Packaging.name, Packaging.id)
    ):
        result.setdefault(p.article_id, []).append(_packaging_ref(p))
    return result


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
