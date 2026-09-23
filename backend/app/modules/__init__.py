"""Modules métier (catalogue, stock, ventes, caisse, restaurant, ...).

Chaque module est un paquet autonome qui déclare un manifeste (code, dépendances,
permissions) et n'est exposé que si la capacité correspondante est active pour le tenant.
Voir docs/architecture/ARCHITECTURE.md (section 5).
"""

from app.modules.planned import PLANNED_MODULES
from app.platform.registry import ModuleManifest

BUSINESS_MODULES: tuple[ModuleManifest, ...] = PLANNED_MODULES
