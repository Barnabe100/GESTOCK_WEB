"""Réglages des commandes d'un site et numérotation (palier R2, ADR-0049 D14).

- Réglages créés à l'activation du module sur un site (``site_setup``) depuis
  ``module_settings`` du profil du site, jamais écrasés ensuite (une réactivation retrouve les
  réglages conservés ; une désactivation ne supprime rien). Un site dont l'activation est
  antérieure à la livraison du module (activation inerte, E.1) reçoit ses réglages au premier
  usage (``ensure_settings``).
- Numéro court par site et par jour de l'entreprise : compteur ``ro:{site_id}:{AAAAMMJJ}``
  (48 caractères, sous la limite de 50 de ``document_sequences.sequence_key``) ; identifiant du
  site (jamais son code ni son nom : la clé est stable) ; jamais préfixé par ``{site_id}:``, qui
  figerait le code du site (``site_has_numbers``).
"""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.restaurant_orders.models import PaymentTiming, RestaurantSiteSettings
from app.platform.catalog.models import BusinessProfile
from app.platform.sequences.service import next_value
from app.shared.ids import new_id

MODULE = "restaurant.orders"
ORDER_SEQUENCE_PREFIX = "ro"


def order_sequence_key(site_id: uuid.UUID, business_date: date) -> str:
    """Clé du compteur des numéros de commande d'un site pour un jour de l'entreprise."""
    return f"{ORDER_SEQUENCE_PREFIX}:{site_id}:{business_date:%Y%m%d}"


def next_order_number(
    db: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, business_date: date
) -> int:
    """Numéro suivant (1, 2, 3…) du site pour ce jour ; ligne du compteur verrouillée jusqu'à la
    fin de la transaction (créations concurrentes sérialisées, numéro rendu si elle échoue)."""
    return next_value(db, tenant_id, order_sequence_key(site_id, business_date))


def business_date(timezone: str, now: datetime) -> date:
    """Jour de l'entreprise (fuseau du tenant, ADR-0028), jamais celui du serveur."""
    return now.astimezone(ZoneInfo(timezone)).date()


def default_payment_timing(profile: BusinessProfile | None) -> PaymentTiming:
    """Paiement par défaut porté par ``module_settings`` du profil (D11) ; à défaut « à la fin »
    (les valeurs des profils sont contrôlées par un test du catalogue)."""
    raw = ((profile.module_settings or {}) if profile is not None else {}).get(MODULE, {})
    try:
        return PaymentTiming(raw.get("payment_timing", PaymentTiming.AT_END))
    except ValueError:
        return PaymentTiming.AT_END


def _insert_defaults(
    session: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, profile: BusinessProfile | None
) -> None:
    session.execute(
        insert(RestaurantSiteSettings)
        .values(
            id=new_id(),
            tenant_id=tenant_id,
            site_id=site_id,
            payment_timing=default_payment_timing(profile),
            claim_protection_minutes=5,
            claim_cooldown_minutes=0,
            qr_auto_accept=False,
        )
        .on_conflict_do_nothing(
            index_elements=[RestaurantSiteSettings.tenant_id, RestaurantSiteSettings.site_id]
        )
    )


def site_setup(
    session: Session, tenant_id: uuid.UUID, site_id: uuid.UUID, profile: BusinessProfile
) -> None:
    """``site_setup`` du manifeste : réglages du site créés s'ils manquent, jamais écrasés."""
    _insert_defaults(session, tenant_id, site_id, profile)


def ensure_settings(
    session: Session,
    tenant_id: uuid.UUID,
    site_id: uuid.UUID,
    profile: BusinessProfile | None,
    *,
    lock: str | None = None,
) -> RestaurantSiteSettings:
    """Réglages du site, créés au premier usage s'ils manquent. ``lock`` : ``"share"`` (création
    d'une commande — un changement de profil les prend en exclusif) ou ``"update"``."""
    stmt = select(RestaurantSiteSettings).where(
        RestaurantSiteSettings.tenant_id == tenant_id, RestaurantSiteSettings.site_id == site_id
    )
    if lock == "share":
        stmt = stmt.with_for_update(read=True)
    elif lock == "update":
        stmt = stmt.with_for_update()
    settings = session.scalars(stmt.execution_options(populate_existing=True)).one_or_none()
    if settings is None:
        _insert_defaults(session, tenant_id, site_id, profile)
        settings = session.scalars(stmt.execution_options(populate_existing=True)).one()
    return settings
