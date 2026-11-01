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
