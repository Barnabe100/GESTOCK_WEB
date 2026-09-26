"""Postes d'un site : activations d'installations sous la licence en vigueur (Phase 3.3-B3,
ADR-0035).

- **Activation** (client installé, ex. Desktop — le Web n'active jamais de navigateur) : sur le
  site sélectionné, sous la licence **en vigueur** de son abonnement ; quota =
  ``max_activations`` de cette licence, compté par abonnement de site, sous verrou de la ligne
  de l'abonnement (la dernière place ne peut être prise deux fois). Idempotente : la même
  installation déjà active sur ce site renvoie son activation (``200``).
- **Contrôle** (``check-in``) : l'installation se signale (``last_seen_at``) et reçoit l'état
  de la licence et la durée hors ligne tolérée.
- **Libération** : définitive pour cette activation ; libère une place, **rien d'autre** (ni la
  licence, ni sa période, ni l'abonnement ne changent). Réactiver = nouvelle activation, même
  licence, même période.

Chaque refus a un code distinct (licence absente, expirée, révoquée, pas encore valide, d'un
autre site, quota atteint, installation active ailleurs) et est journalisé
(``license.activation_failed``) par la route.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.platform.audit.service import record_audit
from app.platform.context import RequestContext
from app.platform.licensing.models import (
    ActivationStatus,
    License,
    LicenseActivation,
    LicenseState,
    LicenseStatus,
    ReleaseSource,
)
from app.platform.licensing.service import (
    license_in_force,
    reference_license,
    subscription_licenses,
)
from app.platform.subscriptions.service import site_subscription
from app.shared.pagination import PageParams, apply_sort, paginate

NOT_FOUND = "activation_not_found"


@dataclass(frozen=True)
class ActivationRequest:
    installation_id: uuid.UUID
    label: str
    client_version: str | None
    license_id: uuid.UUID | None = None


def active_count(session: Session, subscription_id: uuid.UUID) -> int:
    """Postes actifs d'un abonnement de site (quota)."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(LicenseActivation)
            .where(
                LicenseActivation.subscription_id == subscription_id,
                LicenseActivation.status == ActivationStatus.ACTIVE,
            )
        )
        or 0
    )


def active_counts(session: Session) -> dict[uuid.UUID, int]:
    """Postes actifs par abonnement (tenant actif, RLS ; ou toute la plateforme pour la
    console)."""
    return {
        subscription_id: count
        for subscription_id, count in session.execute(
            select(LicenseActivation.subscription_id, func.count())
            .where(LicenseActivation.status == ActivationStatus.ACTIVE)
            .group_by(LicenseActivation.subscription_id)
        ).all()
    }


def _license_refusal(license: License | None, now: datetime) -> ConflictError:
    if license is None:
        return ConflictError("Aucune licence pour ce site", code="license_missing")
    state = license.state(now)
    messages = {
        LicenseState.REVOKED: ("La licence de ce site est révoquée", "license_revoked"),
        LicenseState.EXPIRED: ("La licence de ce site est expirée", "license_expired"),
        LicenseState.NOT_YET_VALID: (
            "La licence de ce site n'est pas encore valide",
            "license_not_yet_valid",
        ),
    }
    detail, code = messages.get(state, ("Licence invalide", "license_invalid"))
    return ConflictError(
        detail,
        code=code,
        extra={"license_number": license.license_number, "state": state.value},
    )


class ActivationService:
    def __init__(self, db: Session, ctx: RequestContext, now: datetime, grace_days: int) -> None:
        self.db = db
        self.ctx = ctx
        self.now = now
        self.grace_days = grace_days

    # --- Lecture -------------------------------------------------------------------------------

    def search(
        self,
        params: PageParams,
        *,
        site_id: uuid.UUID | None,
        status: ActivationStatus | None,
    ) -> tuple[list[LicenseActivation], int]:
        visible = (
            {self.ctx.site.id} if self.ctx.site else set(self.ctx.capabilities.accessible_site_ids)
        )
        if site_id is not None:
            visible &= {site_id}
        stmt = select(LicenseActivation).where(LicenseActivation.site_id.in_(visible))
        if status is not None:
            stmt = stmt.where(LicenseActivation.status == status)
        stmt = apply_sort(
            stmt,
            params.sort,
            {
                "activated_at": LicenseActivation.activated_at,
                "last_seen_at": LicenseActivation.last_seen_at,
                "status": LicenseActivation.status,
            },
            default="-activated_at",
            tiebreaker=LicenseActivation.id,
        )
        return paginate(self.db, stmt, params)

    def is_stale(self, activation: LicenseActivation) -> bool:
        """Poste actif qui ne s'est pas signalé depuis plus que la durée hors ligne tolérée."""
        return activation.status is ActivationStatus.ACTIVE and (
            self.now - activation.last_seen_at > timedelta(days=self.grace_days)
        )

    # --- Activation ----------------------------------------------------------------------------

    def _site(self) -> uuid.UUID:
        if self.ctx.site is None:
            raise BusinessRuleError(
                "Choisissez le site du poste (en-tête X-Site-Id)", code="site_required"
            )
        return self.ctx.site.id

    def _licence_for(
        self, site_id: uuid.UUID, subscription_id: uuid.UUID, requested: uuid.UUID | None
    ) -> License:
        """Licence en vigueur de l'abonnement du site ; si le client présente une licence
        (fichier ``.lic``), elle doit être celle-ci."""
        current = license_in_force(self.db, subscription_id, self.now)
        if requested is not None:
            presented = self.db.get(License, requested)
            if presented is None:
                raise ConflictError("Licence invalide", code="license_invalid")
            if presented.site_id != site_id:
                raise ConflictError(
                    "Cette licence appartient à un autre site",
                    code="license_wrong_site",
                    extra={"license_number": presented.license_number},
                )
            if current is None or presented.id != current.id:
                if presented.status is LicenseStatus.REVOKED or current is None:
                    raise _license_refusal(presented, self.now)
                raise ConflictError(
                    "Cette licence n'est pas celle en vigueur pour ce site",
                    code="license_superseded",
                    extra={"license_number": presented.license_number},
                )
            return presented
        if current is not None:
            return current
        raise _license_refusal(
            reference_license(subscription_licenses(self.db, subscription_id), self.now), self.now
        )

    def activate(self, request: ActivationRequest) -> tuple[LicenseActivation, bool]:
        """(activation, rejouée). Refus : ``409`` avec un code distinct par cause."""
        site_id = self._site()
        subscription = site_subscription(self.db, site_id, for_update=True)
        if subscription is None:
            raise ConflictError("Aucun abonnement pour ce site", code="license_missing")
        license = self._licence_for(site_id, subscription.id, request.license_id)

        existing = self.db.scalars(
            select(LicenseActivation).where(
                LicenseActivation.installation_id == request.installation_id,
                LicenseActivation.status == ActivationStatus.ACTIVE,
            )
        ).one_or_none()
        if existing is not None:
            if existing.site_id != site_id:
                raise ConflictError(
                    "Ce poste est déjà activé sur un autre site",
                    code="installation_active_elsewhere",
                    extra={"site_id": str(existing.site_id)},
                )
            # Rejeu (même installation, même site) : mise à jour de la présence seulement.
            existing.label = request.label
            existing.client_version = request.client_version
            existing.last_seen_at = self.now
            self.db.flush()
            return existing, True

        used = active_count(self.db, subscription.id)
        if used >= license.max_activations:
            raise ConflictError(
                "Le nombre maximal de postes autorisés pour ce site est atteint.",
                code="activation_quota_reached",
                extra={"max_activations": license.max_activations, "used": used},
            )
        activation = LicenseActivation(
            tenant_id=self.ctx.tenant_id,
            site_id=site_id,
            subscription_id=subscription.id,
            license_id=license.id,
            installation_id=request.installation_id,
            label=request.label,
            client_version=request.client_version,
            activated_at=self.now,
            activated_by=self.ctx.user.id,
            last_seen_at=self.now,
            status=ActivationStatus.ACTIVE,
        )
        try:
            with self.db.begin_nested():
                self.db.add(activation)
                self.db.flush()
        except IntegrityError as exc:
            # Même installation activée au même instant sur un autre site (verrous distincts).
            raise ConflictError(
                "Ce poste est déjà activé sur un autre site",
                code="installation_active_elsewhere",
            ) from exc
        record_audit(
            self.db,
            action="license.activation.created",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            site_id=site_id,
            entity_type="license_activation",
            entity_id=activation.id,
            data={
                "installation_id": str(request.installation_id),
                "label": request.label,
                "client_version": request.client_version,
                "license_id": str(license.id),
                "license_number": license.license_number,
                "max_activations": license.max_activations,
                "used": used + 1,
            },
        )
        return activation, False

    def record_failure(self, request: ActivationRequest, code: str) -> None:
        """Journal d'un refus d'activation (écrit par la route dans sa propre transaction)."""
        record_audit(
            self.db,
            action="license.activation_failed",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            site_id=self.ctx.site.id if self.ctx.site else None,
            entity_type="license_activation",
            entity_id=None,
            data={
                "code": code,
                "installation_id": str(request.installation_id),
                "label": request.label,
            },
        )

    # --- Contrôle ------------------------------------------------------------------------------

    def check_in(self, installation_id: uuid.UUID) -> tuple[LicenseActivation, License | None]:
        """L'installation se signale : présence mise à jour, licence en vigueur renvoyée (ou
        aucune : le client doit alors bloquer l'usage)."""
        site_id = self._site()
        activation = self.db.scalars(
            select(LicenseActivation).where(
                LicenseActivation.installation_id == installation_id,
                LicenseActivation.site_id == site_id,
                LicenseActivation.status == ActivationStatus.ACTIVE,
            )
        ).one_or_none()
        if activation is None:
            raise NotFoundError("Ce poste n'est pas activé sur ce site", code=NOT_FOUND)
        activation.last_seen_at = self.now
        self.db.flush()
        return activation, license_in_force(self.db, activation.subscription_id, self.now)

    # --- Libération ----------------------------------------------------------------------------

    def release(self, activation_id: uuid.UUID, reason: str) -> LicenseActivation:
        activation = self.db.scalars(
            select(LicenseActivation)
            .where(LicenseActivation.id == activation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
        if (
            activation is None
            or activation.site_id not in self.ctx.capabilities.accessible_site_ids
        ):
            raise NotFoundError("Poste introuvable", code=NOT_FOUND)
        if self.ctx.site is not None and self.ctx.site.id != activation.site_id:
            raise ForbiddenError("Ce poste appartient à un autre site", code="site_mismatch")
        self.ctx.ensure_site_allows(activation.site_id)
        if activation.status is not ActivationStatus.ACTIVE:
            raise ConflictError("Ce poste est déjà libéré", code="activation_already_released")
        release(
            activation,
            now=self.now,
            by=self.ctx.user.id,
            source=ReleaseSource.TENANT,
            reason=reason,
        )
        self.db.flush()
        record_audit(
            self.db,
            action="license.activation.released",
            tenant_id=self.ctx.tenant_id,
            user_id=self.ctx.user.id,
            site_id=activation.site_id,
            entity_type="license_activation",
            entity_id=activation.id,
            data=release_data(activation),
        )
        return activation


def release(
    activation: LicenseActivation,
    *,
    now: datetime,
    by: uuid.UUID | None,
    source: ReleaseSource,
    reason: str,
) -> None:
    """Libère une place du quota ; ni la licence, ni sa période, ni l'abonnement ne changent."""
    activation.status = ActivationStatus.RELEASED
    activation.released_at = now
    activation.released_by = by
    activation.release_source = source
    activation.release_reason = reason


def release_data(activation: LicenseActivation) -> dict[str, Any]:
    return {
        "installation_id": str(activation.installation_id),
        "label": activation.label,
        "license_id": str(activation.license_id),
        "reason": activation.release_reason,
        "source": activation.release_source.value if activation.release_source else None,
    }
