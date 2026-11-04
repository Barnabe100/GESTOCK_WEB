"""Garde UNIQUE de l'accès public par QR Code (palier R7-B, ADR-0052).

À CHAQUE appel public, dans cet ordre :

1. format du secret (43 caractères base64url) — sinon refus sans aucune requête ;
2. recherche du point par l'EMPREINTE du jeton, la RLS ne laissant voir que cette ligne
   (politique ``qr_token_lookup`` : ``app.qr_token_hash``, aucune entreprise dans le contexte) ;
3. l'entreprise est alors connue PAR LE POINT : contexte RLS de cette entreprise, empreinte
   effacée ; toutes les lectures suivantes passent par la politique d'isolation ;
4. entreprise active, site actif, point actif ;
5. abonnement DU SITE en vigueur autorisant l'écriture (même calcul que pour un membre :
   licence en vigueur sinon plan, statut et politique d'accès) ;
6. modules effectifs du site (profil ∩ offre ∩ activations, dépendances, modules disponibles) :
   QR Code, commandes et menu ; pour un point de table, tables effectives et table ACTIVE du
   même site.

Tout échec donne la MÊME réponse (``404 qr_unavailable``) : rien n'indique au public quelle
vérification a échoué. Le motif est journalisé côté serveur avec l'identifiant du point, jamais
avec le jeton ni son empreinte. Connaître un jeton ne donne accès qu'au menu public de son site
et à la création de commandes sur ce point.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import set_db_context
from app.core.errors import NotFoundError
from app.modules.restaurant_orders.api import TableRef, table_directory
from app.modules.restaurant_qr.models import QrOrder, QrPoint, QrPointKind
from app.modules.restaurant_qr.tokens import secret_hash, well_formed
from app.platform.audit.service import RequestMeta
from app.platform.capabilities.service import CapabilityService
from app.platform.context import PublicScope
from app.platform.registry import ModuleRegistry
from app.platform.tenancy.models import Site, Tenant, TenantStatus
from app.platform.tenancy.restore_lock import tenant_available

logger = logging.getLogger(__name__)

QR_MODULE = "restaurant.qr"
REQUIRED_MODULES = (QR_MODULE, "restaurant.orders", "restaurant.menu")
TABLES_MODULE = "restaurant.tables"


class QrUnavailableError(NotFoundError):
    code = "qr_unavailable"
    title = "QR Code indisponible"

    def __init__(self) -> None:
        super().__init__(
            "Ce QR Code n'est pas disponible. Adressez-vous au personnel de l'établissement."
        )


@dataclass(frozen=True)
class QrAccess:
    scope: PublicScope
    point: QrPoint
    table: TableRef | None
    modules: frozenset[str]

    @property
    def site_id(self) -> uuid.UUID:
        return self.point.site_id


def _refuse(reason: str, point_id: uuid.UUID | None = None) -> QrUnavailableError:
    logger.info("Accès QR refusé : %s (point %s)", reason, point_id or "inconnu")
    return QrUnavailableError()


def resolve_access(
    db: Session,
    registry: ModuleRegistry,
    token: str | None,
    meta: RequestMeta,
    now: datetime,
) -> QrAccess:
    if token is None or not well_formed(token):
        raise _refuse("jeton malformé")
    set_db_context(db, tenant_id=None, user_id=None, qr_token_hash=secret_hash(token))
    point = db.scalars(select(QrPoint).where(QrPoint.token_hash == secret_hash(token))).first()
    if point is None:
        set_db_context(db, qr_token_hash=None)
        raise _refuse("jeton inconnu ou régénéré")
    # L'entreprise vient du POINT ; l'empreinte n'est plus nécessaire.
    set_db_context(db, tenant_id=point.tenant_id, qr_token_hash=None)
    tenant = db.get(Tenant, point.tenant_id)
    site = db.get(Site, point.site_id)
    if tenant is None or tenant.status is not TenantStatus.ACTIVE:
        raise _refuse("entreprise inactive", point.id)
    if not tenant_available(db, point.tenant_id):
        raise _refuse("restauration de l'entreprise en cours", point.id)
    if site is None or not site.is_active:
        raise _refuse("site inactif", point.id)
    if not point.is_active:
        raise _refuse("point désactivé", point.id)
    modules = CapabilityService(db, registry).public_site_modules(tenant, site.id, now)
    if modules is None:
        raise _refuse("abonnement du site sans droit d'écriture", point.id)
    if any(code not in modules for code in REQUIRED_MODULES):
        raise _refuse("module non effectif sur le site", point.id)
    scope = PublicScope(tenant=tenant, site=site, meta=meta)
    table: TableRef | None = None
    if point.kind is QrPointKind.TABLE:
        directory = table_directory(db, scope)
        if TABLES_MODULE not in modules or directory is None or point.table_id is None:
            raise _refuse("tables non effectives sur le site", point.id)
        table = directory.labels([point.table_id]).get(point.table_id)
        if table is None or table.site_id != site.id or not table.is_active:
            raise _refuse("table inactive", point.id)
    return QrAccess(scope=scope, point=point, table=table, modules=modules)


# --- Suivi public d'une commande (palier R7-E) ---------------------------------------------------


class TrackingUnavailableError(NotFoundError):
    code = "qr_tracking_unavailable"
    title = "Suivi indisponible"

    def __init__(self) -> None:
        super().__init__("Ce lien de suivi n'est plus disponible.")


@dataclass(frozen=True)
class TrackingAccess:
    scope: PublicScope
    link: QrOrder
    point: QrPoint | None
    table: TableRef | None


def resolve_tracking(
    db: Session,
    registry: ModuleRegistry,
    secret: str | None,
    meta: RequestMeta,
    now: datetime,
) -> TrackingAccess:
    """Secret de suivi (distinct du jeton du point) → commande, à CHAQUE appel : format,
    empreinte (politique ``qr_tracking_lookup`` : seule cette ligne visible), entreprise et site
    actifs, abonnement du site et module QR effectif. Le suivi survit à la régénération ou à la
    désactivation du point (décision R7) ; l'expiration (fin + délai) est contrôlée par
    l'appelant, qui lit l'état de la commande. Tout échec : ``404 qr_tracking_unavailable``."""
    if secret is None or not well_formed(secret):
        raise TrackingUnavailableError()
    set_db_context(db, tenant_id=None, user_id=None, qr_tracking_hash=secret_hash(secret))
    link = db.scalars(select(QrOrder).where(QrOrder.tracking_hash == secret_hash(secret))).first()
    if link is None:
        set_db_context(db, qr_tracking_hash=None)
        logger.info("Suivi QR refusé : secret inconnu ou renouvelé")
        raise TrackingUnavailableError()
    set_db_context(db, tenant_id=link.tenant_id, qr_tracking_hash=None)
    tenant = db.get(Tenant, link.tenant_id)
    site = db.get(Site, link.site_id)
    modules = (
        CapabilityService(db, registry).public_site_modules(tenant, link.site_id, now)
        if tenant is not None
        and tenant.status is TenantStatus.ACTIVE
        and tenant_available(db, link.tenant_id)
        else None
    )
    if site is None or not site.is_active or modules is None or QR_MODULE not in modules:
        logger.info("Suivi QR refusé : site, abonnement ou module (commande %s)", link.order_id)
        raise TrackingUnavailableError()
    assert tenant is not None
    scope = PublicScope(tenant=tenant, site=site, meta=meta)
    point = db.get(QrPoint, link.point_id)
    table: TableRef | None = None
    if point is not None and point.table_id is not None:
        directory = table_directory(db, scope)
        if directory is not None:
            table = directory.labels([point.table_id]).get(point.table_id)
    return TrackingAccess(scope=scope, link=link, point=point, table=table)
