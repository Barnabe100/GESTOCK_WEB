"""Lot 3-E — index des réceptions par fournisseur (ADR-0043).

La fiche fournisseur (réceptions, synthèse, articles reçus) filtre ``stock_entries`` par
``supplier_id`` ; sans index, chaque consultation parcourt toutes les entrées du tenant, dont le
nombre croît sans limite. Aucune table ni colonne nouvelle : pas de RLS ni de droits à ajouter.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_stock_entries_tenant_supplier",
        "stock_entries",
        ["tenant_id", "supplier_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stock_entries_tenant_supplier", table_name="stock_entries")
