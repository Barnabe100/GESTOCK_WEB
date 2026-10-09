"""Menu d'un site (palier R1, ADR-0049).

Le menu d'un site présente des produits du catalogue GLOBAL de l'entreprise, sans les dupliquer :
un élément de menu référence une présentation (article en unité de base, ou conditionnement de
l'article). Aucun prix par site, aucune image (V1) : le prix affiché est celui du catalogue.
Sections et éléments ne sont jamais supprimés (désactivation, réactivation de la même ligne).
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
SECTION_NAME_INDEX = "uq_restaurant_menu_sections_site_name"
ITEM_PRESENTATION_CONSTRAINT = "uq_restaurant_menu_items_site_presentation"


def _site_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "site_id"], ["sites.tenant_id", "sites.id"], ondelete="RESTRICT"
    )


class MenuSection(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Section du menu d'UN site (Entrées, Boissons…). Nom unique par site, insensible à la
    casse ; jamais supprimée (désactivation)."""

    __tablename__ = "restaurant_menu_sections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        # Cible de la FK composite des éléments : la section d'un autre site est inutilisable
        # en base.
        UniqueConstraint("tenant_id", "site_id", "id"),
        _site_fk(),
        Index(SECTION_NAME_INDEX, "tenant_id", "site_id", func.lower(text("name")), unique=True),
        CheckConstraint("btrim(name) = name AND name <> ''", name="name_trimmed"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MenuItem(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Élément du menu d'UN site : une présentation d'un article du catalogue (``packaging_id``
    nul = unité de base). Une présentation figure au plus une fois au menu d'un site, y compris
    l'unité de base (``NULLS NOT DISTINCT``). L'élément est « commandable » ou non selon l'état
    COURANT du catalogue et de l'assortiment (calculé, jamais stocké) ; ``available`` porte
    l'« épuisé » manuel."""

    __tablename__ = "restaurant_menu_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint(
            "tenant_id",
            "site_id",
            "article_id",
            "packaging_id",
            name=ITEM_PRESENTATION_CONSTRAINT,
            postgresql_nulls_not_distinct=True,
        ),
        _site_fk(),
        # Section du MÊME site (FK composite).
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
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            ondelete="RESTRICT",
        ),
        # Conditionnement DE L'ARTICLE (non contrôlée quand ``packaging_id`` est nul).
        ForeignKeyConstraint(
            ["tenant_id", "article_id", "packaging_id"],
            [
                "catalog_packagings.tenant_id",
                "catalog_packagings.article_id",
                "catalog_packagings.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "display_name IS NULL OR (btrim(display_name) = display_name AND display_name <> '')",
            name="display_name_trimmed",
        ),
        # Motif de l'« épuisé » : seulement pour un élément épuisé, jamais vide.
        CheckConstraint(
            "unavailable_reason IS NULL OR (NOT available AND "
            "btrim(unavailable_reason) = unavailable_reason AND unavailable_reason <> '')",
            name="unavailable_reason_valid",
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    section_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    unavailable_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
