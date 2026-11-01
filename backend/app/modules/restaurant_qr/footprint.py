"""Empreinte des commandes par QR Code sur un site (palier D ; palier R7, ADR-0052).

- ``info`` : points QR du site (configuration, conservée par une désactivation ou un changement
  de profil ; aucun point n'est supprimé)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.restaurant_qr.models import QrPoint
from app.platform.footprint import SiteFootprint, capped_count


def qr_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    return SiteFootprint.of(
        info={
            "restaurant_qr_points": capped_count(
                session, select(QrPoint.id).where(QrPoint.site_id == site_id)
            ),
        },
    )
