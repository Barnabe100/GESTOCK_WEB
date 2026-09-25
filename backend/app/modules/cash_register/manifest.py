from app.modules.cash_register.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE
P = "cash_register"

MANIFEST = ModuleManifest(
    code="cash_register",
    # Stock : périmètre des sites (stock.api). Aucune dépendance aux ventes : c'est le module
    # Ventes qui enregistre ses encaissements espèces via l'API publique de la caisse.
    depends_on=("stock",),
    permissions=(
        PermissionDef(f"{P}.register.view", R),
        PermissionDef(f"{P}.register.manage", W),  # créer, modifier, activer / désactiver
        PermissionDef(f"{P}.session.view", R),  # sessions et journal
        PermissionDef(f"{P}.session.open", W),
        PermissionDef(f"{P}.session.close", W),  # comptage et clôture
        PermissionDef(f"{P}.movement.create", W),  # entrées et sorties manuelles
    ),
    router=router,
    route_prefix="/cash",
)
