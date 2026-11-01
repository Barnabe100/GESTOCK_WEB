"""Catalogue des opérateurs et établissements financiers (R6.5-B, ADR-0051).

``ref_payment_providers`` : référentiel GLOBAL (aucune donnée de tenant, pas de RLS, comme
``geo_countries``) géré par TechNova et synchronisé depuis ``payment_providers.toml`` par
``stockmanager catalog sync`` avec le rôle propriétaire ; **lecture seule** pour le rôle
applicatif (``SELECT``). Vide au départ : le catalogue n'est jamais requis (opérateurs
personnalisés des entreprises).

Descente : la table est supprimée ; son contenu provient entièrement des fichiers versionnés
(resynchronisable), aucune donnée d'entreprise n'est perdue.

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_table(
        "ref_payment_providers",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("names", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("instructions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("kinds", sa.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("countries", sa.ARRAY(sa.String(length=2)), nullable=False),
        sa.Column("currencies", sa.ARRAY(sa.String(length=3)), nullable=False),
        sa.Column("identifiers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "code ~ '^[a-z0-9][a-z0-9_.-]{1,49}$'",
            name=op.f("ck_ref_payment_providers_code_format"),
        ),
        sa.CheckConstraint(
            "cardinality(currencies) > 0", name=op.f("ck_ref_payment_providers_has_currency")
        ),
        sa.CheckConstraint(
            "cardinality(kinds) > 0", name=op.f("ck_ref_payment_providers_has_kind")
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name=op.f("ck_ref_payment_providers_name_not_blank")
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_ref_payment_providers")),
    )
    op.execute(f"GRANT SELECT ON ref_payment_providers TO {_app_role()}")


def downgrade() -> None:
    op.drop_table("ref_payment_providers")
