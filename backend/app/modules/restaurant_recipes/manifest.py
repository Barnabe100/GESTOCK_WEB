from app.modules.catalog.api import register_recipe_usage
from app.modules.restaurant_menu.availability_port import register_short_items
from app.modules.restaurant_recipes.components import (
    RecipeComponents,
    dish_recipe_references,
    ingredient_recipe_references,
    short_menu_items,
)
from app.modules.restaurant_recipes.footprint import recipes_footprint
from app.modules.restaurant_recipes.permissions import RECIPE_MANAGE, RECIPE_VIEW
from app.modules.restaurant_recipes.router import router
from app.modules.sales.api import register_component_provider
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, A = AccessKind.READ, AccessKind.ADMIN

# Ports implémentés (ADR-0053) : ventes (ingrédients à la validation), menu (disponibilité),
# catalogue (plat non géré / ingrédient géré). Aucun de ces modules ne dépend des Recettes.
register_component_provider(RecipeComponents)
register_short_items(short_menu_items)
register_recipe_usage(dish_recipe_references, ingredient_recipe_references)

# Palier R8 (ADR-0053) : livré (migration 0060 : activations héritées remises à ``false`` sur
# les sites existants, activation explicite par site). Dépend du menu, dont il implémente le
# port de disponibilité (comme la cuisine pour les commandes).
MANIFEST = ModuleManifest(
    code="restaurant.recipes",
    depends_on=("catalog", "stock", "sales", "restaurant.menu"),
    permissions=(
        PermissionDef(RECIPE_VIEW, R),
        PermissionDef(RECIPE_MANAGE, A),
    ),
    router=router,
    site_footprint=recipes_footprint,
)
