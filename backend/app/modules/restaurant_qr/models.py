"""Points QR d'un site (palier R7-A, ADR-0052).

``restaurant_qr_points`` : point d'accès public d'UN site — une TABLE existante du site (clé
étrangère composite, même site) ou un COMPTOIR (libellé propre). Le jeton (256 bits) n'est
JAMAIS stocké : seule son empreinte SHA-256 l'est (``token_hash``, unique). Régénérer remplace
l'empreinte (les QR imprimés auparavant cessent aussitôt de fonctionner) ; désactiver rend le
point inutilisable sans changer son jeton. Jamais supprimé.

Accès public : la politique ``qr_token_lookup`` (rôle applicatif, lecture seule) ne rend
visible que la ligne dont l'empreinte est égale à ``app.qr_token_hash`` ; l'entreprise est
ensuite connue par cette ligne et toutes les lectures suivent la politique d'isolation.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin, str_enum

# Contraintes nommées explicitement : leurs noms sont traduits en refus métier (409).
POINT_TABLE_INDEX = "uq_restaurant_qr_points_active_table"
POINT_LABEL_INDEX = "uq_restaurant_qr_points_site_label"
TOKEN_HASH_INDEX = "uq_restaurant_qr_points_token_hash"


class QrPointKind(StrEnum):
    TABLE = "TABLE"
    COUNTER = "COUNTER"


class QrPoint(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "restaurant_qr_points"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible des FK composites des liens de suivi (même site).
        UniqueConstraint("tenant_id", "site_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        # Table du MÊME site (MATCH SIMPLE : contrôlée seulement pour un point de table).
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "table_id"],
            ["restaurant_tables.tenant_id", "restaurant_tables.site_id", "restaurant_tables.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(kind = 'TABLE' AND table_id IS NOT NULL AND label IS NULL) OR "
            "(kind = 'COUNTER' AND table_id IS NULL AND label IS NOT NULL)",
            name="kind_target",
        ),
        CheckConstraint(
            "label IS NULL OR (btrim(label) = label AND label <> '')", name="label_trimmed"
        ),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_format"),
        # Un seul point ACTIF par table.
        Index(
            POINT_TABLE_INDEX,
            "tenant_id",
            "table_id",
            unique=True,
            postgresql_where=text("is_active AND table_id IS NOT NULL"),
        ),
        Index(
            POINT_LABEL_INDEX,
            "tenant_id",
            "site_id",
            func.lower(text("label")),
            unique=True,
            postgresql_where=text("label IS NOT NULL"),
        ),
        # Unique sur TOUTE la plateforme : une empreinte désigne un seul point.
        Index(TOKEN_HASH_INDEX, "token_hash", unique=True),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[QrPointKind] = mapped_column(
        str_enum(QrPointKind, "restaurant_qr_point_kind"), nullable=False
    )
    table_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    label: Mapped[str | None] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_generated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


# --- Réglages QR d'un site (palier R7-C) -------------------------------------------------------

# Plafonds de la PLATEFORME (jamais dépassables par un réglage de site ; contrôles en base).
PLATFORM_MAX_LINES = 100
PLATFORM_MAX_QUANTITY = 99
PLATFORM_MAX_PENDING = 20
# Valeurs initiales d'un site (décision de l'utilisateur, R7).
DEFAULT_MAX_LINES = 30
DEFAULT_MAX_QUANTITY = 20
DEFAULT_MAX_PENDING = 3


class ContactFieldMode(StrEnum):
    """Information demandée au client : jamais (minimisation), facultative ou obligatoire."""

    HIDDEN = "HIDDEN"
    OPTIONAL = "OPTIONAL"
    REQUIRED = "REQUIRED"


class QrSiteSettings(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Réglages QR d'UN site : créés à l'activation du module (``site_setup``), conservés par
    une désactivation ; modifiés avec ``restaurant.qr.settings.manage`` (audit avant / après)."""

    __tablename__ = "restaurant_qr_site_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "site_id"),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
        ),
        CheckConstraint(f"max_lines BETWEEN 1 AND {PLATFORM_MAX_LINES}", name="max_lines_range"),
        CheckConstraint(
            f"max_quantity_per_line BETWEEN 1 AND {PLATFORM_MAX_QUANTITY}",
            name="max_quantity_range",
        ),
        CheckConstraint(
            f"max_pending_per_point BETWEEN 1 AND {PLATFORM_MAX_PENDING}",
            name="max_pending_range",
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    max_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    max_quantity_per_line: Mapped[int] = mapped_column(Integer, nullable=False)
    max_pending_per_point: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_name_mode: Mapped[ContactFieldMode] = mapped_column(
        str_enum(ContactFieldMode, "restaurant_qr_name_mode"), nullable=False
    )
    customer_phone_mode: Mapped[ContactFieldMode] = mapped_column(
        str_enum(ContactFieldMode, "restaurant_qr_phone_mode"), nullable=False
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


# --- Commandes reçues par un point QR et lien de suivi (R7-C, R7-E) ----------------------------

TRACKING_HASH_INDEX = "uq_restaurant_qr_orders_tracking_hash"
CLIENT_KEY_CONSTRAINT = "uq_restaurant_qr_orders_tenant_id_point_id_client_key"


class QrOrder(IdMixin, TenantScopedMixin, Base):
    """Commande créée par un point QR : clé du client (limitée au point), empreinte de la requête
    (une même clé n'est rejouée que pour la MÊME requête) et empreinte du secret de suivi
    (jamais le secret ; remplacée à chaque émission). La commande elle-même appartient au module
    des commandes (FK composite, même site)."""

    __tablename__ = "restaurant_qr_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "order_id"),
        UniqueConstraint("tenant_id", "point_id", "client_key", name=CLIENT_KEY_CONSTRAINT),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "point_id"],
            [
                "restaurant_qr_points.tenant_id",
                "restaurant_qr_points.site_id",
                "restaurant_qr_points.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "order_id"],
            ["restaurant_orders.tenant_id", "restaurant_orders.site_id", "restaurant_orders.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("tracking_hash ~ '^[0-9a-f]{64}$'", name="tracking_hash_format"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="request_hash_format"),
        Index(TRACKING_HASH_INDEX, "tracking_hash", unique=True),
        Index("ix_restaurant_qr_orders_point", "tenant_id", "point_id", "created_at"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    point_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    client_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
