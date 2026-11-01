"""Port « postes de préparation » des commandes (palier R6, ADR-0050 K2 ; même modèle que le port
des tables).

Une commande ne dépend PAS de la cuisine (ADR-0049 D1 : jamais requise). Le module des commandes
déclare ici l'interface dont il a besoin pour router ses lignes ; le module ``restaurant.kitchen``
(qui dépend des commandes) l'implémente et l'enregistre au chargement. Aucune importation de
``restaurant_kitchen`` par les commandes, aucun cycle. Être enregistré ne veut pas dire être
EFFECTIF : l'appelant vérifie que le module est effectif sur le site (capacités recalculées après
ses verrous) avant tout routage.
"""

import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.platform.context import TenantScope

# Code du module qui fournit les postes (test de capacité, jamais le profil).
KITCHEN_MODULE = "restaurant.kitchen"


@dataclass(frozen=True)
class StationRef:
    """Poste tel que lu (nom COURANT : aucune copie dans la ligne)."""

    id: uuid.UUID
    site_id: uuid.UUID
    name: str
    is_active: bool


class StationRouter(Protocol):
    def route(
        self, site_id: uuid.UUID, items: Mapping[uuid.UUID, uuid.UUID]
    ) -> dict[uuid.UUID, StationRef]:
        """Poste de chaque élément du menu (``{élément: section}``) du site : affectation de
        l'élément, sinon de sa section ; un poste désactivé est ignoré. Les postes retenus sont
        pris en PARTAGE (leur désactivation, qui les prend en exclusif, attend). Éléments sans
        poste : absents du résultat (« Sans poste »)."""
        ...

    def names(self, station_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, StationRef]:
        """Postes lus sans verrou (affichage) ; ceux d'une autre entreprise sont absents."""
        ...


StationRouterFactory = Callable[[Session, TenantScope], StationRouter]
_factory: StationRouterFactory | None = None


def register_station_router(factory: StationRouterFactory) -> None:
    """Appelé par le module de la cuisine à son chargement."""
    global _factory
    _factory = factory


def station_router(db: Session, ctx: TenantScope) -> StationRouter | None:
    """Routeur des postes, ou ``None`` si aucun module ne l'a enregistré."""
    return _factory(db, ctx) if _factory is not None else None
