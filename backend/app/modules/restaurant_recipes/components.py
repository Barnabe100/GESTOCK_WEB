"""Implémentations des ports déclarés par les autres modules (palier R8, ADR-0053) :

- Ventes (``components_port``, D7 / D8) : ingrédients des plats d'une vente, matérialisés dans
  ``restaurant_recipe_consumptions`` dans la transaction de la validation ;
- Menu (``availability_port``, D12) : éléments dont aucune unité n'est réalisable ;
- Catalogue (``recipe_port``, D2 / D3) : plats et ingrédients des recettes actives.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog.api import active_packagings, get_article_refs
from app.modules.restaurant_menu.availability_port import Presentation
from app.modules.restaurant_recipes.engine import (
    Applied,
    applicable,
    consumed,
    portions_possible,
    used_ingredient_presentations,
)
from app.modules.restaurant_recipes.models import Recipe, RecipeConsumption
from app.modules.restaurant_recipes.stock_view import site_assortment, site_stock
from app.modules.sales.api import COMPONENTS_MODULE, Component, SoldLine
from app.platform.context import RequestContext
from app.platform.tenancy.models import Tenant
from app.shared.ids import new_id

MODULE = COMPONENTS_MODULE  # « restaurant.recipes »


class RecipeComponents:
    """Fournisseur des ventes (enregistré par le manifeste)."""

    def __init__(self, db: Session, ctx: RequestContext, now: datetime) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now

    def components_for_sale(
        self, *, sale_id: uuid.UUID, site_id: uuid.UUID, lines: Sequence[SoldLine]
    ) -> list[Component]:
        # Recettes lues sous verrou PARTAGÉ : une désactivation ou une nouvelle version
        # concurrente attend la fin de la validation (version appliquée = version enregistrée).
        found = applicable(
            self.db,
            self.ctx.tenant_id,
            {(line.article_id, line.packaging_id) for line in lines},
            lock=True,
        )
        rows: list[RecipeConsumption] = []
        for line in lines:
            applied = found.get((line.article_id, line.packaging_id))
            if applied is None:
                continue
            composition = applied.composition
            portions = line.base_quantity if applied.via_base else line.quantity
            for ingredient in composition.ingredients:
                quantity = consumed(ingredient, portions, composition.yield_quantity)
                if quantity <= 0:
                    continue  # moins d'un millième : rien à sortir (arrondi, D4)
                rows.append(
                    RecipeConsumption(
                        id=new_id(),
                        tenant_id=self.ctx.tenant_id,
                        site_id=site_id,
                        sale_id=sale_id,
                        sale_line_id=line.line_id,
                        recipe_version_id=composition.version_id,
                        article_id=ingredient.article_id,
                        quantity=quantity,
                    )
                )
        if not rows:
            return []
        self.db.add_all(rows)
        self.db.flush()
        return [
            Component(
                id=row.id,
                line_id=row.sale_line_id,
                article_id=row.article_id,
                quantity=row.quantity,
            )
            for row in rows
        ]


def units_available(
    applied: Applied,
    site_id: uuid.UUID,
    conversion: Decimal,
    stock: dict[tuple[uuid.UUID, uuid.UUID], Decimal],
    assorted: set[tuple[uuid.UUID, uuid.UUID]],
) -> tuple[int, list[uuid.UUID]]:
    """Unités vendues réalisables sur le site et ingrédients limitants (D12). ``conversion`` :
    portions consommées par unité vendue (1 ou conversion du conditionnement, D5)."""
    composition = applied.composition
    per_unit = conversion if applied.via_base else Decimal("1")
    best: Decimal | None = None
    limiting: list[uuid.UUID] = []
    for ingredient in composition.ingredients:
        key = (site_id, ingredient.article_id)
        quantity = stock.get(key, Decimal("0")) if key in assorted else Decimal("0")
        possible = portions_possible(quantity, ingredient, composition.yield_quantity) // per_unit
        if best is None or possible < best:
            best, limiting = possible, [ingredient.article_id]
        elif possible == best:
            limiting.append(ingredient.article_id)
    return (int(best) if best is not None else 0), limiting


def _recipe_sites(db: Session, tenant: Tenant, site_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Sites où le module Recettes est EFFECTIF (profil, abonnement en écriture, activation)."""
    from app.platform.capabilities.service import CapabilityService
    from app.platform.registry import get_registry

    service = CapabilityService(db, get_registry())
    now = datetime.now(UTC)
    return {
        site_id
        for site_id in site_ids
        if MODULE in (service.public_site_modules(tenant, site_id, now) or frozenset())
    }


def short_menu_items(db: Session, tenant: Tenant, items: Sequence[Presentation]) -> set[uuid.UUID]:
    """Éléments du menu dont aucune unité n'est réalisable (port du menu, D12)."""
    sites = _recipe_sites(db, tenant, {item.site_id for item in items})
    candidates = [item for item in items if item.site_id in sites]
    if not candidates:
        return set()
    found = applicable(db, tenant.id, {(i.article_id, i.packaging_id) for i in candidates})
    candidates = [i for i in candidates if (i.article_id, i.packaging_id) in found]
    if not candidates:
        return set()
    ingredients = {
        ingredient.article_id
        for applied in found.values()
        for ingredient in applied.composition.ingredients
    }
    levels = site_stock(db, tenant.id, sites, ingredients)
    stock = {key: level.quantity for key, level in levels.items()}
    assorted = site_assortment(db, tenant.id, sites, ingredients)
    conversions = {
        p.id: p.conversion
        for packagings in active_packagings(
            db, {i.article_id for i in candidates}, priced_only=False
        ).values()
        for p in packagings
    }
    short: set[uuid.UUID] = set()
    for item in candidates:
        applied = found[(item.article_id, item.packaging_id)]
        conversion = (
            conversions.get(item.packaging_id, Decimal("1")) if item.packaging_id else Decimal("1")
        )
        units, _ = units_available(applied, item.site_id, conversion, stock, assorted)
        if units < 1:
            short.add(item.item_id)
    return short


def dish_recipe_references(db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID) -> list[str]:
    """Port du catalogue (D2) : recettes ACTIVES dont l'article est le plat."""
    count = db.scalar(
        select(Recipe.id)
        .where(
            Recipe.tenant_id == tenant_id,
            Recipe.article_id == article_id,
            Recipe.is_active.is_(True),
        )
        .limit(1)
    )
    if count is None:
        return []
    refs = get_article_refs(db, {article_id})
    return [refs[article_id].reference] if article_id in refs else []


def ingredient_recipe_references(
    db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID
) -> list[str]:
    """Port du catalogue (D3) : plats dont la recette active utilise l'article."""
    recipe_ids = used_ingredient_presentations(db, tenant_id, article_id)
    if not recipe_ids:
        return []
    dishes = set(db.scalars(select(Recipe.article_id).where(Recipe.id.in_(recipe_ids))))
    refs = get_article_refs(db, dishes)
    return sorted(ref.reference for ref in refs.values())
