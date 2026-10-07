from app.modules.catalog.api import (
    register_assortment_removal_check,
    register_lot_flags_check,
    register_lot_stocked_sites,
    register_packaging_usage,
    register_stocked_sites,
)
from app.modules.stock.assortment_checks import assortment_removal_check
from app.modules.stock.document_service import packagings_used
from app.modules.stock.level_service import stocked_sites
from app.modules.stock.lot_flags_checks import lot_flags_check
from app.modules.stock.lot_service import lot_stocked_sites
from app.modules.stock.reasons import ensure_system_exit_reasons
from app.modules.stock.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W, A = AccessKind.READ, AccessKind.WRITE, AccessKind.ADMIN
TRANSFERS = "stock.transfers"

# Port du catalogue (Lot 3-A) : stock restant d'un article, sur tous les sites du tenant.
register_stocked_sites(stocked_sites)
# Lot 3-G : le suivi par lot ne se désactive qu'avec des soldes de lots tous nuls (D7).
register_lot_stocked_sites(lot_stocked_sites)
# Lot 3-C : un conditionnement saisi sur une entrée, une sortie ou un transfert (brouillon
# compris) a sa conversion figée.
register_packaging_usage("stock", packagings_used)
# Lot 3-H : changement de suivi par lot refusé s'il rendait un document incohérent.
register_lot_flags_check("stock", lot_flags_check)
# Assortiment (ADR-0046, D4) : retrait refusé si stock / lots non nuls ou brouillons du site.
register_assortment_removal_check("stock", assortment_removal_check)

MANIFEST = ModuleManifest(
    code="stock",
    depends_on=("catalog",),
    permissions=(
        PermissionDef("stock.level.view", R),
        PermissionDef("stock.threshold.manage", W),
        # Lot 3-F (ADR-0044) : emplacements d'un site et emplacement des articles.
        PermissionDef("stock.location.manage", W),
        PermissionDef("stock.movement.view", R),
        PermissionDef("stock.entry.view", R),
        PermissionDef("stock.entry.create", W),
        PermissionDef("stock.entry.update", W),
        PermissionDef("stock.entry.validate", W),
        PermissionDef("stock.entry.cancel", W),
        PermissionDef("stock.exit.view", R),
        PermissionDef("stock.exit.create", W),
        PermissionDef("stock.exit.update", W),
        PermissionDef("stock.exit.validate", W),
        PermissionDef("stock.exit.cancel", W),
        PermissionDef("stock.reason.view", R),
        # Motifs de sortie : réservés à l'administration (SOR-05).
        PermissionDef("stock.reason.manage", A),
        # Transferts inter-sites (Phase 2.5, ADR-0018). Consultation toujours possible : un
        # tenant revenu à un plan sans ``stock.transfers`` garde l'accès à son historique.
        # Opérations : accordées seulement si le plan inclut la fonctionnalité.
        PermissionDef("stock.transfer.view", R),
        PermissionDef("stock.transfer.create", W, feature=TRANSFERS),
        PermissionDef("stock.transfer.update", W, feature=TRANSFERS),
        PermissionDef("stock.transfer.validate", W, feature=TRANSFERS),
        PermissionDef("stock.transfer.cancel", W, feature=TRANSFERS),
    ),
    # Fonctionnalités activables par plan (données : plans.toml).
    features=(TRANSFERS,),
    tenant_setup=ensure_system_exit_reasons,
    router=router,
)
