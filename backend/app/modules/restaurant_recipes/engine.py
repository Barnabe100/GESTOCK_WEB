"""Calculs des recettes (palier R8, ADR-0053) — partagés par l'API, la validation des ventes
(composants) et le menu (disponibilité). Lecture seule ; aucune écriture ici.

- Recette applicable à une présentation vendue (D5) : celle du conditionnement si elle existe,
  sinon celle de l'unité de base, appliquée à la quantité DE BASE (``portions_per_unit`` =
  conversion du conditionnement).
- Consommation (D4) : quantité × portions ÷ rendement, arrondie au millième (demi supérieur).
- Portions réalisables (D12) : ⌊stock × rendement ÷ quantité⌋, minimum sur les ingrédients.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_recipes.models import Recipe, RecipeLine, RecipeVersion

QUANTITY_STEP = Decimal("0.001")
COST_STEP = Decimal("0.0001")
MONEY_STEP = Decimal("0.01")

# (article, conditionnement ou ``None`` = unité de base)
PresentationKey = tuple[uuid.UUID, uuid.UUID | None]


@dataclass(frozen=True)
class Ingredient:
    article_id: uuid.UUID
    quantity: Decimal  # pour le rendement de la version, en unité de base de l'ingrédient
    note: str | None


@dataclass(frozen=True)
class Composition:
    recipe_id: uuid.UUID
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None
    version_id: uuid.UUID
    version_number: int
    yield_quantity: Decimal
    instructions: str | None
    ingredients: tuple[Ingredient, ...]


@dataclass(frozen=True)
class Applied:
    """Recette applicable à une présentation vendue et portions produites par unité vendue."""

    composition: Composition
    # 1 : recette propre de la présentation ; conversion : recette de l'unité de base appliquée
    # à un conditionnement (D5).
    via_base: bool


def round_quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)


def consumed(ingredient: Ingredient, portions: Decimal, yield_quantity: Decimal) -> Decimal:
    """Quantité d'ingrédient pour ``portions`` (D4), arrondie au millième."""
    return round_quantity(ingredient.quantity * portions / yield_quantity)


def portions_possible(stock: Decimal, ingredient: Ingredient, yield_quantity: Decimal) -> Decimal:
    """Portions entières réalisables avec ``stock`` de cet ingrédient (jamais négatif)."""
    if stock <= 0:
        return Decimal("0")
    return (stock * yield_quantity / ingredient.quantity).to_integral_value(rounding=ROUND_FLOOR)


def compositions(
    db: Session,
    tenant_id: uuid.UUID,
    keys: Iterable[PresentationKey],
    *,
    active_only: bool = True,
    lock: bool = False,
) -> dict[PresentationKey, Composition]:
    """Version COURANTE des recettes de ces présentations (exactement ces clés). ``lock`` :
    recettes lues sous verrou PARTAGÉ (validation d'une vente : une désactivation ou une
    nouvelle version concurrente attend la fin de la transaction)."""
    wanted = set(keys)
    if not wanted:
        return {}
    stmt = (
        select(Recipe, RecipeVersion)
        .join(
            RecipeVersion,
            (RecipeVersion.tenant_id == Recipe.tenant_id)
            & (RecipeVersion.id == Recipe.current_version_id),
        )
        .where(
            Recipe.tenant_id == tenant_id,
            Recipe.article_id.in_({article for article, _ in wanted}),
        )
        .order_by(Recipe.id)
    )
    if active_only:
        stmt = stmt.where(Recipe.is_active.is_(True))
    if lock:
        stmt = stmt.with_for_update(read=True, of=Recipe)
    rows = [
        (recipe, version)
        for recipe, version in db.execute(stmt).tuples()
        if (recipe.article_id, recipe.packaging_id) in wanted
    ]
    if not rows:
        return {}
    lines: dict[uuid.UUID, list[Ingredient]] = {}
    for line in db.scalars(
        select(RecipeLine)
        .where(
            RecipeLine.tenant_id == tenant_id,
            RecipeLine.version_id.in_({version.id for _, version in rows}),
        )
        .order_by(RecipeLine.version_id, RecipeLine.line_no)
    ):
        lines.setdefault(line.version_id, []).append(
            Ingredient(article_id=line.article_id, quantity=line.quantity, note=line.note)
        )
    return {
        (recipe.article_id, recipe.packaging_id): Composition(
            recipe_id=recipe.id,
            article_id=recipe.article_id,
            packaging_id=recipe.packaging_id,
            version_id=version.id,
            version_number=version.number,
            yield_quantity=version.yield_quantity,
            instructions=version.instructions,
            ingredients=tuple(lines.get(version.id, ())),
        )
        for recipe, version in rows
    }


def applicable(
    db: Session,
    tenant_id: uuid.UUID,
    keys: Iterable[PresentationKey],
    *,
    lock: bool = False,
) -> dict[PresentationKey, Applied]:
    """Recette ACTIVE applicable à chaque présentation (D5) ; absente : aucune recette."""
    wanted = set(keys)
    lookup = wanted | {(article, None) for article, packaging in wanted if packaging is not None}
    found = compositions(db, tenant_id, lookup, lock=lock)
    result: dict[PresentationKey, Applied] = {}
    for key in wanted:
        own = found.get(key)
        if own is not None:
            result[key] = Applied(composition=own, via_base=False)
        elif key[1] is not None and (key[0], None) in found:
            result[key] = Applied(composition=found[(key[0], None)], via_base=True)
    return result


def used_ingredient_presentations(
    db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID
) -> list[uuid.UUID]:
    """Recettes ACTIVES dont la version courante utilise ``article_id`` comme ingrédient."""
    return list(
        db.scalars(
            select(Recipe.id)
            .join(
                RecipeLine,
                (RecipeLine.tenant_id == Recipe.tenant_id)
                & (RecipeLine.version_id == Recipe.current_version_id),
            )
            .where(
                Recipe.tenant_id == tenant_id,
                Recipe.is_active.is_(True),
                RecipeLine.article_id == article_id,
            )
        )
    )
