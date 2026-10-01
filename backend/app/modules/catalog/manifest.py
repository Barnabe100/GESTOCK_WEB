from app.modules.catalog.onboarding import CATALOG_STEPS
from app.modules.catalog.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

R, W = AccessKind.READ, AccessKind.WRITE

MANIFEST = ModuleManifest(
    code="catalog",
    permissions=(
        PermissionDef("catalog.category.view", R),
        PermissionDef("catalog.category.create", W),
        PermissionDef("catalog.category.update", W),
        PermissionDef("catalog.category.status", W),
        PermissionDef("catalog.article.view", R),
        PermissionDef("catalog.article.create", W),
        # Informations générales de l'article ; ne permet PAS de modifier les prix (Lot 3-A).
        PermissionDef("catalog.article.update", W),
        PermissionDef("catalog.article.status", W),
        # Lot 3-A (ADR-0039) : prix catalogue (vente, achat) et coûts internes (prix d'achat,
        # coût moyen pondéré), contrôlés par le serveur dans chaque réponse.
        PermissionDef("catalog.article.price_update", W),
        PermissionDef("catalog.article.cost_view", R),
    ),
    router=router,
    onboarding=CATALOG_STEPS,
)
