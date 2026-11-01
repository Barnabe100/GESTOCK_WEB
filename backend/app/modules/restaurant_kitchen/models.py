"""Postes de préparation d'un site et affectations du menu (palier R6, ADR-0050 K1).

- ``restaurant_prep_stations`` : poste d'UN site (Grillades, Boissons…), nom unique par site
  insensible à la casse, ordre d'affichage ; jamais supprimé (désactivation, réactivation de la
  même ligne) : une ligne de commande garde son poste (clé étrangère ``RESTRICT``).
- ``restaurant_station_routes`` : affectation d'une section OU d'un élément du menu du MÊME site
  à un poste (clés étrangères composites) ; une affectation au plus par section et par élément.
  Le menu n'est jamais modifié par la cuisine.
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
STATION_NAME_INDEX = "uq_restaurant_prep_stations_site_name"
ROUTE_SECTION_INDEX = "uq_restaurant_station_routes_section"
ROUTE_ITEM_INDEX = "uq_restaurant_station_routes_item"


def _site_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
    )


class PrepStation(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Poste de préparation d'UN site ; jamais supprimé."""

    __tablename__ = "restaurant_prep_stations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible des FK composites (affectations, lignes de commande) : même site.
        UniqueConstraint("tenant_id", "site_id", "id"),
        _site_fk(),
        Index(STATION_NAME_INDEX, "tenant_id", "site_id", func.lower(text("name")), unique=True),
        CheckConstraint("btrim(name) = name AND name <> ''", name="name_trimmed"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StationRoute(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Affectation d'une section OU d'un élément du menu du site à un poste du site."""

    __tablename__ = "restaurant_station_routes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        _site_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "station_id"],
            [
                "restaurant_prep_stations.tenant_id",
                "restaurant_prep_stations.site_id",
                "restaurant_prep_stations.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id", "section_id"],
            [
                "restaurant_menu_sections.tenant_id",
                "restaurant_menu_sections.site_id",
                "restaurant_menu_sections.id",
            ],
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
        CheckConstraint("num_nonnulls(section_id, menu_item_id) = 1", name="one_target"),
        Index(
            ROUTE_SECTION_INDEX,
            "tenant_id",
            "site_id",
            "section_id",
            unique=True,
            postgresql_where=text("section_id IS NOT NULL"),
        ),
        Index(
            ROUTE_ITEM_INDEX,
            "tenant_id",
            "site_id",
            "menu_item_id",
            unique=True,
            postgresql_where=text("menu_item_id IS NOT NULL"),
        ),
        Index("ix_restaurant_station_routes_station", "tenant_id", "station_id"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    station_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    section_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    menu_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
