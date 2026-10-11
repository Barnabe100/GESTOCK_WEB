"""Port « tables » des commandes de restauration (palier R5-B, ADR-0049 D1 ; sur le modèle du
port caisse des ventes).

Une commande ne dépend PAS des tables (D2 : jamais exigées). Le module des commandes déclare ici
l'interface dont il a besoin ; le module ``restaurant.tables`` (qui dépend des commandes)
l'implémente et l'enregistre au chargement. Aucune importation de ``restaurant_tables`` par les
commandes, aucun cycle. Être enregistré ne veut pas dire être EFFECTIF : l'appelant vérifie que
le module est effectif sur le site (capacités recalculées après ses verrous) et disponible dans
le registre avant tout rattachement d'une table.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.platform.context import RequestContext

# Code du module qui fournit les tables (test de capacité, jamais le profil).
TABLES_MODULE = "restaurant.tables"


@dataclass(frozen=True)
class TableRef:
    """Table telle que lue (libellé COURANT : aucune copie dans la commande, A7)."""

    id: uuid.UUID
    site_id: uuid.UUID
    label: str
    zone_name: str | None
    is_active: bool


class TableDirectory(Protocol):
    def lock_for_order(self, site_id: uuid.UUID, table_id: uuid.UUID) -> TableRef:
        """Table du site ``site_id``, prise en PARTAGE et relue sous ce verrou (sa
        désactivation, qui la prend en exclusif, attend) ; ``404 table_not_found`` si elle
        n'existe pas ou appartient à un autre site, ``422 table_inactive`` si elle est
        désactivée."""
        ...

    def labels(self, table_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, TableRef]:
        """Tables lues sans verrou (affichage) ; celles d'une autre entreprise sont absentes."""
        ...


TableDirectoryFactory = Callable[[Session, RequestContext], TableDirectory]
_factory: TableDirectoryFactory | None = None


def register_table_directory(factory: TableDirectoryFactory) -> None:
    """Appelé par le module des tables à son chargement."""
    global _factory
    _factory = factory


def table_directory(db: Session, ctx: RequestContext) -> TableDirectory | None:
    """Annuaire des tables, ou ``None`` si aucun module ne l'a enregistré."""
    return _factory(db, ctx) if _factory is not None else None
