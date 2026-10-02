"""Lot 3-H-B1 — transferts inter-sites par lot (ADR-0045).

- ``stock_transfer_line_lots`` : choix manuels des lots d'une ligne de transfert BROUILLON
  (quantité en unité de base), supprimés et recréés avec la ligne, revalidés à la validation ;
  jamais source historique (le journal des mouvements fait foi : une paire ``TRANSFER_OUT`` /
  ``TRANSFER_IN`` par lot, sous le MÊME lot des deux côtés). FK composites : ligne du même
  tenant et du même article, lot du même article.
- ``stock_transfer_lines`` : unicité ``(tenant_id, id, article_id)`` (cible de la FK composite).

Migration additive. ``stock_movements`` inchangée (l'unicité inclut déjà le lot, 0035).
RLS ``ENABLE`` + ``FORCE`` sur la nouvelle table, droits minimaux (lignes de brouillon :
``SELECT, INSERT, UPDATE, DELETE`` comme les lignes de transfert). Retour arrière : refusé si
des choix de lots de transfert existent (données perdues).

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "stock_transfer_line_lots"


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_stock_transfer_lines_tenant_id_id_article_id"),
        "stock_transfer_lines",
        ["tenant_id", "id", "article_id"],
    )
    op.create_table(
        TABLE,
        sa.Column("transfer_line_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_stock_transfer_line_lots_quantity_positive")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "lot_id"],
            ["stock_lots.tenant_id", "stock_lots.article_id", "stock_lots.id"],
            name=op.f("fk_stock_transfer_line_lots_tenant_id_article_id_lot_id_stock_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "transfer_line_id", "article_id"],
            [
                "stock_transfer_lines.tenant_id",
                "stock_transfer_lines.id",
                "stock_transfer_lines.article_id",
            ],
            name=op.f(
                "fk_stock_transfer_line_lots_tenant_id_transfer_line_id_article_id_"
                "stock_transfer_lines"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_stock_transfer_line_lots_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_transfer_line_lots")),
        sa.UniqueConstraint(
            "transfer_line_id",
            "lot_id",
            name=op.f("uq_stock_transfer_line_lots_transfer_line_id_lot_id"),
        ),
    )
    op.create_index(
        op.f("ix_stock_transfer_line_lots_transfer_line_id"), TABLE, ["transfer_line_id"]
    )
    op.create_index(op.f("ix_stock_transfer_line_lots_tenant_id"), TABLE, ["tenant_id"])

    # --- RLS et droits minimaux ----------------------------------------------------------------
    role = _app_role()
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    # Données de brouillon : remplacées avec les lignes tant que le transfert n'est pas validé.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {role}")


def downgrade() -> None:
    # Propriétaire : RLS levée le temps du contrôle (aucune donnée perdue silencieusement).
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    if op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {TABLE})")).scalar():
        raise RuntimeError("Retour arrière refusé : des transferts portent des choix de lots.")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")

    role = _app_role()
    op.execute(f"REVOKE ALL ON {TABLE} FROM {role}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.drop_index(op.f("ix_stock_transfer_line_lots_tenant_id"), table_name=TABLE)
    op.drop_index(op.f("ix_stock_transfer_line_lots_transfer_line_id"), table_name=TABLE)
    op.drop_table(TABLE)
    op.drop_constraint(
        op.f("uq_stock_transfer_lines_tenant_id_id_article_id"),
        "stock_transfer_lines",
        type_="unique",
    )
