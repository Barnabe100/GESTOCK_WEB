"""Implémentation du port « postes » des commandes (palier R6 ; port déclaré par
``restaurant_orders/station_port.py``, enregistré par le manifeste de ce module).

Routage (ADR-0050 K2) : affectation de l'ÉLÉMENT, sinon de sa SECTION ; un poste désactivé est
ignoré (l'élément tombe sur sa section, puis « Sans poste »). Les postes retenus sont pris en
PARTAGE après les verrous de la commande : leur désactivation (exclusif, puis comptage des lignes
non finales) attend la fin de l'écriture et voit les lignes routées."""

import uuid
from collections.abc import Iterable, Mapping

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.restaurant_kitchen.models import PrepStation, StationRoute
from app.modules.restaurant_orders.api import StationRef
from app.platform.context import TenantScope


def _ref(station: PrepStation) -> StationRef:
    return StationRef(
        id=station.id, site_id=station.site_id, name=station.name, is_active=station.is_active
    )


class StationRouterService:
    def __init__(self, db: Session, ctx: TenantScope) -> None:
        self.db = db
        self.ctx = ctx

    def route(
        self, site_id: uuid.UUID, items: Mapping[uuid.UUID, uuid.UUID]
    ) -> dict[uuid.UUID, StationRef]:
        if not items:
            return {}
        routes = self.db.execute(
            select(
                StationRoute.menu_item_id, StationRoute.section_id, StationRoute.station_id
            ).where(
                StationRoute.tenant_id == self.ctx.tenant_id,
                StationRoute.site_id == site_id,
                or_(
                    StationRoute.menu_item_id.in_(set(items)),
                    StationRoute.section_id.in_(set(items.values())),
                ),
            )
        ).tuples()
        by_item: dict[uuid.UUID, uuid.UUID] = {}
        by_section: dict[uuid.UUID, uuid.UUID] = {}
        for item_id, section_id, station_id in routes:
            if item_id is not None:
                by_item[item_id] = station_id
            elif section_id is not None:
                by_section[section_id] = station_id
        wanted = set(by_item.values()) | set(by_section.values())
        if not wanted:
            return {}
        stations = {
            station.id: station
            for station in self.db.scalars(
                select(PrepStation)
                .where(
                    PrepStation.tenant_id == self.ctx.tenant_id,
                    PrepStation.site_id == site_id,
                    PrepStation.id.in_(wanted),
                )
                .order_by(PrepStation.id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        }
        result: dict[uuid.UUID, StationRef] = {}
        for item_id, section_id in items.items():
            for candidate in (by_item.get(item_id), by_section.get(section_id)):
                station = stations.get(candidate) if candidate is not None else None
                if station is not None and station.is_active:
                    result[item_id] = _ref(station)
                    break
        return result

    def names(self, station_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, StationRef]:
        ids = set(station_ids)
        if not ids:
            return {}
        return {
            station.id: _ref(station)
            for station in self.db.scalars(
                select(PrepStation).where(
                    PrepStation.tenant_id == self.ctx.tenant_id, PrepStation.id.in_(ids)
                )
            )
        }
