"""Modules métier (catalogue, stock, ventes, caisse, restaurant, ...).

Chaque module est un paquet autonome qui déclare un manifeste (code, dépendances,
permissions) et n'est exposé que si la capacité correspondante est active pour le tenant.
Voir docs/architecture/ARCHITECTURE.md (section 5).
"""

from app.modules.alerts.manifest import MANIFEST as ALERTS
from app.modules.catalog.manifest import MANIFEST as CATALOG
from app.modules.customers.manifest import MANIFEST as CUSTOMERS
from app.modules.planned import PLANNED_MODULES
from app.modules.stock.manifest import MANIFEST as STOCK
from app.modules.suppliers.manifest import MANIFEST as SUPPLIERS
from app.platform.registry import ModuleManifest

# Registre explicite : modules implémentés, puis modules seulement planifiés.
BUSINESS_MODULES: tuple[ModuleManifest, ...] = (
    CATALOG,
    SUPPLIERS,
    CUSTOMERS,
    STOCK,
    ALERTS,
    *PLANNED_MODULES,
)
