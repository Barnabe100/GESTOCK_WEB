"""Fonctionnalités optionnelles des plans (Plan → limites, modules, fonctionnalités).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23 23:46:12.125478+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Valeur initiale pour les plans existants, puis plus de valeur par défaut côté base
    # (le catalogue synchronisé la fournit toujours).
    op.add_column(
        "plans",
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("plans", "features", server_default=None)


def downgrade() -> None:
    op.drop_column("plans", "features")
