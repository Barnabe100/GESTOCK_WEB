"""Palier R8 — recettes (ADR-0053).

- ``restaurant_recipes`` : fiche d'UNE présentation (article non géré en stock, conditionnement
  facultatif DE L'article), unique par présentation (``NULLS NOT DISTINCT``), version courante
  (FK composite vers une version DE CETTE recette), jamais supprimée (désactivation).
- ``restaurant_recipe_versions`` / ``restaurant_recipe_lines`` : composition IMMUABLE
  (rendement, instructions, ingrédients en unité de base, un ingrédient une fois par version).
- ``restaurant_recipe_consumptions`` : couple (ligne de vente, ingrédient) déduit à la
  validation (O10) ; identifiant = ligne source des mouvements de stock ; ligne DE SA vente et
  vente DU MÊME site (FK composites) ; unique par (ligne, ingrédient).
- ``sale_lines`` : contrainte unique ``(tenant_id, sale_id, id)``, cible de la FK composite des
  consommations (additive, aucune donnée modifiée).

RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : ``SELECT, INSERT`` (versions, lignes et
consommations immuables) et ``UPDATE`` des seules colonnes modifiables de la recette ; aucune
suppression ; aucun droit pour la console. Descente refusée dès qu'une recette existe.

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = (
    "restaurant_recipes",
    "restaurant_recipe_versions",
    "restaurant_recipe_lines",
    "restaurant_recipe_consumptions",
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _enable_rls_and_grants() -> None:
    role = _app_role()
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {role} "
            "USING (tenant_id = app_current_tenant_id()) "
            "WITH CHECK (tenant_id = app_current_tenant_id())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {role}")
    op.execute(
        "GRANT UPDATE (current_version_id, is_active, notes, updated_at) "
        f"ON restaurant_recipes TO {role}"
    )


def _revoke() -> None:
    role = _app_role()
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")


def upgrade() -> None:
    op.create_table(
        "restaurant_recipes",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("packaging_id", sa.Uuid(), nullable=True),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "notes IS NULL OR (btrim(notes) = notes AND notes <> '')",
            name=op.f("ck_restaurant_recipes_notes_trimmed"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_restaurant_recipes_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "packaging_id"],
            [
                "catalog_packagings.tenant_id",
                "catalog_packagings.article_id",
                "catalog_packagings.id",
            ],
            name=op.f("fk_restaurant_recipes_tenant_id_article_id_packaging_id_catalog_packagings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_restaurant_recipes_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_recipes_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_recipes")),
        sa.UniqueConstraint(
            "tenant_id",
            "article_id",
            "packaging_id",
            name="uq_restaurant_recipes_presentation",
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_recipes_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_restaurant_recipes_article_id"), "restaurant_recipes", ["article_id"], unique=False
    )
    op.create_index(
        op.f("ix_restaurant_recipes_tenant_id"), "restaurant_recipes", ["tenant_id"], unique=False
    )
    op.create_table(
        "restaurant_recipe_versions",
        sa.Column("recipe_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("yield_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "instructions IS NULL OR (btrim(instructions) = instructions AND instructions <> '')",
            name=op.f("ck_restaurant_recipe_versions_instructions_trimmed"),
        ),
        sa.CheckConstraint(
            "number > 0", name=op.f("ck_restaurant_recipe_versions_number_positive")
        ),
        sa.CheckConstraint(
            "yield_quantity > 0", name=op.f("ck_restaurant_recipe_versions_yield_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_restaurant_recipe_versions_created_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipe_id"],
            ["restaurant_recipes.tenant_id", "restaurant_recipes.id"],
            name=op.f("fk_restaurant_recipe_versions_tenant_id_recipe_id_restaurant_recipes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_recipe_versions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_recipe_versions")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_recipe_versions_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "recipe_id",
            "id",
            name=op.f("uq_restaurant_recipe_versions_tenant_id_recipe_id_id"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "recipe_id", "number", name="uq_restaurant_recipe_versions_number"
        ),
    )
    op.create_index(
        op.f("ix_restaurant_recipe_versions_recipe_id"),
        "restaurant_recipe_versions",
        ["recipe_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_recipe_versions_tenant_id"),
        "restaurant_recipe_versions",
        ["tenant_id"],
        unique=False,
    )
    # Version courante DE CETTE recette (FK circulaire : posée après la table des versions).
    op.create_foreign_key(
        "fk_restaurant_recipes_current_version",
        "restaurant_recipes",
        "restaurant_recipe_versions",
        ["tenant_id", "id", "current_version_id"],
        ["tenant_id", "recipe_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        op.f("uq_sale_lines_tenant_id_sale_id_id"), "sale_lines", ["tenant_id", "sale_id", "id"]
    )
    op.create_table(
        "restaurant_recipe_consumptions",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("sale_id", sa.Uuid(), nullable=False),
        sa.Column("sale_line_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_version_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_restaurant_recipe_consumptions_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_restaurant_recipe_consumptions_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipe_version_id"],
            ["restaurant_recipe_versions.tenant_id", "restaurant_recipe_versions.id"],
            name=op.f(
                "fk_restaurant_recipe_consumptions_tenant_id_recipe_version_id_restaurant_recipe_versions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sale_id", "sale_line_id"],
            ["sale_lines.tenant_id", "sale_lines.sale_id", "sale_lines.id"],
            name=op.f(
                "fk_restaurant_recipe_consumptions_tenant_id_sale_id_sale_line_id_sale_lines"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sale_id", "site_id"],
            ["sales.tenant_id", "sales.id", "sales.site_id"],
            name=op.f("fk_restaurant_recipe_consumptions_tenant_id_sale_id_site_id_sales"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_recipe_consumptions_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_recipe_consumptions")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_recipe_consumptions_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "sale_line_id",
            "article_id",
            name="uq_restaurant_recipe_consumptions_line_ingredient",
        ),
    )
    op.create_index(
        op.f("ix_restaurant_recipe_consumptions_article_id"),
        "restaurant_recipe_consumptions",
        ["article_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_recipe_consumptions_recipe_version_id"),
        "restaurant_recipe_consumptions",
        ["recipe_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_recipe_consumptions_sale_id"),
        "restaurant_recipe_consumptions",
        ["sale_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_recipe_consumptions_site_id"),
        "restaurant_recipe_consumptions",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_recipe_consumptions_tenant_id"),
        "restaurant_recipe_consumptions",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "restaurant_recipe_lines",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "note IS NULL OR (btrim(note) = note AND note <> '')",
            name=op.f("ck_restaurant_recipe_lines_note_trimmed"),
        ),
        sa.CheckConstraint("line_no > 0", name=op.f("ck_restaurant_recipe_lines_line_no_positive")),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_restaurant_recipe_lines_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_restaurant_recipe_lines_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["restaurant_recipe_versions.tenant_id", "restaurant_recipe_versions.id"],
            name=op.f("fk_restaurant_recipe_lines_tenant_id_version_id_restaurant_recipe_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_recipe_lines_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_recipe_lines")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_recipe_lines_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "version_id", "article_id", name="uq_restaurant_recipe_lines_ingredient"
        ),
    )
    op.create_index(
        op.f("ix_restaurant_recipe_lines_article_id"),
        "restaurant_recipe_lines",
        ["article_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_recipe_lines_tenant_id"),
        "restaurant_recipe_lines",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_recipe_lines_version_id"),
        "restaurant_recipe_lines",
        ["version_id"],
        unique=False,
    )
    _enable_rls_and_grants()


def downgrade() -> None:
    # Jamais de perte silencieuse : une recette (et donc ses versions et consommations) bloque
    # le retour arrière. Comptage sans RLS (rôle propriétaire de la migration).
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM restaurant_recipes")).scalar_one()
    if count:
        raise RuntimeError(f"Retour arrière refusé : {count} recette(s) seraient perdues.")
    _revoke()
    op.drop_constraint(
        "fk_restaurant_recipes_current_version", "restaurant_recipes", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_lines_version_id"), table_name="restaurant_recipe_lines"
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_lines_tenant_id"), table_name="restaurant_recipe_lines"
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_lines_article_id"), table_name="restaurant_recipe_lines"
    )
    op.drop_table("restaurant_recipe_lines")
    op.drop_index(
        op.f("ix_restaurant_recipe_consumptions_tenant_id"),
        table_name="restaurant_recipe_consumptions",
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_consumptions_site_id"),
        table_name="restaurant_recipe_consumptions",
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_consumptions_sale_id"),
        table_name="restaurant_recipe_consumptions",
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_consumptions_recipe_version_id"),
        table_name="restaurant_recipe_consumptions",
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_consumptions_article_id"),
        table_name="restaurant_recipe_consumptions",
    )
    op.drop_table("restaurant_recipe_consumptions")
    op.drop_index(
        op.f("ix_restaurant_recipe_versions_tenant_id"), table_name="restaurant_recipe_versions"
    )
    op.drop_index(
        op.f("ix_restaurant_recipe_versions_recipe_id"), table_name="restaurant_recipe_versions"
    )
    op.drop_table("restaurant_recipe_versions")
    op.drop_index(op.f("ix_restaurant_recipes_tenant_id"), table_name="restaurant_recipes")
    op.drop_index(op.f("ix_restaurant_recipes_article_id"), table_name="restaurant_recipes")
    op.drop_table("restaurant_recipes")
    op.drop_constraint(op.f("uq_sale_lines_tenant_id_sale_id_id"), "sale_lines", type_="unique")
