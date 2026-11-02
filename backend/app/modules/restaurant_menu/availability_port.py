"""Port « disponibilité » du menu (palier R8, ADR-0053 D12).

Un élément du menu peut ne plus être commandable faute d'ingrédients (recette du plat). Le menu
ne dépend pas des Recettes : il déclare ici l'interface, que le module Recettes implémente et
enregistre au chargement (aucune dépendance ``restaurant_menu → restaurant_recipes``). Sans
implémentation, ou sans module Recettes effectif sur le site, aucun motif n'est ajouté.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.platform.tenancy.models import Tenant

INGREDIENTS_UNAVAILABLE = "ingredients_unavailable"


@dataclass(frozen=True)
class Presentation:
    item_id: uuid.UUID
    site_id: uuid.UUID
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None


# (session, entreprise, éléments) → éléments sans aucune portion réalisable.
ShortItems = Callable[[Session, Tenant, Sequence[Presentation]], set[uuid.UUID]]
_short_items: ShortItems | None = None


def register_short_items(function: ShortItems) -> None:
    """Appelé par le module Recettes à son chargement."""
    global _short_items
    _short_items = function


def short_items(
    db: Session, tenant: Tenant | None, items: Sequence[Presentation]
) -> set[uuid.UUID]:
    if _short_items is None or tenant is None or not items:
        return set()
    return _short_items(db, tenant, items)
