"""Lectures et commande publiques par QR Code (palier R7-B / R7-C, ADR-0052).

Commande publique (``place_order``), dans cet ordre :

1. comptée par adresse IP (empreinte HMAC) et validée AVANT tout traitement ;
2. garde d'accès (jeton, point, entreprise, site, abonnement, modules, table) ;
3. comptée par point QR, validée ; la garde est ensuite REJOUÉE dans la nouvelle transaction ;
4. verrou consultatif du POINT (soumissions simultanées sur un même point sérialisées) puis
   réglages QR du site en partage ;
5. règles du site : nombre de lignes, quantité par ligne, nom et téléphone demandés ;
6. idempotence LIMITÉE AU POINT : même clé + même requête (empreinte du contenu) = aucune
   nouvelle commande, dans un délai court, avec un NOUVEAU secret de suivi (l'ancien lien cesse de
   fonctionner) ; même clé + autre contenu : refus. La clé est un UUID v4 aléatoire du
   navigateur : la connaître ne suffit pas (il faut aussi le jeton du point et le contenu exact) ;
7. plafond des commandes qui attendent le personnel sur ce point (compté sous le verrou) ;
8. création par le moteur UNIQUE des commandes (canal QR, « à confirmer » sauf acceptation
   automatique), lien de suivi enregistré (empreinte seule).

Les refus du moteur sont ASSAINIS : un élément non commandable ne révèle aucun motif interne ; tout
autre refus devient l'indisponibilité générique du QR Code.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.client_ip import client_ip
from app.core.config import Settings
from app.core.errors import AppError, BusinessRuleError, ConflictError
from app.core.public_urls import PublicUrls
from app.modules.restaurant_menu.api import site_menu
from app.modules.restaurant_orders.api import (
    LineInput,
    awaiting_staff_count,
    create_public_order,
    order_ref,
)
from app.modules.restaurant_qr.access import QrAccess, QrUnavailableError, resolve_access
from app.modules.restaurant_qr.models import (
    CLIENT_KEY_CONSTRAINT,
    ContactFieldMode,
    QrOrder,
    QrSiteSettings,
)
from app.modules.restaurant_qr.public_schemas import (
    PublicMenuItem,
    PublicMenuOut,
    PublicMenuSection,
    PublicOrderCreate,
    PublicOrdering,
    PublicOrderPlaced,
    PublicPoint,
)
from app.modules.restaurant_qr.settings import ensure_settings
from app.modules.restaurant_qr.tokens import new_secret, secret_hash
from app.platform.audit.service import RequestMeta
from app.platform.ratelimit.service import RateLimiter
from app.platform.registry import ModuleRegistry
from app.shared.text import normalize_phone

# Seul motif « non commandable » montré au public : épuisé (signalé par le personnel). Les
# autres motifs (article désactivé, hors assortiment, conditionnement sans prix…) sont internes :
# l'élément est simplement absent du menu public.
PUBLIC_BLOCKERS = frozenset({"unavailable"})
ORDER_IP_BUCKET = "qr.order.ip"
ORDER_POINT_BUCKET = "qr.order.point"
# Une même requête n'est rejouée (nouveau lien de suivi) que peu après son premier envoi.
REPLAY_WINDOW = timedelta(minutes=15)
# Clés d'idempotence des commandes QR : dérivées du point (jamais égales à une clé du personnel).
QR_KEY_NAMESPACE = uuid.UUID("6f1d3c52-6d1f-4a39-9a3e-52b7a1c0e7a4")
_PHONE_RE = re.compile(r"\+?\d{6,15}")
# Refus du moteur traduits pour le public (aucun motif interne).
_ITEM_REFUSALS = frozenset({"menu_item_not_found", "menu_item_not_orderable"})


def point_label(access: QrAccess) -> str:
    if access.table is not None:
        return access.table.label
    return access.point.label or ""


def _ordering(settings: QrSiteSettings) -> PublicOrdering:
    return PublicOrdering(
        max_lines=settings.max_lines,
        max_quantity_per_line=settings.max_quantity_per_line,
        customer_name_mode=settings.customer_name_mode,
        customer_phone_mode=settings.customer_phone_mode,
    )


def public_menu(db: Session, access: QrAccess) -> PublicMenuOut:
    sections, items = site_menu(db, access.scope, access.site_id)
    by_section: dict[object, list[PublicMenuItem]] = {s.id: [] for s in sections}
    for item in items:
        if item.price is None or not set(item.blockers) <= PUBLIC_BLOCKERS:
            continue
        by_section[item.section_id].append(
            PublicMenuItem(
                id=item.id,
                name=item.display_name or item.designation,
                packaging_name=item.packaging_name,
                description=item.description,
                price=item.price,
                available=item.orderable,
            )
        )
    tenant = access.scope.tenant
    return PublicMenuOut(
        establishment=tenant.trade_name or tenant.name,
        site_name=access.scope.site.name,
        point=PublicPoint(kind=access.point.kind, label=point_label(access)),
        currency=tenant.currency,
        sections=[
            PublicMenuSection(id=s.id, name=s.name, items=by_section[s.id])
            for s in sections
            if by_section[s.id]
        ],
        ordering=_ordering(ensure_settings(db, tenant.id, access.site_id)),
    )


# --- Commande publique --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaceOrderContext:
    db: Session
    settings: Settings
    registry: ModuleRegistry
    request: Request
    meta: RequestMeta
    now: datetime


def request_hash(body: PublicOrderCreate, phone: str | None) -> str:
    """Empreinte du CONTENU de la requête (ordre des lignes compris), clé exclue."""
    canonical = json.dumps(
        {
            "lines": [
                [str(line.menu_item_id), line.quantity, line.note or ""] for line in body.lines
            ],
            "name": body.customer_name or "",
            "phone": phone or "",
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _hit(ctx: PlaceOrderContext, bucket: str, key: str, limit: int) -> None:
    RateLimiter(ctx.db, ctx.now, ctx.settings.rate_limit_hmac_key.get_secret_value()).hit(
        bucket, key, limit=limit, window=timedelta(minutes=ctx.settings.qr_order_window_minutes)
    )
    ctx.db.commit()


def _contact(body: PublicOrderCreate, settings: QrSiteSettings) -> tuple[str | None, str | None]:
    """Nom et téléphone selon le réglage du site : jamais conservés s'ils ne sont pas demandés
    (minimisation), obligatoires s'ils sont exigés ; téléphone normalisé (séparateurs retirés,
    aucun indicatif ajouté)."""
    name = (
        body.customer_name if settings.customer_name_mode is not ContactFieldMode.HIDDEN else None
    )
    if settings.customer_name_mode is ContactFieldMode.REQUIRED and not name:
        raise BusinessRuleError("Indiquez votre nom", code="qr_order_name_required")
    phone: str | None = None
    if settings.customer_phone_mode is not ContactFieldMode.HIDDEN and body.customer_phone:
        try:
            phone = normalize_phone(body.customer_phone)
        except ValueError:
            phone = None
        if phone is None or not _PHONE_RE.fullmatch(phone):
            raise BusinessRuleError("Numéro de téléphone invalide", code="qr_order_phone_invalid")
    if settings.customer_phone_mode is ContactFieldMode.REQUIRED and not phone:
        raise BusinessRuleError(
            "Indiquez votre numéro de téléphone", code="qr_order_phone_required"
        )
    return name, phone


def _check_limits(body: PublicOrderCreate, settings: QrSiteSettings) -> None:
    if len(body.lines) > settings.max_lines:
        raise BusinessRuleError(
            "Trop de lignes dans la commande",
            code="qr_order_too_many_lines",
            extra={"max_lines": settings.max_lines},
        )
    if any(line.quantity > settings.max_quantity_per_line for line in body.lines):
        raise BusinessRuleError(
            "Quantité trop élevée",
            code="qr_order_quantity_too_large",
            extra={"max_quantity_per_line": settings.max_quantity_per_line},
        )


def _issue_tracking(link: QrOrder, now: datetime) -> str:
    secret = new_secret()
    link.tracking_hash = secret_hash(secret)
    link.tracking_issued_at = now
    return secret


def place_order(
    ctx: PlaceOrderContext, token: str | None, body: PublicOrderCreate
) -> PublicOrderPlaced:
    db, settings = ctx.db, ctx.settings
    _hit(ctx, ORDER_IP_BUCKET, client_ip(ctx.request, settings), settings.qr_order_ip_limit)
    access = resolve_access(db, ctx.registry, token, ctx.meta, ctx.now)
    _hit(ctx, ORDER_POINT_BUCKET, str(access.point.id), settings.qr_order_point_limit)
    # Nouvelle transaction : la garde est rejouée (aucun état lu avant la validation du compteur).
    access = resolve_access(db, ctx.registry, token, ctx.meta, ctx.now)
    point = access.point
    db.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"restaurant.qr:{point.id}", 0)))
    )
    site_settings = ensure_settings(db, point.tenant_id, point.site_id, lock="share")
    _check_limits(body, site_settings)
    name, phone = _contact(body, site_settings)
    fingerprint = request_hash(body, phone)
    urls = PublicUrls(settings)
    urls.qr_tracking("")  # adresse publique configurée, avant toute écriture

    existing = db.scalars(
        select(QrOrder).where(
            QrOrder.tenant_id == point.tenant_id,
            QrOrder.point_id == point.id,
            QrOrder.client_key == body.client_key,
        )
    ).one_or_none()
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise ConflictError(
                "Cette commande a déjà été envoyée avec un autre contenu",
                code="qr_order_key_reused",
            )
        if existing.created_at < ctx.now - REPLAY_WINDOW:
            raise ConflictError("Cette commande a déjà été envoyée", code="qr_order_already_sent")
        ref = order_ref(db, existing.order_id)
        secret = _issue_tracking(existing, ctx.now)
        db.flush()
        return PublicOrderPlaced(
            daily_number=ref.daily_number,
            status=ref.status,
            tracking_url=urls.qr_tracking(secret),
            replayed=True,
        )

    if (
        awaiting_staff_count(db, select(QrOrder.order_id).where(QrOrder.point_id == point.id))
        >= site_settings.max_pending_per_point
    ):
        raise ConflictError(
            "Des commandes de cette table ou de ce comptoir attendent encore le personnel",
            code="qr_order_pending_limit",
            extra={"max_pending_per_point": site_settings.max_pending_per_point},
        )
    try:
        ref = create_public_order(
            db,
            access.scope,
            ctx.now,
            point_id=point.id,
            table_id=point.table_id,
            call_name=name,
            contact_phone=phone,
            lines=[
                LineInput(menu_item_id=line.menu_item_id, quantity=line.quantity, note=line.note)
                for line in body.lines
            ],
            idempotency_key=uuid.uuid5(QR_KEY_NAMESPACE, f"{point.id}:{body.client_key}"),
        )
    except QrUnavailableError:
        raise
    except AppError as exc:
        raise _public_refusal(exc) from exc
    link = QrOrder(
        tenant_id=point.tenant_id,
        site_id=point.site_id,
        point_id=point.id,
        order_id=ref.id,
        client_key=body.client_key,
        request_hash=fingerprint,
        tracking_hash="0" * 64,
        tracking_issued_at=ctx.now,
        created_at=ctx.now,
    )
    secret = _issue_tracking(link, ctx.now)
    db.add(link)
    try:
        db.flush()
    except IntegrityError as exc:  # protégé par le verrou du point ; refus sûr sinon
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == CLIENT_KEY_CONSTRAINT:
            raise ConflictError("Commande déjà envoyée", code="qr_order_already_sent") from exc
        raise
    return PublicOrderPlaced(
        daily_number=ref.daily_number,
        status=ref.status,
        tracking_url=urls.qr_tracking(secret),
        replayed=False,
    )


def _public_refusal(exc: AppError) -> AppError:
    """Refus du moteur traduit pour le public : identifiants des éléments seulement (aucun motif
    interne : assortiment, article, conditionnement…), sinon indisponibilité générique."""
    if exc.code in _ITEM_REFUSALS:
        items = [
            str(entry["id"]) if isinstance(entry, dict) else str(entry)
            for entry in exc.extra.get("items", [])
        ]
        return BusinessRuleError(
            "Certains articles ne sont plus disponibles",
            code="qr_order_item_unavailable",
            extra={"items": items},
        )
    return QrUnavailableError()
