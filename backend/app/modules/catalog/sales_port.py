"""Port « ventes » du module Catalogue (Lot 3-B, ADR-0040).

La conversion d'un conditionnement déjà utilisé par une vente est figée. Le catalogue ne
dépend pas des ventes (ce sont les ventes qui dépendent du catalogue) : il déclare ici
l'interface dont il a besoin, que le module Ventes implémente et enregistre au chargement de
son manifeste — aucune dépendance ``catalog → sales``, aucun cycle (même principe que
``catalog.stock_port``).
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

# (session, conditionnements) → ceux qu'au moins une ligne de vente (brouillon compris) utilise.
PackagingUsage = Callable[[Session, set[uuid.UUID]], set[uuid.UUID]]
_packaging_used: PackagingUsage | None = None


def register_packaging_usage(function: PackagingUsage) -> None:
    """Appelé par le module Ventes à son chargement."""
    global _packaging_used
    _packaging_used = function


def packagings_in_use(db: Session, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Conditionnements qui figurent sur une vente (aucun si le module Ventes n'est pas chargé :
    aucune ligne de vente ne peut alors exister)."""
    if _packaging_used is None or not ids:
        return set()
    return _packaging_used(db, ids)
