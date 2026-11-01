"""Réglages QR d'un site (palier R7-C, ADR-0052) : créés à l'activation du module sur un site
(``site_setup``), jamais écrasés ensuite ; créés au premier usage s'ils manquent."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.restaurant_qr.models import (
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_PENDING,
    DEFAULT_MAX_QUANTITY,
    ContactFieldMode,
    QrSiteSettings,
)
from app.platform.catalog.models import BusinessProfile
from app.shared.ids import new_id


def _insert_defaults(session: Session, tenant_id: uuid.UUID, site_id: uuid.UUID) -> None:
    session.execute(
        insert(QrSiteSettings)
        .values(
            id=new_id(),
            tenant_id=tenant_id,
            site_id=site_id,
            max_lines=DEFAULT_MAX_LINES,
            max_quantity_per_line=DEFAULT_MAX_QUANTITY,
            max_pending_per_point=DEFAULT_MAX_PENDING,
            # Minimisation : nom facultatif (appel au comptoir), téléphone non demandé.
            customer_name_mode=ContactFieldMode.OPTIONAL,
            customer_phone_mode=ContactFieldMode.HIDDEN,
        )
        .on_conflict_do_nothing(index_elements=[QrSiteSettings.tenant_id, QrSiteSettings.site_id])
    )


def site_setup(
    session: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, profile: BusinessProfile
) -> None:
    """``site_setup`` du manifeste : réglages du site créés s'ils manquent, jamais écrasés."""
    _insert_defaults(session, tenant_id, site_id)


def ensure_settings(
    session: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, *, lock: str | None = None
) -> QrSiteSettings:
    """Réglages du site, créés au premier usage s'ils manquent. ``lock`` : ``"share"``
    (commande publique) ou ``"update"`` (modification par le personnel)."""
    stmt = select(QrSiteSettings).where(
        QrSiteSettings.tenant_id == tenant_id, QrSiteSettings.site_id == site_id
    )
    if lock == "share":
        stmt = stmt.with_for_update(read=True)
    elif lock == "update":
        stmt = stmt.with_for_update()
    settings = session.scalars(stmt.execution_options(populate_existing=True)).one_or_none()
    if settings is None:
        _insert_defaults(session, tenant_id, site_id)
        settings = session.scalars(stmt.execution_options(populate_existing=True)).one()
    return settings
