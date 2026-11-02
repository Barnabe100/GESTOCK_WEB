"""Recettes (palier R8, ADR-0053).

- ``Recipe`` : fiche d'UNE présentation vendue (article NON géré en stock, en unité de base ou
  dans un de ses conditionnements) ; version courante ; jamais supprimée (désactivation).
- ``RecipeVersion`` / ``RecipeLine`` : composition IMMUABLE (rendement, instructions,
  ingrédients en unité de base) ; toute modification crée une nouvelle version.
- ``RecipeConsumption`` : couple (ligne de vente, ingrédient) déduit à la validation d'une vente
  (O10) ; son identifiant est la ``source_line_id`` du mouvement de stock (source = la vente).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.platform.models_base import IdMixin, TenantScopedMixin, TimestampMixin

QUANTITY = Numeric(18, 3)

# Contraintes nommées : traduites en refus métier (409).
RECIPE_PRESENTATION_CONSTRAINT = "uq_restaurant_recipes_presentation"
VERSION_NUMBER_CONSTRAINT = "uq_restaurant_recipe_versions_number"
LINE_INGREDIENT_CONSTRAINT = "uq_restaurant_recipe_lines_ingredient"
CONSUMPTION_CONSTRAINT = "uq_restaurant_recipe_consumptions_line_ingredient"

MAX_LINES = 50


def _article_fk(column: str = "article_id") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", column],
        ["catalog_articles.tenant_id", "catalog_articles.id"],
        ondelete="RESTRICT",
    )


class Recipe(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Fiche technique d'une présentation (``packaging_id`` nul = unité de base) : au plus une
    par présentation (``NULLS NOT DISTINCT``)."""

    __tablename__ = "restaurant_recipes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint(
            "tenant_id",
            "article_id",
            "packaging_id",
            name=RECIPE_PRESENTATION_CONSTRAINT,
            postgresql_nulls_not_distinct=True,
        ),
        _article_fk(),
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
        # Version courante DE CETTE recette (posée après la création de la première version).
        ForeignKeyConstraint(
            ["tenant_id", "id", "current_version_id"],
            [
                "restaurant_recipe_versions.tenant_id",
                "restaurant_recipe_versions.recipe_id",
                "restaurant_recipe_versions.id",
            ],
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_restaurant_recipes_current_version",
        ),
        CheckConstraint(
            "notes IS NULL OR (btrim(notes) = notes AND notes <> '')", name="notes_trimmed"
        ),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    packaging_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class RecipeVersion(IdMixin, TenantScopedMixin, Base):
    """Composition figée : rendement (portions de la présentation produites) et instructions."""

    __tablename__ = "restaurant_recipe_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "recipe_id", "id"),
        UniqueConstraint("tenant_id", "recipe_id", "number", name=VERSION_NUMBER_CONSTRAINT),
        ForeignKeyConstraint(
            ["tenant_id", "recipe_id"],
            ["restaurant_recipes.tenant_id", "restaurant_recipes.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint("yield_quantity > 0", name="yield_positive"),
        CheckConstraint(
            "instructions IS NULL OR (btrim(instructions) = instructions AND instructions <> '')",
            name="instructions_trimmed",
        ),
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    yield_quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RecipeLine(IdMixin, TenantScopedMixin, Base):
    """Ingrédient d'une version : article géré en stock, quantité en unité de base."""

    __tablename__ = "restaurant_recipe_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "version_id", "article_id", name=LINE_INGREDIENT_CONSTRAINT),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["restaurant_recipe_versions.tenant_id", "restaurant_recipe_versions.id"],
            ondelete="RESTRICT",
        ),
        _article_fk(),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("line_no > 0", name="line_no_positive"),
        CheckConstraint("note IS NULL OR (btrim(note) = note AND note <> '')", name="note_trimmed"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


class RecipeConsumption(IdMixin, TenantScopedMixin, Base):
    """Ingrédient déduit pour UNE ligne de vente (O10). Identifiant = ``source_line_id`` des
    mouvements ``SALE`` / ``CANCELLATION`` de cet ingrédient (source = la vente)."""

    __tablename__ = "restaurant_recipe_consumptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "sale_line_id", "article_id", name=CONSUMPTION_CONSTRAINT),
        # Vente DU MÊME site ; ligne DE CETTE vente.
        ForeignKeyConstraint(
            ["tenant_id", "sale_id", "site_id"],
            ["sales.tenant_id", "sales.id", "sales.site_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sale_id", "sale_line_id"],
            ["sale_lines.tenant_id", "sale_lines.sale_id", "sale_lines.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipe_version_id"],
            ["restaurant_recipe_versions.tenant_id", "restaurant_recipe_versions.id"],
            ondelete="RESTRICT",
        ),
        _article_fk(),
        CheckConstraint("quantity > 0", name="quantity_positive"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sale_line_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    recipe_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    article_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
