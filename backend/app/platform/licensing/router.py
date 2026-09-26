"""Postes (activations) des sites, vus par l'entreprise (Phase 3.3-B3, ADR-0035).

L'entreprise lit ses licences (``GET /subscriptions``) et ses postes, libère un poste. Une
installation cliente (Desktop) s'active et se contrôle sur le site sélectionné ; le Web
n'active jamais de navigateur. Aucune route ne crée ni ne modifie une licence.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings
from app.core.errors import AppError
from app.platform.context import (
    DbSession,
    NowDep,
    RequestContext,
    SettingsDep,
    require_permission,
)
from app.platform.licensing.activations import (
    ActivationRequest,
    ActivationService,
    active_count,
)
from app.platform.licensing.models import ActivationStatus, LicenseActivation
from app.platform.licensing.schemas import LicenseSummary, license_summary
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page

router = APIRouter(tags=["licensing"])

View = Annotated[RequestContext, Depends(require_permission("subscription.subscription.view"))]
Manage = Annotated[RequestContext, Depends(require_permission("subscription.activation.manage"))]
Check = Annotated[RequestContext, Depends(require_permission("subscription.activation.check"))]


def _text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("Valeur obligatoire")
    return stripped


class ActivationIn(BaseModel):
    """Demande d'une installation cliente. ``installation_id`` : UUID aléatoire propre à
    l'installation (jamais une adresse MAC, un numéro de processeur ni une adresse IP)."""

    model_config = ConfigDict(extra="forbid")

    installation_id: uuid.UUID
    label: str = Field(min_length=1, max_length=100)
    client_version: str | None = Field(default=None, max_length=50)
    # Licence présentée par le client (fichier .lic) : doit être celle en vigueur du site.
    license_id: uuid.UUID | None = None

    _label = field_validator("label")(_text)


class CheckInIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: uuid.UUID


class ReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)

    _reason = field_validator("reason")(_text)


class ActivationOut(BaseModel):
    """Poste vu par l'entreprise ; une libération par TechNova n'en révèle jamais l'agent."""

    id: uuid.UUID
    site_id: uuid.UUID
    subscription_id: uuid.UUID
    license_id: uuid.UUID
    installation_id: uuid.UUID
    label: str
    client_version: str | None
    status: str
    activated_at: datetime
    last_seen_at: datetime
    stale: bool
    released_at: datetime | None
    release_source: str | None
    release_reason: str | None


class CheckInOut(BaseModel):
    activation: ActivationOut
    # Licence en vigueur du site ; nulle : aucune licence en vigueur, le client doit bloquer.
    license: LicenseSummary | None
    offline_grace_days: int
    server_time: datetime


def _out(service: ActivationService, a: LicenseActivation) -> ActivationOut:
    return ActivationOut(
        id=a.id,
        site_id=a.site_id,
        subscription_id=a.subscription_id,
        license_id=a.license_id,
        installation_id=a.installation_id,
        label=a.label,
        client_version=a.client_version,
        status=a.status.value,
        activated_at=a.activated_at,
        last_seen_at=a.last_seen_at,
        stale=service.is_stale(a),
        released_at=a.released_at,
        release_source=a.release_source.value if a.release_source else None,
        release_reason=a.release_reason,
    )


def _service(
    db: DbSession, ctx: RequestContext, now: datetime, settings: Settings
) -> ActivationService:
    return ActivationService(db, ctx, now, settings.activation_offline_grace_days)


@router.get("/license-activations", response_model=Page[ActivationOut])
def list_activations(
    ctx: View,
    db: DbSession,
    now: NowDep,
    settings: SettingsDep,
    params: Annotated[PageParams, Depends(page_params)],
    site_id: uuid.UUID | None = None,
    status: ActivationStatus | None = None,
) -> Page[ActivationOut]:
    """Postes des sites visibles (site sélectionné, sinon sites accessibles) ; filtres ``site_id``,
    ``status`` ; tri ``activated_at`` décroissant par défaut, ``last_seen_at``, ``status``."""
    service = _service(db, ctx, now, settings)
    rows, total = service.search(params, site_id=site_id, status=status)
    return Page(
        items=[_out(service, a) for a in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.post(
    "/license-activations",
    response_model=ActivationOut,
    status_code=status.HTTP_201_CREATED,
)
def activate(
    body: ActivationIn,
    ctx: Manage,
    db: DbSession,
    now: NowDep,
    settings: SettingsDep,
    response: Response,
) -> ActivationOut:
    """Active une installation sur le site sélectionné (``X-Site-Id``), sous la licence en
    vigueur. ``201`` ; ``200`` si l'installation est déjà active sur ce site (idempotent).
    Refus (``409``, journalisés) : ``license_missing``, ``license_expired``,
    ``license_revoked``, ``license_not_yet_valid``, ``license_invalid``,
    ``license_wrong_site``, ``license_superseded``, ``activation_quota_reached``,
    ``installation_active_elsewhere``."""
    service = _service(db, ctx, now, settings)
    request = ActivationRequest(
        installation_id=body.installation_id,
        label=body.label,
        client_version=body.client_version,
        license_id=body.license_id,
    )
    try:
        activation, replayed = service.activate(request)
    except AppError as exc:
        db.rollback()
        service.record_failure(request, exc.code)
        db.commit()
        raise
    db.commit()
    if replayed:
        response.status_code = status.HTTP_200_OK
    return _out(service, activation)


@router.post("/license-activations/check-in", response_model=CheckInOut)
def check_in(
    body: CheckInIn, ctx: Check, db: DbSession, now: NowDep, settings: SettingsDep
) -> CheckInOut:
    """Contrôle d'une installation active sur le site sélectionné : présence mise à jour,
    licence en vigueur (ou aucune) et durée hors ligne tolérée."""
    service = _service(db, ctx, now, settings)
    activation, current = service.check_in(body.installation_id)
    db.commit()
    return CheckInOut(
        activation=_out(service, activation),
        license=license_summary(current, now, active_count(db, activation.subscription_id)),
        offline_grace_days=settings.activation_offline_grace_days,
        server_time=now,
    )


@router.post("/license-activations/{activation_id}/release", response_model=ActivationOut)
def release_activation(
    activation_id: uuid.UUID,
    body: ReleaseIn,
    ctx: Manage,
    db: DbSession,
    now: NowDep,
    settings: SettingsDep,
) -> ActivationOut:
    """Libère un poste (définitif pour cette activation) : une place se libère ; la licence, sa
    période et l'abonnement ne changent pas. ``409 activation_already_released``."""
    service = _service(db, ctx, now, settings)
    activation = service.release(activation_id, body.reason)
    db.commit()
    return _out(service, activation)
