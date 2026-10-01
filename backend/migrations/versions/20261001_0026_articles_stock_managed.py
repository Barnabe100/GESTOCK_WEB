"""Lot 3-A — Articles gérés ou non en stock (ADR-0039).

- ``catalog_articles.stock_managed`` : ``true`` = article suivi en stock (comportement
  historique), ``false`` = article / service vendu sans mouvement ni contrôle de stock.
  Valeur par défaut ``true`` : tous les articles existants conservent leur comportement.

Aucune nouvelle table : la RLS (``ENABLE`` + ``FORCE``) et les droits du rôle applicatif
(``SELECT, INSERT, UPDATE`` sur la table) s'appliquent à la nouvelle colonne.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_articles",
        sa.Column("stock_managed", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("catalog_articles", "stock_managed")
