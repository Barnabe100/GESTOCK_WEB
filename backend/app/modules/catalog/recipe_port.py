"""Port « recettes » du module Catalogue (palier R8, ADR-0053 D2 / D3).

Aucune double déduction : l'article d'une recette ACTIVE reste non géré en stock, et un
ingrédient d'une recette active reste géré en stock. Le catalogue ne dépend pas des Recettes :
il déclare ici l'interface, que le module Recettes implémente et enregistre au chargement
(aucune dépendance ``catalog → restaurant_recipes``, aucun cycle).
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

# (session, tenant, article) → références des plats dont la recette ACTIVE concerne l'article
# (comme plat, ou comme ingrédient de la version courante).
RecipeUsage = Callable[[Session, uuid.UUID, uuid.UUID], list[str]]
_dish_recipes: RecipeUsage | None = None
_ingredient_recipes: RecipeUsage | None = None


def register_recipe_usage(dish: RecipeUsage, ingredient: RecipeUsage) -> None:
    """Appelé par le module Recettes à son chargement."""
    global _dish_recipes, _ingredient_recipes
    _dish_recipes, _ingredient_recipes = dish, ingredient


def dish_recipes(db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID) -> list[str]:
    """Recettes actives dont l'article est le plat (aucune sans module Recettes)."""
    return [] if _dish_recipes is None else _dish_recipes(db, tenant_id, article_id)


def ingredient_recipes(db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID) -> list[str]:
    """Plats dont la recette active utilise l'article comme ingrédient."""
    return [] if _ingredient_recipes is None else _ingredient_recipes(db, tenant_id, article_id)
