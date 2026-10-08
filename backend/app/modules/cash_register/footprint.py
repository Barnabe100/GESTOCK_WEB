"""Empreinte de la caisse sur un site (palier D) : lecture seule.

Une session OUVERTE est un travail en cours (``active``) : si le module Caisse cessait d'être
effectif sur le site, elle ne pourrait plus être clôturée — la reconfiguration est alors
refusée. Ses mouvements (fond initial, encaissements) sont de l'historique.

``lock`` : verrou exclusif du réglage de caisse du site, celui que l'ouverture d'une session
prend en partage (``CashService.open_session``) : aucune session ne s'ouvre pendant la
reconfiguration, et une ouverture en cours est vue (elle est attendue). Aucune écriture."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.cash_register.models import (
    CashMovement,
    CashRegister,
    CashSession,
    CashSessionStatus,
    CashSiteSetting,
)
from app.platform.footprint import SiteFootprint, capped_count


def cash_footprint(session: Session, site_id: uuid.UUID, lock: bool) -> SiteFootprint:
    if lock:
        session.execute(
            select(CashSiteSetting.site_id)
            .where(CashSiteSetting.site_id == site_id)
            .with_for_update()
        )
    sessions = select(CashSession.id).where(CashSession.site_id == site_id)
    return SiteFootprint.of(
        history={
            "cash_sessions_closed": capped_count(
                session, sessions.where(CashSession.status == CashSessionStatus.CLOSED)
            ),
            "cash_movements": capped_count(
                session, select(CashMovement.id).where(CashMovement.site_id == site_id)
            ),
        },
        active={
            "cash_sessions_open": capped_count(
                session, sessions.where(CashSession.status == CashSessionStatus.OPEN)
            )
        },
        info={
            "cash_registers": capped_count(
                session, select(CashRegister.id).where(CashRegister.site_id == site_id)
            )
        },
    )
