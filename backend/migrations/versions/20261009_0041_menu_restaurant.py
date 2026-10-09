"""Palier R1 — menu des sites de restauration (ADR-0049, ``RESTAURANT.md`` §4).

- ``restaurant_menu_sections`` : section du menu d'UN site, nom unique par site insensible à la
  casse, jamais supprimée (désactivation).
- ``restaurant_menu_items`` : présentation d'un article du catalogue (unité de base si
  ``packaging_id`` est nul, sinon conditionnement DE L'ARTICLE : FK composite) au menu d'un
  site ; section du MÊME site (FK composite) ; une présentation au plus une fois par site, y
  compris l'unité de base (``UNIQUE NULLS NOT DISTINCT``, PostgreSQL ≥ 15) ; « épuisé » manuel.

RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : ``SELECT, INSERT`` + ``UPDATE`` des seules
colonnes modifiables (jamais de suppression) ; aucun droit pour le rôle de la console.

Données (D10) : le module devient disponible ; ses activations héritées de la période
« Bientôt disponible » (défaut du profil, inertes) sont remises à ``false`` sur les sites
EXISTANTS — le menu n'y est utilisable qu'après une activation explicite. La descente ne les
restaure pas (elles étaient inertes) et refuse de perdre un menu saisi.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("restaurant_menu_sections", "restaurant_menu_items")
MODULE = "restaurant.menu"


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
        "GRANT UPDATE (name, sort_order, is_active, updated_at) "
        f"ON restaurant_menu_sections TO {role}"
    )
    op.execute(
        "GRANT UPDATE (section_id, display_name, description, sort_order, is_active, available, "
        f"unavailable_reason, updated_at) ON restaurant_menu_items TO {role}"
    )


def _revoke() -> None:
    role = _app_role()
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")


def upgrade() -> None:
    op.create_table(
        "restaurant_menu_sections",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
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
            "btrim(name) = name AND name <> ''",
            name=op.f("ck_restaurant_menu_sections_name_trimmed"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_menu_sections_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_menu_sections_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_menu_sections")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_menu_sections_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "id",
            name=op.f("uq_restaurant_menu_sections_tenant_id_site_id_id"),
        ),
    )
    op.create_index(
        op.f("ix_restaurant_menu_sections_site_id"),
        "restaurant_menu_sections",
        ["site_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_menu_sections_tenant_id"),
        "restaurant_menu_sections",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_restaurant_menu_sections_site_name",
        "restaurant_menu_sections",
        ["tenant_id", "site_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "restaurant_menu_items",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("packaging_id", sa.Uuid(), nullable=True),
        sa.Column("display_name", sa.String(length=150), nullable=True),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("unavailable_reason", sa.String(length=200), nullable=True),
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
            "display_name IS NULL OR (btrim(display_name) = display_name AND display_name <> '')",
            name=op.f("ck_restaurant_menu_items_display_name_trimmed"),
        ),
        sa.CheckConstraint(
            "unavailable_reason IS NULL OR (NOT available AND "
            "btrim(unavailable_reason) = unavailable_reason AND unavailable_reason <> '')",
            name=op.f("ck_restaurant_menu_items_unavailable_reason_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "packaging_id"],
            [
                "catalog_packagings.tenant_id",
                "catalog_packagings.article_id",
                "catalog_packagings.id",
            ],
            name=op.f(
                "fk_restaurant_menu_items_tenant_id_article_id_packaging_id_catalog_packagings"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_restaurant_menu_items_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "section_id"],
            [
                "restaurant_menu_sections.tenant_id",
                "restaurant_menu_sections.site_id",
                "restaurant_menu_sections.id",
            ],
            name=op.f(
                "fk_restaurant_menu_items_tenant_id_site_id_section_id_restaurant_menu_sections"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_menu_items_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_menu_items_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_menu_items")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_menu_items_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "article_id",
            "packaging_id",
            name="uq_restaurant_menu_items_site_presentation",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_restaurant_menu_items_article_id"),
        "restaurant_menu_items",
        ["article_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_menu_items_section_id"),
        "restaurant_menu_items",
        ["section_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_menu_items_site_id"), "restaurant_menu_items", ["site_id"], unique=False
    )
    op.create_index(
        op.f("ix_restaurant_menu_items_tenant_id"),
        "restaurant_menu_items",
        ["tenant_id"],
        unique=False,
    )

    _enable_rls_and_grants()
    # D10 : sites existants — menu désactivé jusqu'à une activation explicite (rôle
    # propriétaire ; la politique de ``site_modules`` ne vise que le rôle applicatif).
    op.execute(
        sa.text("UPDATE site_modules SET enabled = false WHERE module_code = :code").bindparams(
            code=MODULE
        )
    )


def downgrade() -> None:
    # Jamais de perte silencieuse : un menu saisi par les utilisateurs bloque le retour arrière
    # (comme l'assortiment, 0038). Comptage hors RLS le temps de la vérification.
    bind = op.get_bind()
    count = 0
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        count += bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if count:
        raise RuntimeError(
            f"Retour arrière refusé : {count} section(s) ou élément(s) de menu saisis par les "
            "utilisateurs seraient perdus."
        )
    _revoke()
    op.drop_index(op.f("ix_restaurant_menu_items_tenant_id"), table_name="restaurant_menu_items")
    op.drop_index(op.f("ix_restaurant_menu_items_site_id"), table_name="restaurant_menu_items")
    op.drop_index(op.f("ix_restaurant_menu_items_section_id"), table_name="restaurant_menu_items")
    op.drop_index(op.f("ix_restaurant_menu_items_article_id"), table_name="restaurant_menu_items")
    op.drop_table("restaurant_menu_items")
    op.drop_index("uq_restaurant_menu_sections_site_name", table_name="restaurant_menu_sections")
    op.drop_index(
        op.f("ix_restaurant_menu_sections_tenant_id"), table_name="restaurant_menu_sections"
    )
    op.drop_index(
        op.f("ix_restaurant_menu_sections_site_id"), table_name="restaurant_menu_sections"
    )
    op.drop_table("restaurant_menu_sections")
