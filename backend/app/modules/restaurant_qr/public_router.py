"""API PUBLIQUE des commandes par QR Code (palier R7, ADR-0052) : ``/api/v1/public/restaurant/qr``.

Aucune authentification : le jeton du point (en-tête ``X-QR-Token``, lu par l'interface dans le
FRAGMENT de l'adresse, jamais dans l'URL d'une requête) est vérifié à CHAQUE appel par
``access.resolve_access``. Chaque appel est d'abord compté par adresse IP (empreinte HMAC,
proxies de confiance seulement), puis les réponses sont marquées : aucun cache, aucun
référent, aucune indexation."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from sqlalchemy.orm import Session

from app.core.client_ip import client_ip
from app.core.config import Settings
from app.modules.restaurant_qr.access import resolve_access
from app.modules.restaurant_qr.public_schemas import (
    PublicMenuOut,
    PublicOrderCreate,
    PublicOrderPlaced,
)
from app.modules.restaurant_qr.public_service import PlaceOrderContext, place_order, public_menu
from app.platform.context import DbSession, MetaDep, NowDep, RegistryDep, SettingsDep
from app.platform.ratelimit.service import RateLimiter

public_router = APIRouter(tags=["public.restaurant.qr"])

TokenHeader = Annotated[str | None, Header(alias="X-QR-Token")]
READ_BUCKET = "qr.read.ip"


def secure_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"


def count_read(db: Session, settings: Settings, request: Request, now: datetime) -> None:
    """Lecture publique comptée par adresse IP, validée AVANT tout traitement."""
    RateLimiter(db, now, settings.rate_limit_hmac_key.get_secret_value()).hit(
        READ_BUCKET,
        client_ip(request, settings),
        limit=settings.qr_read_ip_limit,
        window=timedelta(minutes=settings.qr_read_window_minutes),
    )
    db.commit()


@public_router.get("/menu", response_model=PublicMenuOut)
def get_public_menu(
    request: Request,
    response: Response,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
    now: NowDep,
    meta: MetaDep,
    token: TokenHeader = None,
) -> PublicMenuOut:
    secure_headers(response)
    count_read(db, settings, request, now)
    access = resolve_access(db, registry, token, meta, now)
    return public_menu(db, access)


@public_router.post("/orders", response_model=PublicOrderPlaced, status_code=201)
def post_public_order(
    body: PublicOrderCreate,
    request: Request,
    response: Response,
    db: DbSession,
    registry: RegistryDep,
    settings: SettingsDep,
    now: NowDep,
    meta: MetaDep,
    token: TokenHeader = None,
) -> PublicOrderPlaced:
    """Commande sans compte : « à confirmer » par le personnel, sauf acceptation automatique du
    site ; aucun paiement. Réponse : numéro, état et lien de suivi temporaire."""
    secure_headers(response)
    placed = place_order(
        PlaceOrderContext(
            db=db, settings=settings, registry=registry, request=request, meta=meta, now=now
        ),
        token,
        body,
    )
    db.commit()
    if placed.replayed:
        response.status_code = 200
    return placed
