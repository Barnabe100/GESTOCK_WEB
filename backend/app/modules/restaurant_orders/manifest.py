from app.modules.catalog.api import (
    register_assortment_removal_check,
    register_lot_flags_check,
    register_packaging_usage,
)
from app.modules.restaurant_orders.footprint import (
    assortment_removal_check,
    lot_flags_check,
    orders_footprint,
    packagings_used,
)
from app.modules.restaurant_orders.permissions import (
    ORDER_CANCEL,
    ORDER_CANCEL_PREPARED,
    ORDER_CREATE,
    ORDER_PREPARE,
    ORDER_SERVE,
    ORDER_VIEW,
    SETTINGS_MANAGE,
)
from app.modules.restaurant_orders.router import router
from app.modules.restaurant_orders.settings import site_setup
from app.platform.registry import AccessKind, ModuleManifest, ModuleStatus, PermissionDef

R, W, A = AccessKind.READ, AccessKind.WRITE, AccessKind.ADMIN

# Ports du catalogue : une commande non finale est un document ouvert (assortiment, suivi par
# lot) ; une ligne de commande fige la conversion de son conditionnement.
register_packaging_usage("restaurant.orders", packagings_used)
register_lot_flags_check("restaurant.orders", lot_flags_check)
register_assortment_removal_check("restaurant.orders", assortment_removal_check)

MANIFEST = ModuleManifest(
    code="restaurant.orders",
    # N1 (ADR-0049, D14) : le module reste PLANIFIÉ dans le registre de production jusqu'au
    # commit R2-E (règlement et interface livrés) — routes non montées, activation refusée
    # (``module_not_implemented``) ; les tests intermédiaires utilisent un registre de test.
    status=ModuleStatus.PLANNED,
    # Menu : lignes issues du menu du site. Ventes : règlement (R2-D). Tables, postes, caisse,
    # clients, POS : enrichissements facultatifs, testés par capacité (D1).
    depends_on=("restaurant.menu", "sales"),
    permissions=(
        PermissionDef(ORDER_VIEW, R),
        PermissionDef(ORDER_CREATE, W),
        PermissionDef(ORDER_PREPARE, W),
        PermissionDef(ORDER_SERVE, W),
        # Lignes reçues ; commandes (non réglées, aucune ligne servie).
        PermissionDef(ORDER_CANCEL, W),
        # Lignes en préparation ou prêtes : réservé à l'encadrement.
        PermissionDef(ORDER_CANCEL_PREPARED, A),
        PermissionDef(SETTINGS_MANAGE, A),
    ),
    router=router,
    # ``/restaurant/orders`` et ``/restaurant/settings/{site_id}``.
    route_prefix="/restaurant",
    site_footprint=orders_footprint,
    site_setup=site_setup,
)
