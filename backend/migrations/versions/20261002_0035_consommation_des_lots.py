"""Lot 3-H-A — consommation des lots : ventes, POS, sorties et annulations (ADR-0045).

- ``stock_movements`` : l'unicité anti double application inclut le lot (M1 : une ligne répartie
  produit un mouvement par lot) — ``(tenant, ligne source, type, site, lot)`` en ``NULLS NOT
  DISTINCT`` : sans lot (article non suivi), toujours UN mouvement par ligne, type et site.
- ``stock_exit_line_lots`` : choix manuels des lots d'une ligne de sortie BROUILLON (quantité en
  unité de base), supprimés et recréés avec la ligne, revalidés à la validation ; jamais source
  historique (le journal des mouvements fait foi). FK composites : ligne du même tenant et du
  même article, lot du même article.
- ``stock_exit_lines`` : unicité ``(tenant_id, id, article_id)`` (cible de la FK composite).
- ``sales`` : dérogation à la vente d'un lot périmé (auteur, date, motif — O-1).

RLS ``ENABLE`` + ``FORCE`` sur la nouvelle table, droits minimaux (lignes de brouillon :
``SELECT, INSERT, UPDATE, DELETE`` comme les lignes de sortie). Retour arrière : refusé si une
ligne a été répartie sur plusieurs lots (l'ancienne unicité serait violée), si des choix de lots
existent ou si une vente porte une dérogation (données perdues).

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "stock_exit_line_lots"
MOVEMENT_KEY = ["tenant_id", "source_line_id", "movement_type", "site_id"]


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    # --- Journal des mouvements : un mouvement par lot ---------------------------------------
    op.drop_constraint(op.f("uq_stock_movements_line_type_site"), "stock_movements", type_="unique")
    op.create_unique_constraint(
        "uq_stock_movements_line_type_site_lot",
        "stock_movements",
        [*MOVEMENT_KEY, "lot_id"],
        postgresql_nulls_not_distinct=True,
    )

    # --- Choix des lots d'une ligne de sortie (brouillon) -------------------------------------
    op.create_unique_constraint(
        op.f("uq_stock_exit_lines_tenant_id_id_article_id"),
        "stock_exit_lines",
        ["tenant_id", "id", "article_id"],
    )
    op.create_table(
        TABLE,
        sa.Column("exit_line_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_stock_exit_line_lots_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "lot_id"],
            ["stock_lots.tenant_id", "stock_lots.article_id", "stock_lots.id"],
            name=op.f("fk_stock_exit_line_lots_tenant_id_article_id_lot_id_stock_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "exit_line_id", "article_id"],
            ["stock_exit_lines.tenant_id", "stock_exit_lines.id", "stock_exit_lines.article_id"],
            name=op.f("fk_stock_exit_line_lots_tenant_id_exit_line_id_article_id_stock_exit_lines"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_stock_exit_line_lots_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_exit_line_lots")),
        sa.UniqueConstraint(
            "exit_line_id", "lot_id", name=op.f("uq_stock_exit_line_lots_exit_line_id_lot_id")
        ),
    )
    op.create_index(op.f("ix_stock_exit_line_lots_exit_line_id"), TABLE, ["exit_line_id"])
    op.create_index(op.f("ix_stock_exit_line_lots_tenant_id"), TABLE, ["tenant_id"])

    # --- Ventes : dérogation à la vente d'un lot périmé ---------------------------------------
    op.add_column("sales", sa.Column("expired_lot_override_by", sa.Uuid(), nullable=True))
    op.add_column(
        "sales", sa.Column("expired_lot_override_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sales", sa.Column("expired_lot_override_reason", sa.String(length=500), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_sales_expired_lot_override_by_users"),
        "sales",
        "users",
        ["expired_lot_override_by"],
        ["id"],
    )
    op.create_check_constraint(
        "expired_lot_override_complete",
        "sales",
        "(expired_lot_override_by IS NULL) = (expired_lot_override_at IS NULL) "
        "AND (expired_lot_override_by IS NULL) = (expired_lot_override_reason IS NULL)",
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
    # Données de brouillon : remplacées avec les lignes tant que la sortie n'est pas validée.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {role}")


def _refuse_if(sql: str, message: str) -> None:
    if op.get_bind().execute(sa.text(sql)).scalar():
        raise RuntimeError(f"Retour arrière refusé : {message}")


def downgrade() -> None:
    # Propriétaire : RLS levée le temps des contrôles (aucune donnée perdue silencieusement).
    for table in ("stock_movements", TABLE, "sales"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    _refuse_if(
        "SELECT EXISTS (SELECT 1 FROM stock_movements GROUP BY "
        f"{', '.join(MOVEMENT_KEY)} HAVING count(*) > 1)",
        "des lignes ont été réparties sur plusieurs lots (Lot 3-H, un mouvement par lot).",
    )
    _refuse_if(f"SELECT EXISTS (SELECT 1 FROM {TABLE})", "des sorties portent des choix de lots.")
    _refuse_if(
        "SELECT EXISTS (SELECT 1 FROM sales WHERE expired_lot_override_by IS NOT NULL)",
        "des ventes portent une dérogation à un lot périmé.",
    )
    for table in ("stock_movements", TABLE, "sales"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    role = _app_role()
    op.execute(f"REVOKE ALL ON {TABLE} FROM {role}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")

    op.drop_constraint(op.f("ck_sales_expired_lot_override_complete"), "sales", type_="check")
    op.drop_constraint(op.f("fk_sales_expired_lot_override_by_users"), "sales", type_="foreignkey")
    for column in (
        "expired_lot_override_reason",
        "expired_lot_override_at",
        "expired_lot_override_by",
    ):
        op.drop_column("sales", column)

    op.drop_index(op.f("ix_stock_exit_line_lots_tenant_id"), table_name=TABLE)
    op.drop_index(op.f("ix_stock_exit_line_lots_exit_line_id"), table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_constraint(
        op.f("uq_stock_exit_lines_tenant_id_id_article_id"), "stock_exit_lines", type_="unique"
    )

    op.drop_constraint("uq_stock_movements_line_type_site_lot", "stock_movements", type_="unique")
    op.create_unique_constraint(
        op.f("uq_stock_movements_line_type_site"), "stock_movements", MOVEMENT_KEY
    )
