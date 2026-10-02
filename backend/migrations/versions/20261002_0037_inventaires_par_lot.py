"""Lot 3-H — inventaires par lot (ADR-0045 : O-5, T-4 ; finalisation du module lots).

- ``inventory_lines.lot_tracked`` : mode de suivi par lot de l'article FIGÉ au démarrage du
  comptage (relu sous verrou à la validation : ``409 inventory_lot_mode_changed``).
- ``inventory_lines`` : unicité ``(tenant_id, id, article_id)`` (cible de la FK composite).
- ``inventory_line_lots`` : lots d'une ligne d'article suivi — attendus (solde non nul au
  démarrage) ou découverts (numéro, péremption, fabrication saisis ; lot créé à la validation
  seulement) ; théorique initial, théorique relu à la validation, physique, écart, comptage en
  conditionnement + vrac (Lot 3-C). FK composites : ligne du même tenant et du même article, lot
  du même article, conditionnement du tenant. Un lot une seule fois par ligne ; un lot NOUVEAU
  une seule fois par numéro (sans casse, index partiel).

Migration additive. RLS ``ENABLE`` + ``FORCE`` sur la nouvelle table, droits minimaux
(données de comptage : ``SELECT, INSERT, UPDATE, DELETE`` comme les lignes d'inventaire), aucun
droit pour le rôle de la console. Retour arrière : refusé si des lots d'inventaire existent ou
si une ligne a été figée « suivie par lot » (données perdues).

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "inventory_line_lots"


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.add_column(
        "inventory_lines",
        sa.Column("lot_tracked", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_unique_constraint(
        op.f("uq_inventory_lines_tenant_id_id_article_id"),
        "inventory_lines",
        ["tenant_id", "id", "article_id"],
    )
    op.create_table(
        "inventory_line_lots",
        sa.Column("inventory_line_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("discovered", sa.Boolean(), nullable=False),
        sa.Column("lot_number", sa.String(length=50), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("manufacturing_date", sa.Date(), nullable=True),
        sa.Column("stock_theoretical_initial", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column(
            "stock_theoretical_at_validation", sa.Numeric(precision=18, scale=3), nullable=True
        ),
        sa.Column("quantity_physical", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("quantity_variance", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counted_by", sa.Uuid(), nullable=True),
        sa.Column("count_packaging_id", sa.Uuid(), nullable=True),
        sa.Column("count_packaging_name", sa.String(length=50), nullable=True),
        sa.Column("count_packaging_conversion", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("count_packaging_quantity", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("count_unit_quantity", sa.Numeric(precision=18, scale=3), nullable=True),
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
            "(count_packaging_id IS NULL) = (count_packaging_name IS NULL) AND (count_packaging_id"
            " IS NULL) = (count_packaging_conversion IS NULL) AND (count_packaging_id IS NULL) = "
            "(count_packaging_quantity IS NULL) AND (count_packaging_id IS NULL) = "
            "(count_unit_quantity IS NULL)",
            name=op.f("ck_inventory_line_lots_count_packaging_complete"),
        ),
        sa.CheckConstraint(
            "count_packaging_id IS NULL OR (count_packaging_conversion > 0 AND "
            "count_packaging_quantity >= 0 AND count_unit_quantity >= 0 AND quantity_physical = "
            "count_packaging_quantity * count_packaging_conversion + count_unit_quantity)",
            name=op.f("ck_inventory_line_lots_count_packaging_consistent"),
        ),
        sa.CheckConstraint(
            "discovered = (lot_number IS NOT NULL)",
            name=op.f("ck_inventory_line_lots_discovered_has_number"),
        ),
        sa.CheckConstraint(
            "discovered OR (expiry_date IS NULL AND manufacturing_date IS NULL)",
            name=op.f("ck_inventory_line_lots_dates_only_discovered"),
        ),
        sa.CheckConstraint(
            "lot_id IS NOT NULL OR discovered", name=op.f("ck_inventory_line_lots_expected_has_lot")
        ),
        sa.CheckConstraint(
            "manufacturing_date IS NULL OR expiry_date IS NULL OR manufacturing_date <= "
            "expiry_date",
            name=op.f("ck_inventory_line_lots_dates_ordered"),
        ),
        sa.CheckConstraint(
            "quantity_physical IS NULL OR quantity_physical >= 0",
            name=op.f("ck_inventory_line_lots_physical_non_negative"),
        ),
        sa.CheckConstraint(
            "quantity_variance IS NULL OR (stock_theoretical_at_validation IS NOT NULL AND "
            "quantity_physical IS NOT NULL AND quantity_variance = quantity_physical - "
            "stock_theoretical_at_validation)",
            name=op.f("ck_inventory_line_lots_variance_consistent"),
        ),
        sa.CheckConstraint(
            "stock_theoretical_at_validation IS NULL OR stock_theoretical_at_validation >= 0",
            name=op.f("ck_inventory_line_lots_at_validation_non_negative"),
        ),
        sa.CheckConstraint(
            "stock_theoretical_initial >= 0",
            name=op.f("ck_inventory_line_lots_initial_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["counted_by"], ["users.id"], name=op.f("fk_inventory_line_lots_counted_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "lot_id"],
            ["stock_lots.tenant_id", "stock_lots.article_id", "stock_lots.id"],
            name=op.f("fk_inventory_line_lots_tenant_id_article_id_lot_id_stock_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "count_packaging_id"],
            ["catalog_packagings.tenant_id", "catalog_packagings.id"],
            name=op.f("fk_inventory_line_lots_tenant_id_count_packaging_id_catalog_packagings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "inventory_line_id", "article_id"],
            ["inventory_lines.tenant_id", "inventory_lines.id", "inventory_lines.article_id"],
            name=op.f(
                "fk_inventory_line_lots_tenant_id_inventory_line_id_article_id_inventory_lines"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_inventory_line_lots_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inventory_line_lots")),
        sa.UniqueConstraint(
            "inventory_line_id",
            "lot_id",
            name=op.f("uq_inventory_line_lots_inventory_line_id_lot_id"),
        ),
    )
    op.create_index(
        op.f("ix_inventory_line_lots_inventory_line_id"),
        "inventory_line_lots",
        ["inventory_line_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_line_lots_tenant_id"), "inventory_line_lots", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_inventory_line_lots_new_number",
        "inventory_line_lots",
        ["inventory_line_id", sa.literal_column("lower(lot_number)")],
        unique=True,
        postgresql_where=sa.text("lot_id IS NULL"),
    )

    # --- RLS et droits minimaux ----------------------------------------------------------------
    role = _app_role()
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    # Données de comptage : saisies, remplacées et retirées tant que l'inventaire est ouvert.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {role}")


def _refuse_if(sql: str, message: str) -> None:
    if op.get_bind().execute(sa.text(sql)).scalar():
        raise RuntimeError(f"Retour arrière refusé : {message}")


def downgrade() -> None:
    # Propriétaire : RLS levée le temps des contrôles (aucune donnée perdue silencieusement).
    for table in (TABLE, "inventory_lines"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    _refuse_if(f"SELECT EXISTS (SELECT 1 FROM {TABLE})", "des inventaires portent des lots.")
    _refuse_if(
        "SELECT EXISTS (SELECT 1 FROM inventory_lines WHERE lot_tracked)",
        "des lignes d'inventaire sont suivies par lot.",
    )
    for table in (TABLE, "inventory_lines"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    role = _app_role()
    op.execute(f"REVOKE ALL ON {TABLE} FROM {role}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.drop_index(
        "uq_inventory_line_lots_new_number",
        table_name=TABLE,
        postgresql_where=sa.text("lot_id IS NULL"),
    )
    op.drop_index(op.f("ix_inventory_line_lots_tenant_id"), table_name=TABLE)
    op.drop_index(op.f("ix_inventory_line_lots_inventory_line_id"), table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_constraint(
        op.f("uq_inventory_lines_tenant_id_id_article_id"), "inventory_lines", type_="unique"
    )
    op.drop_column("inventory_lines", "lot_tracked")
