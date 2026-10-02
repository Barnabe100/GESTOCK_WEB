"""Lot 3-G — lots et péremption : stock et réception (ADR-0045).

- ``catalog_articles`` : ``lot_tracked``, ``expiry_tracked`` (défaut faux) ; péremption ⇒ lot ⇒
  article géré en stock (contraintes). Activation fermée en exploitation jusqu'au Lot 3-H
  (P1-b, règle applicative).
- ``stock_lots`` : référentiel des lots du tenant, unique par (article, numéro sans casse),
  jamais supprimé ni modifié (rôle applicatif : ``SELECT, INSERT``).
- ``stock_lot_levels`` : solde d'un lot par site (``quantity >= 0``), ventilation du stock
  (site, article) ; ``UPDATE (quantity, updated_at)`` seulement.
- ``stock_settings`` : seuil « bientôt périmé » du tenant (défaut applicatif : 30 jours).
- ``stock_entry_lines`` : lot saisi (numéro, péremption, fabrication) et lot résolu ; unicité des
  lignes par présentation ET par lot.
- ``stock_movements`` : ``lot_id`` (journal toujours append-only).
- FK composites ``(tenant_id, article_id, lot_id)`` → ``stock_lots (tenant_id, article_id,
  id)`` : un lot d'un autre article (ou d'un autre tenant) est inaffectable en base.

RLS ``ENABLE`` + ``FORCE`` sur les nouvelles tables, droits minimaux. Retour arrière : refusé si
des lots existent (les données de lots seraient perdues et des réceptions multi-lots ne
respecteraient plus l'ancienne unicité des lignes).

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("stock_lots", "stock_lot_levels", "stock_settings")
QUANTITY = sa.Numeric(precision=18, scale=3)
LOT_KEY = sa.literal_column("COALESCE(lower(lot_number), '')")


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _timestamps() -> list[sa.Column[object]]:
    return [
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
    ]


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["tenants.id"],
        name=op.f(f"fk_{table}_tenant_id_tenants"),
        ondelete="RESTRICT",
    )


def _lot_fk(table: str) -> None:
    op.create_foreign_key(
        op.f(f"fk_{table}_tenant_id_article_id_lot_id_stock_lots"),
        table,
        "stock_lots",
        ["tenant_id", "article_id", "lot_id"],
        ["tenant_id", "article_id", "id"],
        ondelete="RESTRICT",
    )


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
    # Lots figés (D11) : ni modification ni suppression.
    op.execute(f"GRANT SELECT, INSERT ON stock_lots TO {role}")
    op.execute(f"GRANT SELECT, INSERT ON stock_lot_levels TO {role}")
    op.execute(f"GRANT UPDATE (quantity, updated_at) ON stock_lot_levels TO {role}")
    op.execute(f"GRANT SELECT, INSERT ON stock_settings TO {role}")
    op.execute(f"GRANT UPDATE (expiry_warning_days, updated_at) ON stock_settings TO {role}")


def _revoke() -> None:
    role = _app_role()
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")


def upgrade() -> None:
    # --- Articles : réglages de suivi ------------------------------------------------------------
    for column in ("lot_tracked", "expiry_tracked"):
        op.add_column(
            "catalog_articles",
            sa.Column(column, sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    op.create_check_constraint(
        "expiry_requires_lots", "catalog_articles", "NOT expiry_tracked OR lot_tracked"
    )
    op.create_check_constraint(
        "lots_require_stock", "catalog_articles", "NOT lot_tracked OR stock_managed"
    )

    # --- Référentiel des lots --------------------------------------------------------------------
    op.create_table(
        "stock_lots",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(length=50), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("manufacturing_date", sa.Date(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "btrim(number) = number AND number <> ''", name=op.f("ck_stock_lots_number_trimmed")
        ),
        sa.CheckConstraint(
            "manufacturing_date IS NULL OR expiry_date IS NULL "
            "OR manufacturing_date <= expiry_date",
            name=op.f("ck_stock_lots_dates_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_stock_lots_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_stock_lots_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        _tenant_fk("stock_lots"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_lots")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_stock_lots_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "article_id", "id", name=op.f("uq_stock_lots_tenant_id_article_id_id")
        ),
    )
    op.create_index(op.f("ix_stock_lots_tenant_id"), "stock_lots", ["tenant_id"], unique=False)
    op.create_index(
        "uq_stock_lots_article_number",
        "stock_lots",
        ["tenant_id", "article_id", sa.literal_column("lower(number)")],
        unique=True,
    )

    # --- Soldes par lot et par site --------------------------------------------------------------
    op.create_table(
        "stock_lot_levels",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", QUANTITY, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("quantity >= 0", name=op.f("ck_stock_lot_levels_quantity_non_negative")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_stock_lot_levels_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "lot_id"],
            ["stock_lots.tenant_id", "stock_lots.article_id", "stock_lots.id"],
            name=op.f("fk_stock_lot_levels_tenant_id_article_id_lot_id_stock_lots"),
            ondelete="RESTRICT",
        ),
        _tenant_fk("stock_lot_levels"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_lot_levels")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "lot_id",
            name=op.f("uq_stock_lot_levels_tenant_id_site_id_lot_id"),
        ),
    )
    op.create_index(
        op.f("ix_stock_lot_levels_site_id"), "stock_lot_levels", ["site_id"], unique=False
    )
    op.create_index(
        op.f("ix_stock_lot_levels_tenant_id"), "stock_lot_levels", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_stock_lot_levels_tenant_lot", "stock_lot_levels", ["tenant_id", "lot_id"], unique=False
    )

    # --- Réglages du module Stock ---------------------------------------------------------------
    op.create_table(
        "stock_settings",
        sa.Column("expiry_warning_days", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "expiry_warning_days >= 0 AND expiry_warning_days <= 365",
            name=op.f("ck_stock_settings_expiry_warning_days_range"),
        ),
        _tenant_fk("stock_settings"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_settings")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_stock_settings_tenant_id")),
    )
    op.create_index(
        op.f("ix_stock_settings_tenant_id"), "stock_settings", ["tenant_id"], unique=False
    )

    # --- Lignes de réception : lot saisi, lot résolu, unicité par lot ---------------------------
    op.add_column("stock_entry_lines", sa.Column("lot_number", sa.String(length=50)))
    op.add_column("stock_entry_lines", sa.Column("lot_expiry_date", sa.Date()))
    op.add_column("stock_entry_lines", sa.Column("lot_manufacturing_date", sa.Date()))
    op.add_column("stock_entry_lines", sa.Column("lot_id", sa.Uuid()))
    _lot_fk("stock_entry_lines")
    op.create_check_constraint(
        "lot_number_trimmed",
        "stock_entry_lines",
        "lot_number IS NULL OR (btrim(lot_number) = lot_number AND lot_number <> '')",
    )
    op.create_check_constraint(
        "lot_has_number", "stock_entry_lines", "lot_id IS NULL OR lot_number IS NOT NULL"
    )
    op.create_check_constraint(
        "lot_dates_need_number",
        "stock_entry_lines",
        "lot_number IS NOT NULL OR (lot_expiry_date IS NULL AND lot_manufacturing_date IS NULL)",
    )
    op.create_check_constraint(
        "lot_dates_ordered",
        "stock_entry_lines",
        "lot_manufacturing_date IS NULL OR lot_expiry_date IS NULL "
        "OR lot_manufacturing_date <= lot_expiry_date",
    )
    op.drop_index("uq_stock_entry_lines_article_base", table_name="stock_entry_lines")
    op.drop_index("uq_stock_entry_lines_packaging", table_name="stock_entry_lines")
    op.create_index(
        "uq_stock_entry_lines_article_base_lot",
        "stock_entry_lines",
        ["entry_id", "article_id", LOT_KEY],
        unique=True,
        postgresql_where=sa.text("packaging_id IS NULL"),
    )
    op.create_index(
        "uq_stock_entry_lines_packaging_lot",
        "stock_entry_lines",
        ["entry_id", "packaging_id", LOT_KEY],
        unique=True,
        postgresql_where=sa.text("packaging_id IS NOT NULL"),
    )

    # --- Journal des mouvements ----------------------------------------------------------------
    op.add_column("stock_movements", sa.Column("lot_id", sa.Uuid()))
    _lot_fk("stock_movements")
    op.create_index(
        "ix_stock_movements_tenant_lot", "stock_movements", ["tenant_id", "lot_id"], unique=False
    )

    _enable_rls_and_grants()


def downgrade() -> None:
    # Aucun lot ne doit être perdu silencieusement (propriétaire : RLS levée pour ce contrôle).
    op.execute("ALTER TABLE stock_lots NO FORCE ROW LEVEL SECURITY")
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM stock_lots)")).scalar():
        raise RuntimeError(
            "Retour arrière refusé : des lots existent (Lot 3-G). Les supprimer ferait perdre la "
            "traçabilité des réceptions par lot."
        )
    op.execute("ALTER TABLE stock_lots FORCE ROW LEVEL SECURITY")
    _revoke()
    op.drop_index("ix_stock_movements_tenant_lot", table_name="stock_movements")
    op.drop_constraint(
        op.f("fk_stock_movements_tenant_id_article_id_lot_id_stock_lots"),
        "stock_movements",
        type_="foreignkey",
    )
    op.drop_column("stock_movements", "lot_id")

    op.drop_index("uq_stock_entry_lines_packaging_lot", table_name="stock_entry_lines")
    op.drop_index("uq_stock_entry_lines_article_base_lot", table_name="stock_entry_lines")
    op.create_index(
        "uq_stock_entry_lines_article_base",
        "stock_entry_lines",
        ["entry_id", "article_id"],
        unique=True,
        postgresql_where=sa.text("packaging_id IS NULL"),
    )
    op.create_index(
        "uq_stock_entry_lines_packaging",
        "stock_entry_lines",
        ["entry_id", "packaging_id"],
        unique=True,
        postgresql_where=sa.text("packaging_id IS NOT NULL"),
    )
    for name in (
        "lot_dates_ordered",
        "lot_dates_need_number",
        "lot_has_number",
        "lot_number_trimmed",
    ):
        op.drop_constraint(op.f(f"ck_stock_entry_lines_{name}"), "stock_entry_lines", type_="check")
    op.drop_constraint(
        op.f("fk_stock_entry_lines_tenant_id_article_id_lot_id_stock_lots"),
        "stock_entry_lines",
        type_="foreignkey",
    )
    for column in ("lot_id", "lot_manufacturing_date", "lot_expiry_date", "lot_number"):
        op.drop_column("stock_entry_lines", column)

    op.drop_index(op.f("ix_stock_settings_tenant_id"), table_name="stock_settings")
    op.drop_table("stock_settings")
    op.drop_index("ix_stock_lot_levels_tenant_lot", table_name="stock_lot_levels")
    op.drop_index(op.f("ix_stock_lot_levels_tenant_id"), table_name="stock_lot_levels")
    op.drop_index(op.f("ix_stock_lot_levels_site_id"), table_name="stock_lot_levels")
    op.drop_table("stock_lot_levels")
    op.drop_index("uq_stock_lots_article_number", table_name="stock_lots")
    op.drop_index(op.f("ix_stock_lots_tenant_id"), table_name="stock_lots")
    op.drop_table("stock_lots")

    op.drop_constraint(
        op.f("ck_catalog_articles_lots_require_stock"), "catalog_articles", type_="check"
    )
    op.drop_constraint(
        op.f("ck_catalog_articles_expiry_requires_lots"), "catalog_articles", type_="check"
    )
    op.drop_column("catalog_articles", "expiry_tracked")
    op.drop_column("catalog_articles", "lot_tracked")
