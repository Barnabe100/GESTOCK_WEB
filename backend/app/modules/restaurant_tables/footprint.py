"""Empreinte des tables sur un site (palier D ; palier R5-A, ADR-0049).

- ``active`` : commandes non finales rattachées à une table du site (A6) — la désactivation
  manuelle du module est refusée (``module_has_open_operations``) ; un changement de profil qui
  retire le module est BLOQUÉ (mécanisme commun du palier D) ;
- ``info`` : zones et tables (configuration, conservée par une désactivation ou un changement
  de profil).

Les commandes sont lues par leur interface publique (``restaurant_orders.api``). ``lock`` : la
reconfiguration tient déjà le verrou EXCLUSIF du site (``lock_site``) ; les écritures qui
rattachent une table à une commande (palier R5-B) prennent le site en partage de clé en
premier : elles attendent donc la fin de la reconfiguration, qui n'a rien d'autre à verrouiller.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_orders.api import open_orders_on_tables
from app.modules.restaurant_tables.models import RestaurantTable, RestaurantZone
from app.platform.footprint import SiteFootprint, capped_count


def tables_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    return SiteFootprint.of(
        active={"restaurant_orders_on_tables": open_orders_on_tables(session, site_id)},
        info={
            "restaurant_zones": capped_count(
                session, select(RestaurantZone.id).where(RestaurantZone.site_id == site_id)
            ),
            "restaurant_tables": capped_count(
                session, select(RestaurantTable.id).where(RestaurantTable.site_id == site_id)
            ),
        },
    )
