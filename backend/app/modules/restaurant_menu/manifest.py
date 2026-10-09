from app.modules.restaurant_menu.footprint import menu_footprint
from app.modules.restaurant_menu.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W, A = AccessKind.READ, AccessKind.WRITE, AccessKind.ADMIN

# Palier R1 (ADR-0049) : menu par site. Dépend du seul catalogue (assortiment par site compris,
# ADR-0046) ; aucun lien avec le stock, les ventes ni le point de vente.
MANIFEST = ModuleManifest(
    code="restaurant.menu",
    depends_on=("catalog",),
    permissions=(
        PermissionDef("restaurant.menu.view", R),
        # Sections, éléments, ordre, activation : configuration du site.
        PermissionDef("restaurant.menu.manage", A),
        # « Épuisé » manuel : geste d'exploitation, sans configuration.
        PermissionDef("restaurant.menu.availability", W),
    ),
    router=router,
    site_footprint=menu_footprint,
)
