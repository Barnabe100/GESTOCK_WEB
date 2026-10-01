"""Lot 3-F — emplacements physiques par site (ADR-0044).

- ``stock_locations`` : emplacement d'un site (rayon, étagère, réserve…), nom unique par site
  insensible à la casse, jamais supprimé (désactivation).
- ``stock_article_locations`` : emplacement COURANT (au plus un) d'un article sur un site ;
  FK composite ``(tenant_id, site_id, location_id)`` → l'emplacement d'un autre site est
  inaffectable en base. Aucune quantité par emplacement : le stock reste par (site, article).

RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : emplacements ``SELECT, INSERT`` +
``UPDATE (name, is_active, updated_at)`` (jamais de suppression) ; affectations ``SELECT,
INSERT, DELETE`` + ``UPDATE (location_id, updated_at)`` (retrait = ligne supprimée, audité).

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("stock_locations", "stock_article_locations")


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
    op.execute(f"GRANT SELECT, INSERT ON stock_locations TO {role}")
    op.execute(f"GRANT UPDATE (name, is_active, updated_at) ON stock_locations TO {role}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON stock_article_locations TO {role}")
    op.execute(f"GRANT UPDATE (location_id, updated_at) ON stock_article_locations TO {role}")


def _revoke() -> None:
    role = _app_role()
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")


def upgrade() -> None:
    op.create_table(
        "stock_locations",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
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
            "btrim(name) = name AND name <> ''", name=op.f("ck_stock_locations_name_trimmed")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_stock_locations_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_stock_locations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_locations")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_stock_locations_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "id", name=op.f("uq_stock_locations_tenant_id_site_id_id")
        ),
    )
    op.create_index(
        op.f("ix_stock_locations_site_id"), "stock_locations", ["site_id"], unique=False
    )
    op.create_index(
        op.f("ix_stock_locations_tenant_id"), "stock_locations", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_stock_locations_site_name",
        "stock_locations",
        ["tenant_id", "site_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "stock_article_locations",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_stock_article_locations_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "location_id"],
            ["stock_locations.tenant_id", "stock_locations.site_id", "stock_locations.id"],
            name=op.f("fk_stock_article_locations_tenant_id_site_id_location_id_stock_locations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_stock_article_locations_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_stock_article_locations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_article_locations")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "article_id",
            name=op.f("uq_stock_article_locations_tenant_id_site_id_article_id"),
        ),
    )
    op.create_index(
        op.f("ix_stock_article_locations_location_id"),
        "stock_article_locations",
        ["location_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_stock_article_locations_tenant_id"),
        "stock_article_locations",
        ["tenant_id"],
        unique=False,
    )
    _enable_rls_and_grants()


def downgrade() -> None:
    _revoke()
    op.drop_index(
        op.f("ix_stock_article_locations_tenant_id"), table_name="stock_article_locations"
    )
    op.drop_index(
        op.f("ix_stock_article_locations_location_id"), table_name="stock_article_locations"
    )
    op.drop_table("stock_article_locations")
    op.drop_index("uq_stock_locations_site_name", table_name="stock_locations")
    op.drop_index(op.f("ix_stock_locations_tenant_id"), table_name="stock_locations")
    op.drop_index(op.f("ix_stock_locations_site_id"), table_name="stock_locations")
    op.drop_table("stock_locations")
