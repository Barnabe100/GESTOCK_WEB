"""Remises sur les ventes (palier R9, ADR-0054).

Une vente porte au plus UNE remise globale : montant (``discount_amount``, calculé par le
serveur depuis un montant ou un pourcentage), motif obligatoire et auteur (``discount_by``).
``total = subtotal - discount_amount`` est garanti par une contrainte : le total reste la seule
base des paiements, du crédit, des créances et du reçu.

Ventes existantes : ``discount_amount = 0`` (valeur par défaut), motif et auteur ``NULL`` ; elles
satisfont les deux contraintes (``total = subtotal`` depuis 0007). Aucune donnée modifiée.

Droits : les privilèges de table de ``sales`` (0007) couvrent les nouvelles colonnes ; la
politique RLS existante s'applique sans changement.

Descente : refusée dès qu'une vente porte une remise (protection des données).

Revision ID: 0061
Revises: 0060
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sales",
        sa.Column(
            "discount_amount",
            sa.Numeric(precision=18, scale=2),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column("sales", sa.Column("discount_reason", sa.String(length=200), nullable=True))
    op.add_column("sales", sa.Column("discount_by", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_sales_discount_by_users"), "sales", "users", ["discount_by"], ["id"]
    )
    op.create_check_constraint(
        op.f("ck_sales_discount_consistent"),
        "sales",
        "discount_amount >= 0 AND discount_amount <= subtotal "
        "AND total = subtotal - discount_amount",
    )
    op.create_check_constraint(
        op.f("ck_sales_discount_has_reason"),
        "sales",
        "(discount_amount = 0) = (discount_reason IS NULL) "
        "AND (discount_amount = 0) = (discount_by IS NULL)",
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM sales WHERE discount_amount <> 0) THEN "
        "RAISE EXCEPTION 'Descente refusée : des ventes portent une remise.'; END IF; END $$"
    )
    op.drop_constraint(op.f("ck_sales_discount_has_reason"), "sales", type_="check")
    op.drop_constraint(op.f("ck_sales_discount_consistent"), "sales", type_="check")
    op.drop_constraint(op.f("fk_sales_discount_by_users"), "sales", type_="foreignkey")
    op.drop_column("sales", "discount_by")
    op.drop_column("sales", "discount_reason")
    op.drop_column("sales", "discount_amount")
