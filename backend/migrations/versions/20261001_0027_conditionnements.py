"""Lot 3-B — Quantités décimales et conditionnements (ADR-0040).

- ``catalog_articles.decimal_quantity_allowed`` (défaut ``false``, articles existants
  compris) : quantités vendues entières seulement, sauf article autorisé (kg, m, L…).
- ``catalog_packagings`` : conditionnements de vente d'un article (nom libre, conversion vers
  l'unité de base strictement positive, prix de vente propre, actif / inactif). Aucun
  conditionnement n'est créé pour les articles existants. RLS ``ENABLE`` + ``FORCE`` ; rôle
  applicatif : lecture, insertion et mise à jour des seules colonnes modifiables — jamais de
  suppression (désactivation), jamais de changement d'article.
- ``sale_lines`` : instantané du conditionnement vendu (identifiant, nom, conversion) et
  ``base_quantity`` (quantité en unité de base, celle du stock ; reprise : la quantité vendue,
  toutes les lignes existantes étant en unité de base). L'unicité « un article par vente »
  devient « un article par présentation » (unité de base OU conditionnement).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PACKAGING_UPDATABLE = "name, conversion, sale_price, is_active, updated_at"


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_table(
        "catalog_packagings",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("conversion", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("sale_price", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "conversion > 0", name=op.f("ck_catalog_packagings_conversion_positive")
        ),
        sa.CheckConstraint(
            "sale_price >= 0", name=op.f("ck_catalog_packagings_sale_price_positive")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_catalog_packagings_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_catalog_packagings_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_packagings")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_catalog_packagings_tenant_id_id")),
    )
    op.create_index(
        op.f("ix_catalog_packagings_article_id"), "catalog_packagings", ["article_id"], unique=False
    )
    op.create_index(
        op.f("ix_catalog_packagings_tenant_id"), "catalog_packagings", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_catalog_packagings_article_name_active",
        "catalog_packagings",
        ["tenant_id", "article_id", sa.literal_column("lower(name)")],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.add_column(
        "catalog_articles",
        sa.Column(
            "decimal_quantity_allowed", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )

    op.add_column("sale_lines", sa.Column("packaging_id", sa.Uuid(), nullable=True))
    op.add_column("sale_lines", sa.Column("packaging_name", sa.String(length=50), nullable=True))
    op.add_column(
        "sale_lines",
        sa.Column("packaging_conversion", sa.Numeric(precision=18, scale=3), nullable=True),
    )
    op.add_column(
        "sale_lines",
        sa.Column("base_quantity", sa.Numeric(precision=18, scale=3), nullable=True),
    )
    # Reprise (propriétaire de la table ; FORCE levé dans cette transaction) : toutes les
    # lignes existantes sont en unité de base.
    op.execute("ALTER TABLE sale_lines NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE sale_lines SET base_quantity = quantity")
    op.execute("ALTER TABLE sale_lines FORCE ROW LEVEL SECURITY")
    op.alter_column(
        "sale_lines",
        "base_quantity",
        existing_type=sa.Numeric(precision=18, scale=3),
        nullable=False,
    )
    op.drop_constraint(op.f("uq_sale_lines_sale_id_article_id"), "sale_lines", type_="unique")
    op.create_index(
        "uq_sale_lines_sale_article_base",
        "sale_lines",
        ["sale_id", "article_id"],
        unique=True,
        postgresql_where=sa.text("packaging_id IS NULL"),
    )
    op.create_index(
        "uq_sale_lines_sale_packaging",
        "sale_lines",
        ["sale_id", "packaging_id"],
        unique=True,
        postgresql_where=sa.text("packaging_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("fk_sale_lines_tenant_id_packaging_id_catalog_packagings"),
        "sale_lines",
        "catalog_packagings",
        ["tenant_id", "packaging_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "packaging_snapshot_complete",
        "sale_lines",
        "(packaging_id IS NULL) = (packaging_name IS NULL) "
        "AND (packaging_id IS NULL) = (packaging_conversion IS NULL)",
    )
    op.create_check_constraint(
        "packaging_conversion_positive",
        "sale_lines",
        "packaging_conversion IS NULL OR packaging_conversion > 0",
    )
    op.create_check_constraint(
        "base_quantity_consistent",
        "sale_lines",
        "base_quantity = quantity * COALESCE(packaging_conversion, 1)",
    )

    role = _app_role()
    op.execute("ALTER TABLE catalog_packagings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE catalog_packagings FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON catalog_packagings TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT ON catalog_packagings TO {role}")
    op.execute(f"GRANT UPDATE ({PACKAGING_UPDATABLE}) ON catalog_packagings TO {role}")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("ALTER TABLE sale_lines NO FORCE ROW LEVEL SECURITY")
    sold = bind.execute(
        sa.text("SELECT count(*) FROM sale_lines WHERE packaging_id IS NOT NULL")
    ).scalar_one()
    op.execute("ALTER TABLE sale_lines FORCE ROW LEVEL SECURITY")
    if sold:
        raise RuntimeError(
            f"Retour arrière impossible : {sold} ligne(s) de vente en conditionnement — "
            "l'ancien schéma les lirait comme des quantités en unité de base."
        )
    role = _app_role()
    op.execute(f"REVOKE ALL ON catalog_packagings FROM {role}")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON catalog_packagings")
    for name in (
        "base_quantity_consistent",
        "packaging_conversion_positive",
        "packaging_snapshot_complete",
    ):
        op.drop_constraint(op.f(f"ck_sale_lines_{name}"), "sale_lines", type_="check")
    op.drop_constraint(
        op.f("fk_sale_lines_tenant_id_packaging_id_catalog_packagings"),
        "sale_lines",
        type_="foreignkey",
    )
    op.drop_index("uq_sale_lines_sale_packaging", table_name="sale_lines")
    op.drop_index("uq_sale_lines_sale_article_base", table_name="sale_lines")
    op.create_unique_constraint(
        op.f("uq_sale_lines_sale_id_article_id"), "sale_lines", ["sale_id", "article_id"]
    )
    op.drop_column("sale_lines", "base_quantity")
    op.drop_column("sale_lines", "packaging_conversion")
    op.drop_column("sale_lines", "packaging_name")
    op.drop_column("sale_lines", "packaging_id")
    op.drop_column("catalog_articles", "decimal_quantity_allowed")
    op.drop_index("uq_catalog_packagings_article_name_active", table_name="catalog_packagings")
    op.drop_index(op.f("ix_catalog_packagings_tenant_id"), table_name="catalog_packagings")
    op.drop_index(op.f("ix_catalog_packagings_article_id"), table_name="catalog_packagings")
    op.drop_table("catalog_packagings")
