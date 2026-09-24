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
    _planned("customers"),
    _planned("stock", "catalog"),
    _planned("inventory_count", "stock"),
    _planned("sales", "catalog"),
    _planned("payments", "sales"),
    _planned("cash_register", "payments"),
    _planned("pos", "sales", "payments", "cash_register"),
    _planned("reports"),
    _planned("alerts", "stock"),
    # Restauration (V2)
    _planned("restaurant.menu", "catalog"),
    _planned("restaurant.tables"),
    _planned("restaurant.orders", "restaurant.menu", "restaurant.tables"),
    _planned("restaurant.kitchen", "restaurant.orders"),
    _planned("restaurant.qr", "restaurant.orders"),
    _planned("restaurant.recipes", "catalog", "stock"),
)
