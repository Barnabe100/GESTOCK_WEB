from app.modules.restaurant_qr.footprint import qr_footprint
from app.modules.restaurant_qr.permissions import POINT_MANAGE, POINT_VIEW, SETTINGS_MANAGE
from app.modules.restaurant_qr.router import router
from app.platform.registry import AccessKind, ModuleManifest, ModuleStatus, PermissionDef

R, A = AccessKind.READ, AccessKind.ADMIN

# Palier R7 (ADR-0052) : commandes par QR Code d'un site. Dépend des commandes (une commande QR
# est une commande du moteur unique, canal ``QR``) ; les tables sont facultatives (point de
# comptoir sans table). PLANIFIÉ pendant son développement (routes non montées en production,
# permissions non accordées) ; livré par la migration 0058.
MANIFEST = ModuleManifest(
    code="restaurant.qr",
    status=ModuleStatus.PLANNED,
    depends_on=("restaurant.orders",),
    permissions=(
        PermissionDef(POINT_VIEW, R),
        PermissionDef(POINT_MANAGE, A),
        PermissionDef(SETTINGS_MANAGE, A),
    ),
    router=router,
    site_footprint=qr_footprint,
)
