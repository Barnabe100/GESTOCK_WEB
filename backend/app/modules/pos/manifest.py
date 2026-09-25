from app.modules.pos.router import router
from app.platform.registry import AccessKind, ModuleManifest, PermissionDef

W = AccessKind.WRITE

MANIFEST = ModuleManifest(
    code="pos",
    # Interface au-dessus des services existants : ventes (checkout, paiements), catalogue
    # (prix), stock (niveaux, sites). La caisse n'est PAS requise : seules les espèces en ont
    # besoin (port ``sales.cash_port``).
    depends_on=("sales", "catalog", "stock"),
    permissions=(
        # Utiliser le point de vente. N'ouvre aucun droit de vente : l'encaissement exige aussi
        # sales.sale.create, sales.sale.validate (et sales.payment.create pour un paiement).
        PermissionDef("pos.terminal.use", W),
    ),
    router=router,
)
