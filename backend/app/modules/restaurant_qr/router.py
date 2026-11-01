"""API de gestion des points QR d'un site (palier R7-A, ADR-0052) : ``/restaurant/qr``.

Monté avec ``require_module("restaurant.qr")`` ; sans site sélectionné, le service limite les
lectures aux sites où le module est effectif pour le membre et revérifie chaque écriture pour
son site. Les routes PUBLIQUES (menu, commande, suivi) sont dans ``public_router``."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.modules.restaurant_qr.models import QrPointKind
from app.modules.restaurant_qr.permissions import POINT_MANAGE, POINT_VIEW, SETTINGS_MANAGE
from app.modules.restaurant_qr.schemas import (
    PointCreate,
    PointIssued,
    PointOut,
    PointRegenerate,
    PointUpdate,
    QrSettingsOut,
    QrSettingsUpdate,
)
from app.modules.restaurant_qr.service import QrPointService
from app.modules.restaurant_qr.settings_service import QrSettingsService
from app.platform.context import DbSession, RequestContext, SettingsDep, require_permission
from app.shared.clock import utcnow
from app.shared.pagination import PageParams, page_params
from app.shared.schemas import Page, StatusFilter

router = APIRouter(tags=["restaurant.qr"])

PointView = Annotated[RequestContext, Depends(require_permission(POINT_VIEW))]
PointManage = Annotated[RequestContext, Depends(require_permission(POINT_MANAGE))]
SettingsManage = Annotated[RequestContext, Depends(require_permission(SETTINGS_MANAGE))]
Paging = Annotated[PageParams, Depends(page_params)]
StatusParam = Annotated[StatusFilter, Query(alias="status")]


def _service(db: DbSession, ctx: RequestContext, settings: SettingsDep) -> QrPointService:
    return QrPointService(db, ctx, settings, utcnow())


def _no_store(response: Response) -> None:
    # L'adresse et l'image du QR Code ne doivent rester dans aucun cache.
    response.headers["Cache-Control"] = "no-store"


@router.get("/points", response_model=Page[PointOut])
def list_points(
    ctx: PointView,
    db: DbSession,
    settings: SettingsDep,
    paging: Paging,
    search: str | None = None,
    site_id: uuid.UUID | None = None,
    kind: QrPointKind | None = None,
    status_filter: StatusParam = StatusFilter.ALL,
) -> Page[PointOut]:
    items, total = _service(db, ctx, settings).search(paging, search, status_filter, site_id, kind)
    return Page(items=items, total=total, limit=paging.limit, offset=paging.offset)


@router.get("/points/{point_id}", response_model=PointOut)
def get_point(
    point_id: uuid.UUID, ctx: PointView, db: DbSession, settings: SettingsDep
) -> PointOut:
    return _service(db, ctx, settings).get(point_id)


@router.post("/points", response_model=PointIssued, status_code=status.HTTP_201_CREATED)
def create_point(
    body: PointCreate,
    ctx: PointManage,
    db: DbSession,
    settings: SettingsDep,
    response: Response,
) -> PointIssued:
    issued = _service(db, ctx, settings).create(body)
    db.commit()
    _no_store(response)
    return issued


@router.put("/points/{point_id}", response_model=PointOut)
def update_point(
    point_id: uuid.UUID, body: PointUpdate, ctx: PointManage, db: DbSession, settings: SettingsDep
) -> PointOut:
    out = _service(db, ctx, settings).update(point_id, body.label)
    db.commit()
    return out


@router.post("/points/{point_id}/activate", response_model=PointOut)
def activate_point(
    point_id: uuid.UUID, ctx: PointManage, db: DbSession, settings: SettingsDep
) -> PointOut:
    out = _service(db, ctx, settings).set_active(point_id, True)
    db.commit()
    return out


@router.post("/points/{point_id}/deactivate", response_model=PointOut)
def deactivate_point(
    point_id: uuid.UUID, ctx: PointManage, db: DbSession, settings: SettingsDep
) -> PointOut:
    out = _service(db, ctx, settings).set_active(point_id, False)
    db.commit()
    return out


@router.post("/points/{point_id}/regenerate", response_model=PointIssued)
def regenerate_point(
    point_id: uuid.UUID,
    body: PointRegenerate,
    ctx: PointManage,
    db: DbSession,
    settings: SettingsDep,
    response: Response,
) -> PointIssued:
    """Nouveau jeton (``confirm_invalidation`` obligatoire) : les QR Codes imprimés de ce point
    cessent immédiatement de fonctionner."""
    issued = _service(db, ctx, settings).regenerate(point_id)
    db.commit()
    _no_store(response)
    return issued


# --- Réglages QR du site ------------------------------------------------------------------------


@router.get("/settings", response_model=QrSettingsOut)
def get_qr_settings(
    ctx: PointView, db: DbSession, site_id: uuid.UUID | None = None
) -> QrSettingsOut:
    out = QrSettingsService(db, ctx).get(site_id)
    db.commit()  # réglages créés au premier usage s'ils manquaient
    return out


@router.put("/settings", response_model=QrSettingsOut)
def update_qr_settings(body: QrSettingsUpdate, ctx: SettingsManage, db: DbSession) -> QrSettingsOut:
    out = QrSettingsService(db, ctx).update(body)
    db.commit()
    return out
