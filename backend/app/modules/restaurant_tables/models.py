"""Zones et tables d'un site de restauration (palier R5-A, ADR-0049 ; ``RESTAURANT.md`` §4).

Configuration d'UN site, facultative : une commande n'exige jamais de table (D2). Une zone
regroupe des tables (Salle, Terrasse…) ; la zone d'une table est facultative (A4). Libellé de
table unique par site, toutes zones confondues (« Table 4 » sans ambiguïté sur un ticket) ; nom
de zone unique par site ; insensibles à la casse. Rien n'est jamais supprimé (désactivation,
réactivation de la même ligne) : une commande close garde sa table (clé étrangère ``RESTRICT``).
"""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin

# Contraintes nommées explicitement : leurs noms sont traduits en refus métier (409).
ZONE_NAME_INDEX = "uq_restaurant_zones_site_name"
TABLE_LABEL_INDEX = "uq_restaurant_tables_site_label"
MAX_SEATS = 999


def _site_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
    )


class RestaurantZone(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Zone d'UN site (Salle, Terrasse…). Nom unique par site, insensible à la casse ; jamais
    supprimée (désactivation, refusée tant qu'elle contient des tables actives)."""

    __tablename__ = "restaurant_zones"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible de la FK composite des tables : la zone d'un autre site est inutilisable en base.
        UniqueConstraint("tenant_id", "site_id", "id"),
        _site_fk(),
        Index(ZONE_NAME_INDEX, "tenant_id", "site_id", func.lower(text("name")), unique=True),
        CheckConstraint("btrim(name) = name AND name <> ''", name="name_trimmed"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RestaurantTable(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Table d'UN site : libellé unique par site (insensible à la casse), zone facultative du
    MÊME site (FK composite), nombre de places facultatif ; jamais supprimée."""

    __tablename__ = "restaurant_tables"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible de la FK composite des commandes (``restaurant_orders.table_id``, même site).
        UniqueConstraint("tenant_id", "site_id", "id"),
        _site_fk(),
        # Zone facultative : contrôlée seulement si renseignée (MATCH SIMPLE), toujours du site.
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "zone_id"],
            ["restaurant_zones.tenant_id", "restaurant_zones.site_id", "restaurant_zones.id"],
            ondelete="RESTRICT",
        ),
        Index(TABLE_LABEL_INDEX, "tenant_id", "site_id", func.lower(text("label")), unique=True),
        Index("ix_restaurant_tables_zone", "tenant_id", "zone_id"),
        CheckConstraint("btrim(label) = label AND label <> ''", name="label_trimmed"),
        CheckConstraint(f"seats IS NULL OR seats BETWEEN 1 AND {MAX_SEATS}", name="seats_range"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    seats: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
