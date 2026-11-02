from app.modules.reports.permissions import REPORT_EXPORT, REPORT_VIEW
from app.modules.reports.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, E = AccessKind.READ, AccessKind.EXPORT

# Palier R10 (ADR-0055) : livré (migration 0062 : activations héritées remises à ``false`` sur
# les sites existants, activation explicite par site). Consultation seule : les ventes, les
# paiements et le coût des sorties de stock restent la source unique (``sales.api``,
# ``stock.api``) ; libellés des articles et des clients (``catalog.api``, ``customers.api``).
MANIFEST = ModuleManifest(
    code="reports",
    depends_on=("catalog", "customers", "stock", "sales"),
    permissions=(
        PermissionDef(REPORT_VIEW, R),
        PermissionDef(REPORT_EXPORT, E),
    ),
    router=router,
)
