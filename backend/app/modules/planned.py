"""Modules métier déclarés mais NON implémentés (statut ``planned``).

Ils existent dans le registre pour que les profils d'activité et les plans puissent les
référencer dès maintenant. Chaque module recevra son propre paquet (manifest, router, service,
models…) et ses permissions lorsqu'il sera développé, après validation de la phase concernée.
"""

from app.platform.registry import ModuleManifest, ModuleStatus


def _planned(code: str, *depends_on: str) -> ModuleManifest:
    return ModuleManifest(code=code, status=ModuleStatus.PLANNED, depends_on=depends_on)


PLANNED_MODULES: tuple[ModuleManifest, ...] = (
    # Core commercial (V1)
    # Paiements électroniques : intégrations fournisseurs (Mobile Money, TPE, banques). Les
    # encaissements des ventes font partie du module ``sales`` depuis la Phase 2.7 (ADR-0020),
    # la caisse (``cash_register``) depuis la Phase 2.9 (ADR-0022), le point de vente (``pos``)
    # depuis la Phase 3.0 (ADR-0023).
    _planned("payments", "sales"),
    _planned("reports"),
    # Restauration (V2)
    _planned("restaurant.menu", "catalog"),
    _planned("restaurant.tables"),
    _planned("restaurant.orders", "restaurant.menu", "restaurant.tables"),
    _planned("restaurant.kitchen", "restaurant.orders"),
    _planned("restaurant.qr", "restaurant.orders"),
    _planned("restaurant.recipes", "catalog", "stock"),
    # Automobile (profils d'activité de la Phase 3.1 ; hors plans tant qu'ils sont planifiés)
    _planned("automobile.vehicles", "customers"),
    _planned("automobile.workshop", "catalog", "stock", "customers", "automobile.vehicles"),
)
