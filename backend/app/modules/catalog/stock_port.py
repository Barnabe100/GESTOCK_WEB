"""Port « stock » du module Catalogue (Lot 3-A, ADR-0039).

Passer un article de « géré en stock » à « non géré » exige un stock nul sur TOUS les sites du
tenant. Le catalogue ne dépend pas du stock (c'est le stock qui dépend du catalogue) : il
déclare ici l'interface dont il a besoin, que le module Stock implémente et enregistre au
chargement de son manifeste — aucune dépendance ``catalog → stock``, aucun cycle (même
principe que ``sales.cash_port``).
"""

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

# (session, tenant, article) → noms des sites où le stock de l'article n'est pas nul.
StockedSites = Callable[[Session, uuid.UUID, uuid.UUID], list[str]]
_stocked_sites: StockedSites | None = None


def register_stocked_sites(function: StockedSites) -> None:
    """Appelé par le module Stock à son chargement."""
    global _stocked_sites
    _stocked_sites = function


def sites_with_stock(db: Session, tenant_id: uuid.UUID, article_id: uuid.UUID) -> list[str]:
    """Sites où l'article a encore du stock (aucun si le module Stock n'est pas chargé : il n'y
    a alors aucun niveau de stock)."""
    if _stocked_sites is None:
        return []
    return _stocked_sites(db, tenant_id, article_id)
