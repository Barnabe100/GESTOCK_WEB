"""Port « composants » du module Ventes (palier R8, ADR-0053 D7 / D8).

Un article NON géré en stock (plat) peut consommer des ingrédients gérés en stock à la
validation de la vente. Les Ventes ne dépendent pas des Recettes : elles déclarent ici ce dont
elles ont besoin ; le module Recettes (``restaurant.recipes``, qui dépend des ventes)
l'implémente et l'enregistre au chargement — aucune dépendance ``sales → restaurant_recipes``,
aucun cycle (même principe que ``cash_port``).

- Module non effectif sur le site de la vente, ou non chargé : aucun composant ; la vente se
  comporte exactement comme avant.
- Composants : un par couple (ligne de vente, ingrédient), MATÉRIALISÉ par le fournisseur dans
  la transaction de la validation ; son identifiant est la ``source_line_id`` du mouvement de
  stock (O10 : la garde d'unicité des mouvements reste inchangée).
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from app.platform.context import RequestContext

# Code du module qui fournit les composants (test de capacité, comme ``require_module``).
COMPONENTS_MODULE = "restaurant.recipes"


@dataclass(frozen=True)
class SoldLine:
    """Ligne de vente d'un article non géré en stock (présentation vendue)."""

    line_id: uuid.UUID
    article_id: uuid.UUID
    packaging_id: uuid.UUID | None
    quantity: Decimal  # dans la présentation vendue
    base_quantity: Decimal  # en unité de base de l'article vendu


@dataclass(frozen=True)
class Component:
    """Ingrédient à sortir du stock pour une ligne (quantité positive, unité de base)."""

    id: uuid.UUID
    line_id: uuid.UUID
    article_id: uuid.UUID
    quantity: Decimal


class ComponentProvider(Protocol):
    def components_for_sale(
        self, *, sale_id: uuid.UUID, site_id: uuid.UUID, lines: Sequence[SoldLine]
    ) -> list[Component]: ...


ComponentProviderFactory = Callable[[Session, RequestContext, datetime], ComponentProvider]
_factory: ComponentProviderFactory | None = None


def register_component_provider(factory: ComponentProviderFactory) -> None:
    """Appelé par le module Recettes à son chargement."""
    global _factory
    _factory = factory


def site_modules(ctx: RequestContext, site_id: uuid.UUID) -> frozenset[str]:
    """Modules EFFECTIFS du site de la vente (capacités résolues pour CE site)."""
    if ctx.site is not None and ctx.site.id == site_id:
        return frozenset(ctx.capabilities.modules)
    return frozenset(ctx.site_capabilities(site_id).modules)


def component_provider(
    db: Session, ctx: RequestContext, now: datetime, site_id: uuid.UUID
) -> ComponentProvider | None:
    """Fournisseur de composants pour une vente du site, ou ``None`` (module non effectif)."""
    if _factory is None or COMPONENTS_MODULE not in site_modules(ctx, site_id):
        return None
    return _factory(db, ctx, now)
