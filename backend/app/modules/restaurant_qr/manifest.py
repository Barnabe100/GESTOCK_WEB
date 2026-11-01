from app.modules.restaurant_qr.footprint import qr_footprint
from app.modules.restaurant_qr.permissions import POINT_MANAGE, POINT_VIEW, SETTINGS_MANAGE
from app.modules.restaurant_qr.public_router import public_router
from app.modules.restaurant_qr.router import router
from app.modules.restaurant_qr.settings import site_setup
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
    # ``/api/v1/public/restaurant/qr`` : menu, commande et suivi publics (jeton vérifié).
    public_router=public_router,
    site_footprint=qr_footprint,
    # Réglages QR du site créés à l'activation du module (jamais écrasés).
    site_setup=site_setup,
)
