"""Commandes de restauration (palier R2, ADR-0049 ; ``RESTAURANT.md`` §4).

- ``restaurant_site_settings`` : réglages du module sur UN site (mode de paiement, protection et
  délai entre prises, acceptation automatique QR), créés à l'activation (``site_setup``) depuis
  ``module_settings`` du profil, jamais écrasés ensuite ; jamais supprimés par une
  désactivation.
- ``restaurant_orders`` : la commande, document du moteur unique (canal posé par la route),
  numéro court par site et par jour de l'entreprise ; mode de paiement RECOPIÉ à la création ;
  aucune copie de l'état financier (lu sur la vente, R2-D).
- ``restaurant_order_lines`` : une présentation du menu, instantané figé à la création (libellé,
  conversion, prix) ; seul l'état évolue.
- ``restaurant_order_events`` : historique en ajout seul.

États finaux protégés en base (déclencheur ``trg_restaurant_final_state``) : ligne servie ou
annulée, commande close, annulée ou refusée ; instantané d'une ligne jamais modifié.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum

MONEY = Numeric(18, 2)
QUANTITY = Numeric(18, 3)

# Contraintes nommées : leurs noms sont traduits en refus métier.
ORDER_NUMBER_CONSTRAINT = "uq_restaurant_orders_site_day_number"
ORDER_IDEMPOTENCY_CONSTRAINT = "uq_restaurant_orders_site_idempotency"
EVENT_IDEMPOTENCY_CONSTRAINT = "uq_restaurant_order_events_idempotency"


class PaymentTiming(StrEnum):
    AT_END = "AT_END"  # règlement à tout moment
    AT_ORDER = "AT_ORDER"  # règlement avant la préparation


class OrderChannel(StrEnum):
    STAFF = "STAFF"  # personnel (palier R2)
    POS = "POS"  # écran du point de vente (ultérieur, D14 Q2)
    QR = "QR"  # commande publique (R7)


class ServiceMode(StrEnum):
    ON_SITE = "ON_SITE"
    COUNTER = "COUNTER"
    TAKEAWAY = "TAKEAWAY"


class OrderStatus(StrEnum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"  # QR (R7)
    OPEN = "OPEN"
    CLOSED = "CLOSED"  # réglée ET lignes non annulées servies : définitif
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"  # QR refusée (R7)


FINAL_ORDER_STATUSES = (OrderStatus.CLOSED, OrderStatus.CANCELLED, OrderStatus.REJECTED)
ACTIVE_ORDER_STATUSES = (OrderStatus.PENDING_CONFIRMATION, OrderStatus.OPEN)


class SettlementStatus(StrEnum):
    UNSETTLED = "UNSETTLED"
    SETTLED = "SETTLED"  # vente issue de la commande validée (R2-D)


class LineStatus(StrEnum):
    RECEIVED = "RECEIVED"
    IN_PREPARATION = "IN_PREPARATION"
    READY = "READY"
    SERVED = "SERVED"  # « Servie » sur place, « Remise » au comptoir / à emporter : définitif
    CANCELLED = "CANCELLED"  # définitif


FINAL_LINE_STATUSES = (LineStatus.SERVED, LineStatus.CANCELLED)


class PrepStatus(StrEnum):
    """Résumé de préparation d'une commande, tenu par le service sous le verrou de la commande :
    état de la ligne active la MOINS avancée ; ``SERVED`` quand toutes les lignes non annulées
    sont servies ; ``NONE`` sans ligne active."""

    RECEIVED = "RECEIVED"
    IN_PREPARATION = "IN_PREPARATION"
    READY = "READY"
    SERVED = "SERVED"
    NONE = "NONE"


class EventType(StrEnum):
    CREATED = "CREATED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CLAIMED = "CLAIMED"
    REASSIGNED = "REASSIGNED"
    LINES_ADDED = "LINES_ADDED"
    PREP_STARTED = "PREP_STARTED"
    READY = "READY"
    READY_REVERTED = "READY_REVERTED"
    SERVED = "SERVED"
    LINE_CANCELLED = "LINE_CANCELLED"
    SETTLED = "SETTLED"
    SALE_CANCELLED = "SALE_CANCELLED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    # Association tardive d'un client (R2-E, Z1 ; migration 0044).
    CUSTOMER_SET = "CUSTOMER_SET"


class ActorKind(StrEnum):
    STAFF = "STAFF"
    PUBLIC = "PUBLIC"
    SYSTEM = "SYSTEM"


def _site_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
    )


def _reason_check(status_column: str, statuses: str) -> CheckConstraint:
    return CheckConstraint(
        f"({status_column} NOT IN ({statuses})) OR (cancelled_at IS NOT NULL "
        "AND cancelled_by IS NOT NULL AND cancel_reason IS NOT NULL)",
        name="cancelled_has_reason",
    )


class RestaurantSiteSettings(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Réglages des commandes d'UN site (une ligne par site)."""

    __tablename__ = "restaurant_site_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "site_id"),
        _site_fk(),
        CheckConstraint(
            "claim_protection_minutes BETWEEN 0 AND 1440", name="claim_protection_range"
        ),
        CheckConstraint("claim_cooldown_minutes BETWEEN 0 AND 1440", name="claim_cooldown_range"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payment_timing: Mapped[PaymentTiming] = mapped_column(
        str_enum(PaymentTiming, "restaurant_payment_timing"), nullable=False
    )
    claim_protection_minutes: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    claim_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qr_auto_accept: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RestaurantOrder(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "restaurant_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible des FK composites des lignes et des évènements (même site).
        UniqueConstraint("tenant_id", "site_id", "id"),
        UniqueConstraint(
            "tenant_id", "site_id", "business_date", "daily_number", name=ORDER_NUMBER_CONSTRAINT
        ),
        UniqueConstraint(
            "tenant_id", "site_id", "idempotency_key", name=ORDER_IDEMPOTENCY_CONSTRAINT
        ),
        _site_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        # Vente active : du même tenant ET du même site que la commande (migration 0043, R2-D).
        ForeignKeyConstraint(
            ["tenant_id", "sale_id", "site_id"],
            ["sales.tenant_id", "sales.id", "sales.site_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("daily_number > 0", name="daily_number_positive"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "call_name IS NULL OR (btrim(call_name) = call_name AND call_name <> '')",
            name="call_name_trimmed",
        ),
        CheckConstraint(
            "cancel_reason IS NULL OR btrim(cancel_reason) <> ''", name="cancel_reason_not_blank"
        ),
        _reason_check("status", "'CANCELLED', 'REJECTED'"),
        CheckConstraint(
            "status <> 'CLOSED' OR (settlement_status = 'SETTLED' AND closed_at IS NOT NULL)",
            name="closed_is_settled",
        ),
        CheckConstraint(
            "settlement_status <> 'SETTLED' OR sale_id IS NOT NULL", name="settled_has_sale"
        ),
        CheckConstraint(
            "(assigned_user_id IS NULL) = (assigned_at IS NULL)", name="assignment_complete"
        ),
        # Ancienneté des commandes « à régler » (limites de V1, §6).
        Index(
            "ix_restaurant_orders_backlog",
            "tenant_id",
            "site_id",
            "settlement_status",
            "status",
            "created_at",
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    # Jour de l'entreprise (fuseau du tenant, ADR-0028) et numéro court de ce jour.
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    daily_number: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[OrderChannel] = mapped_column(
        str_enum(OrderChannel, "restaurant_order_channel"), nullable=False
    )
    service_mode: Mapped[ServiceMode] = mapped_column(
        str_enum(ServiceMode, "restaurant_service_mode"), nullable=False
    )
    # Table (R5) : clé étrangère ajoutée avec les tables.
    table_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    call_name: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[OrderStatus] = mapped_column(
        str_enum(OrderStatus, "restaurant_order_status"), nullable=False
    )
    prep_status: Mapped[PrepStatus] = mapped_column(
        str_enum(PrepStatus, "restaurant_prep_status"), nullable=False
    )
    settlement_status: Mapped[SettlementStatus] = mapped_column(
        str_enum(SettlementStatus, "restaurant_settlement_status"), nullable=False
    )
    payment_timing: Mapped[PaymentTiming] = mapped_column(
        str_enum(PaymentTiming, "restaurant_order_payment_timing"), nullable=False
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    # Vente ACTIVE issue de la commande (R2-D) ; unicité portée par les ventes
    # (``uq_sales_active_origin``) ; remise à nul par l'annulation de la vente (Z3).
    sale_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    # Informatif (P-7) : incrémenté à chaque écriture, jamais exigé en entrée.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class RestaurantOrderLine(IdMixin, TenantScopedMixin, Base):
    __tablename__ = "restaurant_order_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "order_id", "line_no"),
        # Ligne du MÊME site que sa commande et que l'élément de menu (FK composites).
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "order_id"],
            ["restaurant_orders.tenant_id", "restaurant_orders.site_id", "restaurant_orders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "menu_item_id"],
            [
                "restaurant_menu_items.tenant_id",
                "restaurant_menu_items.site_id",
                "restaurant_menu_items.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "article_id", "packaging_id"],
            [
                "catalog_packagings.tenant_id",
                "catalog_packagings.article_id",
                "catalog_packagings.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("line_no > 0", name="line_no_positive"),
        CheckConstraint("quantity > 0 AND base_quantity > 0", name="quantities_positive"),
        CheckConstraint("unit_price >= 0 AND line_total >= 0", name="amounts_non_negative"),
        CheckConstraint("(packaging_id IS NULL) = (conversion IS NULL)", name="packaging_snapshot"),
        CheckConstraint(
            "cancel_reason IS NULL OR btrim(cancel_reason) <> ''", name="cancel_reason_not_blank"
        ),
        _reason_check("status", "'CANCELLED'"),
        CheckConstraint(
            "status <> 'SERVED' OR (served_at IS NOT NULL AND served_by IS NOT NULL)",
            name="served_complete",
        ),
        Index("ix_restaurant_order_lines_order", "tenant_id", "order_id"),
        Index("ix_restaurant_order_lines_article", "tenant_id", "site_id", "article_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    menu_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # Instantané figé à la création (jamais relu dans le catalogue ni le menu).
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    packaging_name: Mapped[str | None] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    conversion: Mapped[Decimal | None] = mapped_column(QUANTITY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    base_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[LineStatus] = mapped_column(
        str_enum(LineStatus, "restaurant_line_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    prepared_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))


class RestaurantOrderEvent(IdMixin, TenantScopedMixin, Base):
    """Historique d'une commande, en ajout seul (``SELECT`` + ``INSERT`` pour le rôle
    applicatif). La dernière prise d'un employé (délai entre prises, D7) s'y lit."""

    __tablename__ = "restaurant_order_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Ajout de lignes rejouable (P-8) : une clé ne vaut qu'une fois par commande.
        UniqueConstraint(
            "tenant_id", "order_id", "idempotency_key", name=EVENT_IDEMPOTENCY_CONSTRAINT
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "order_id"],
            ["restaurant_orders.tenant_id", "restaurant_orders.site_id", "restaurant_orders.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(actor_kind = 'STAFF') = (actor_user_id IS NOT NULL)", name="staff_actor_known"
        ),
        Index("ix_restaurant_order_events_order", "tenant_id", "order_id", "occurred_at"),
        Index(
            "ix_restaurant_order_events_actor",
            "tenant_id",
            "site_id",
            "actor_user_id",
            "event_type",
            text("occurred_at DESC"),
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        str_enum(EventType, "restaurant_order_event_type"), nullable=False
    )
    actor_kind: Mapped[ActorKind] = mapped_column(
        str_enum(ActorKind, "restaurant_actor_kind"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(String(500))
    line_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
