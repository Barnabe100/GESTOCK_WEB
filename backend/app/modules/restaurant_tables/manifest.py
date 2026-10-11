from app.modules.restaurant_orders.api import register_table_directory
from app.modules.restaurant_tables.footprint import tables_footprint
from app.modules.restaurant_tables.permissions import TABLE_MANAGE, TABLE_VIEW
from app.modules.restaurant_tables.port import TableDirectoryService
from app.modules.restaurant_tables.router import router
from app.platform.registry import AccessKind, ModuleManifest, ModuleStatus, PermissionDef

R, A = AccessKind.READ, AccessKind.ADMIN

# Port « tables » des commandes (R5-B) : enregistré même si le module est planifié ; les
# commandes vérifient qu'il est EFFECTIF (capacités recalculées) avant tout rattachement.
register_table_directory(TableDirectoryService)

# Palier R5-A (ADR-0049) : zones et tables d'un site, facultatives (une commande n'exige jamais
# de table, D2). Dépend des commandes (D1) ; les commandes ne dépendent PAS des tables (le
# rattachement d'une table à une commande passera par un port déclaré par les commandes, R5-B).
MANIFEST = ModuleManifest(
    code="restaurant.tables",
    # A2 : PLANIFIÉ dans le registre de production tant que ``restaurant.orders`` l'est —
    # routes non montées, activation refusée (``module_not_implemented``), aucune permission
    # accordée ; les tests utilisent un registre de test.
    status=ModuleStatus.PLANNED,
    depends_on=("restaurant.orders",),
    permissions=(
        PermissionDef(TABLE_VIEW, R),
        # Zones et tables : configuration du site.
        PermissionDef(TABLE_MANAGE, A),
    ),
    router=router,
    # ``/restaurant/tables`` (tables) et ``/restaurant/tables/zones`` (zones).
    site_footprint=tables_footprint,
)
