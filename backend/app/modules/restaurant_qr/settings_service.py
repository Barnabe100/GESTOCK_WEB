"""Réglages QR d'un site par le personnel (palier R7-C, ADR-0052) : lecture avec
``restaurant.qr.point.view``, modification avec ``restaurant.qr.settings.manage`` sur CE site
(abonnement du site revérifié), sous verrou exclusif des réglages, audit avant / après. Les
valeurs sont bornées par le schéma ET par les contraintes en base (plafonds de la plateforme)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError
from app.modules.restaurant_qr.models import QrSiteSettings
from app.modules.restaurant_qr.permissions import SETTINGS_MANAGE
from app.modules.restaurant_qr.schemas import QrSettingsOut, QrSettingsUpdate
from app.modules.restaurant_qr.settings import ensure_settings
from app.modules.restaurant_qr.sites import operation_site, readable_site_ids
from app.platform.audit.service import audit_action, changes
from app.platform.context import RequestContext

FIELDS = (
    "max_lines",
    "max_quantity_per_line",
    "max_pending_per_point",
    "customer_name_mode",
    "customer_phone_mode",
)


def _out(settings: QrSiteSettings) -> QrSettingsOut:
    return QrSettingsOut(
        site_id=settings.site_id,
        max_lines=settings.max_lines,
        max_quantity_per_line=settings.max_quantity_per_line,
        max_pending_per_point=settings.max_pending_per_point,
        customer_name_mode=settings.customer_name_mode,
        customer_phone_mode=settings.customer_phone_mode,
        updated_at=settings.updated_at,
    )


def _values(settings: QrSiteSettings) -> dict[str, Any]:
    return {field: str(getattr(settings, field)) for field in FIELDS}


class QrSettingsService:
    def __init__(self, db: Session, ctx: RequestContext) -> None:
        self.db = db
        self.ctx = ctx

    def get(self, site_id: uuid.UUID | None) -> QrSettingsOut:
        target = site_id or (self.ctx.site.id if self.ctx.site is not None else None)
        if target is None or target not in readable_site_ids(self.ctx, target):
            raise ForbiddenError("Accès à ce site refusé", code="site_access_denied")
        settings = ensure_settings(self.db, self.ctx.tenant_id, target)
        return _out(settings)

    def update(self, data: QrSettingsUpdate) -> QrSettingsOut:
        site_id = operation_site(self.ctx, data.site_id)
        if not self.ctx.has_site_permission(site_id, SETTINGS_MANAGE):
            raise ForbiddenError(
                "Permission insuffisante sur ce site",
                code="permission_denied",
                extra={"site_id": str(site_id)},
            )
        settings = ensure_settings(self.db, self.ctx.tenant_id, site_id, lock="update")
        before = _values(settings)
        for field in FIELDS:
            setattr(settings, field, getattr(data, field))
        settings.updated_by = self.ctx.user.id
        self.db.flush()
        after = _values(settings)
        if after != before:
            audit_action(
                self.db,
                self.ctx,
                "restaurant_qr_settings.updated",
                entity_type="restaurant_qr_settings",
                entity_id=settings.id,
                site_id=site_id,
                data=changes(before, after),
            )
        return _out(settings)
