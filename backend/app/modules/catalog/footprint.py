"""Empreinte du catalogue sur un site (palier D) : assortiment du site, en information seulement
(un changement de profil ne copie, ne retire ni ne modifie jamais l'assortiment)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.catalog.models import SiteArticle
from app.platform.footprint import SiteFootprint, capped_count


def catalog_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    return SiteFootprint.of(
        info={
            "assortment_articles": capped_count(
                session,
                select(SiteArticle.id).where(
                    SiteArticle.site_id == site_id, SiteArticle.is_active.is_(True)
                ),
            )
        }
    )
