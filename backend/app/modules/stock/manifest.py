from app.modules.stock.reasons import ensure_system_exit_reasons
from app.modules.stock.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W, A = AccessKind.READ, AccessKind.WRITE, AccessKind.ADMIN
TRANSFERS = "stock.transfers"

MANIFEST = ModuleManifest(
    code="stock",
    depends_on=("catalog",),
    permissions=(
        PermissionDef("stock.level.view", R),
        PermissionDef("stock.threshold.manage", W),
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
