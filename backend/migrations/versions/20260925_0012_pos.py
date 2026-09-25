"""Point de vente (Phase 3.0, ADR-0023) : aucune nouvelle table.

- ``sales.channel`` : canal de saisie (``BACKOFFICE`` par défaut, ``POS``), dimension de
  reporting ; ventes existantes : ``BACKOFFICE`` ;
- ``sales.idempotency_key`` : clé fournie par le client pour un encaissement en une étape,
  unique par tenant (double soumission d'un panier : une seule vente).

Tables et droits inchangés (RLS déjà active sur ``sales``).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sales",
        sa.Column(
            "channel",
            sa.Enum(
                "BACKOFFICE",
                "POS",
                name="sale_channel",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            # Contrainte CHECK ``ck_sales_sale_channel`` créée avec la colonne.
            server_default="BACKOFFICE",
            nullable=False,
        ),
    )
    op.add_column("sales", sa.Column("idempotency_key", sa.Uuid(), nullable=True))
    op.create_unique_constraint(
        op.f("uq_sales_tenant_id_idempotency_key"), "sales", ["tenant_id", "idempotency_key"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_sales_tenant_id_idempotency_key"), "sales", type_="unique")
    op.drop_column("sales", "idempotency_key")
    op.drop_column("sales", "channel")
