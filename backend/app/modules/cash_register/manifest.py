from app.modules.cash_register.router import router
from app.modules.cash_register.service import CashService
from app.modules.sales.api import register_cash_ledger
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

# Paiements espèces : la caisse implémente le port des ventes (la vente ne dépend pas d'elle).
register_cash_ledger(CashService)

R, W = AccessKind.READ, AccessKind.WRITE
P = "cash_register"

MANIFEST = ModuleManifest(
    code="cash_register",
    # Ventes : encaissements espèces des paiements (port ``sales.cash_port``, FK vers
    # ``payments``) ; stock : périmètre des sites (stock.api).
    depends_on=("sales", "stock"),
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
