from app.modules.restaurant_kitchen.footprint import kitchen_footprint
from app.modules.restaurant_kitchen.permissions import (
    LINE_ROUTE,
    PRIORITY_SET,
    STATION_MANAGE,
    STATION_VIEW,
)
from app.modules.restaurant_kitchen.port import StationRouterService
from app.modules.restaurant_kitchen.router import router
from app.modules.restaurant_orders.api import register_station_router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W, A = AccessKind.READ, AccessKind.WRITE, AccessKind.ADMIN

register_station_router(StationRouterService)

# Palier R6 (ADR-0050) : postes de préparation d'un site, facultatifs (une commande n'exige jamais
# de poste, ADR-0049 D1). Dépend des commandes ; les commandes ne dépendent PAS de la cuisine
# (routage par un port déclaré par les commandes).
MANIFEST = ModuleManifest(
    code="restaurant.kitchen",
    # Livré (ADR-0050 K7, migration 0050) : disponible ; activations héritées de la période
    # « Bientôt disponible » remises à ``false`` sur les sites existants (activation explicite).
    depends_on=("restaurant.orders",),
    permissions=(
        PermissionDef(STATION_VIEW, R),
        PermissionDef(STATION_MANAGE, A),
        PermissionDef(LINE_ROUTE, W),
        PermissionDef(PRIORITY_SET, W),
    ),
    router=router,
    site_footprint=kitchen_footprint,
)
