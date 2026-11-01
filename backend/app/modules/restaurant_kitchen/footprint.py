"""Empreinte de la cuisine sur un site (palier D ; palier R6, ADR-0050).

- ``active`` : lignes NON finales routées vers un poste du site — la désactivation manuelle du
  module est refusée (``module_has_open_operations``) ; un changement de profil qui retire le
  module est BLOQUÉ (mécanisme commun du palier D) ;
- ``info`` : postes et affectations (configuration, conservée par une désactivation ou un
  changement de profil).

Les lignes sont lues par l'interface publique des commandes."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_kitchen.models import PrepStation, StationRoute
from app.modules.restaurant_orders.api import open_routed_lines
from app.platform.footprint import SiteFootprint, capped_count


def kitchen_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    return SiteFootprint.of(
        active={"restaurant_routed_lines": open_routed_lines(session, site_id)},
        info={
            "restaurant_prep_stations": capped_count(
                session, select(PrepStation.id).where(PrepStation.site_id == site_id)
            ),
            "restaurant_station_routes": capped_count(
                session, select(StationRoute.id).where(StationRoute.site_id == site_id)
            ),
        },
    )
