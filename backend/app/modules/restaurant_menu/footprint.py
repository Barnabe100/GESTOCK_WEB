"""Empreinte du menu sur un site (palier D) : configuration seulement — un changement de profil
du site ne la bloque jamais et la conserve (lecture seule, tables du module seulement)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_menu.models import MenuItem, MenuSection
from app.platform.footprint import SiteFootprint, capped_count


def menu_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    return SiteFootprint.of(
        info={
            "menu_sections": capped_count(
                session, select(MenuSection.id).where(MenuSection.site_id == site_id)
            ),
            "menu_items": capped_count(
                session, select(MenuItem.id).where(MenuItem.site_id == site_id)
            ),
        }
    )
