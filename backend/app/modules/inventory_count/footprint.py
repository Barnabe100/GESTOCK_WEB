"""Empreinte des inventaires sur un site (palier D) : lecture seule."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.inventory_count.models import Inventory, InventoryStatus
from app.platform.footprint import SiteFootprint, capped_count

_CLOSED = (InventoryStatus.VALIDATED, InventoryStatus.CANCELLED)


def inventory_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    """Inventaires clos (historique) ; en préparation, comptage ou attente de validation
    (ouverts, toujours compatibles : les inventaires sont proposés par tous les profils)."""
    of_site = select(Inventory.id).where(Inventory.site_id == site_id)
    return SiteFootprint.of(
        history={
            "inventories": capped_count(session, of_site.where(Inventory.status.in_(_CLOSED)))
        },
        open={
            "inventories_open": capped_count(
                session, of_site.where(Inventory.status.not_in(_CLOSED))
            )
        },
    )
