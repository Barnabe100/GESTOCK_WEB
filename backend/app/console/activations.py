"""Postes des sites vus et libérés par TechNova (Phase 3.3-B3, ADR-0035).

Métadonnées seulement (libellé, version du client, identifiant d'installation, dates) ; jamais
l'utilisateur de l'entreprise qui a activé le poste. La libération par TechNova (support :
poste perdu, réinstallation) libère une place du quota, rien d'autre ; double audit. Le rôle
SQL de la console ne peut modifier que les colonnes de libération d'un poste ``ACTIVE``.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.console.audit import PlatformActor, record_tenant_action
from app.core.errors import ConflictError, NotFoundError
from app.platform.audit.service import RequestMeta
from app.platform.licensing.activations import release, release_data
from app.platform.licensing.models import (
    ActivationStatus,
    License,
    LicenseActivation,
    ReleaseSource,
)
from app.platform.tenancy.models import Site, Tenant
from app.shared.pagination import PageParams, apply_sort, paginate_rows

NOT_FOUND = "activation_not_found"


@dataclass(frozen=True)
class ActivationFilters:
    tenant_id: uuid.UUID | None = None
    subscription_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    status: ActivationStatus | None = None


class ActivationAdminService:
    def __init__(self, db: Session, now: datetime) -> None:
        self.db = db
        self.now = now

    def _base(self) -> Select[Any]:
        return (
            select(
                LicenseActivation,
                Tenant.name.label("tenant_name"),
                Site.name.label("site_name"),
                License.license_number,
            )
            .select_from(LicenseActivation)
            .join(Tenant, Tenant.id == LicenseActivation.tenant_id)
            .join(Site, Site.id == LicenseActivation.site_id)
            .join(License, License.id == LicenseActivation.license_id)
        )

    def search(self, filters: ActivationFilters, params: PageParams) -> tuple[list[Any], int]:
        stmt = self._base()
        if filters.tenant_id:
            stmt = stmt.where(LicenseActivation.tenant_id == filters.tenant_id)
        if filters.subscription_id:
            stmt = stmt.where(LicenseActivation.subscription_id == filters.subscription_id)
        if filters.site_id:
            stmt = stmt.where(LicenseActivation.site_id == filters.site_id)
        if filters.status:
            stmt = stmt.where(LicenseActivation.status == filters.status)
        stmt = apply_sort(
            stmt,
            params.sort,
            {
                "activated_at": LicenseActivation.activated_at,
                "last_seen_at": LicenseActivation.last_seen_at,
            },
            default="-activated_at",
            tiebreaker=LicenseActivation.id,
        )
        return paginate_rows(self.db, stmt, params)

    def row(self, activation_id: uuid.UUID) -> Any:
        found = self.db.execute(
            self._base().where(LicenseActivation.id == activation_id)
        ).one_or_none()
        if found is None:
            raise NotFoundError("Poste introuvable", code=NOT_FOUND)
        return found

    def release(
        self, activation_id: uuid.UUID, reason: str, actor: PlatformActor, meta: RequestMeta
    ) -> None:
        """Verrou de la ligne ; la politique RLS de libération n'expose au verrou que les postes
        ``ACTIVE`` : un poste déjà libéré répond ``409``."""
        activation = self.db.scalars(
            select(LicenseActivation)
            .where(LicenseActivation.id == activation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if activation is None or activation.status is not ActivationStatus.ACTIVE:
            self.row(activation_id)  # 404 si inconnu
            raise ConflictError("Ce poste est déjà libéré", code="activation_already_released")
        release(
            activation,
            now=self.now,
            by=actor.user_id,
            source=ReleaseSource.TECHNOVA,
            reason=reason,
        )
        self.db.flush()
        record_tenant_action(
            self.db,
            actor=actor,
            action="license.activation.released",
            tenant_id=activation.tenant_id,
            target_type="license_activation",
            target_id=activation.id,
            before={"status": ActivationStatus.ACTIVE.value},
            after={"status": ActivationStatus.RELEASED.value},
            reason=reason,
            data=release_data(activation) | {"site_id": activation.site_id},
            meta=meta,
        )
