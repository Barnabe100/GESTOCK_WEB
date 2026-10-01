"""Port « usages des conditionnements » du module Catalogue (Lot 3-B, étendu en 3-C).

La conversion d'un conditionnement déjà utilisé par une opération (vente, entrée, sortie,
transfert, comptage d'inventaire — brouillons compris) est figée. Le catalogue ne dépend
d'aucun de ces modules (ce sont eux qui dépendent du catalogue) : il déclare ici l'interface
dont il a besoin, que chaque module utilisateur implémente et enregistre au chargement de son
manifeste — aucune dépendance ``catalog → ventes / stock``, aucun cycle (même principe que
``catalog.stock_port``).
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

# (session, conditionnements) → ceux qu'au moins une ligne d'opération (brouillon compris)
# utilise.
PackagingUsage = Callable[[Session, set[uuid.UUID]], set[uuid.UUID]]
_providers: dict[str, PackagingUsage] = {}


def register_packaging_usage(name: str, function: PackagingUsage) -> None:
    """Appelé par chaque module utilisateur (ventes, stock, inventaires) à son chargement ;
    ``name`` rend l'enregistrement idempotent."""
    _providers[name] = function


def packagings_in_use(db: Session, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Conditionnements utilisés par au moins une opération (aucun si aucun module utilisateur
    n'est chargé : aucune ligne ne peut alors exister)."""
    used: set[uuid.UUID] = set()
    if not ids:
        return used
    for provider in _providers.values():
        used |= provider(db, ids - used)
        if used >= ids:
            break
    return used
