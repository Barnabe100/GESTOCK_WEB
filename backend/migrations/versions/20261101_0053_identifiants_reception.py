"""Identifiants de réception des moyens (R6.5-D, ADR-0051).

``payment_method_accounts`` : numéro de téléphone, code marchand, code agent, numéro de compte
ou identifiant personnalisé où l'entreprise reçoit les fonds, propre à UN site (``site_id``) ou
COMMUN à tous les sites (``NULL``), sans nombre maximal ; jamais supprimé (désactivé). FK
composites ``(tenant_id, …)`` vers le moyen et le site ; un même identifiant actif une seule fois
par moyen et par portée (index unique partiel, ``NULLS NOT DISTINCT``).

RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : lecture, insertion et modification des seules
colonnes de description et d'état (ni le moyen, ni le site, ni le type ne changent), aucune
suppression.

Descente refusée dès qu'un identifiant existe (aucune perte silencieuse).

Revision ID: 0053
Revises: 0052
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "payment_method_accounts"
UPDATABLE = (
    "custom_label, value, holder_name, instructions, sort_order, is_active, updated_at, updated_by"
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_table(
        "payment_method_accounts",
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=True),
        sa.Column(
            "identifier_type",
            sa.Enum(
                "PHONE",
                "MERCHANT_CODE",
                "AGENT_CODE",
                "ACCOUNT_NUMBER",
                "CUSTOM",
                name="receiving_identifier_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("custom_label", sa.String(length=60), nullable=True),
        sa.Column("value", sa.String(length=100), nullable=False),
        sa.Column("holder_name", sa.String(length=100), nullable=True),
        sa.Column("instructions", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
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
            "identifier_type <> 'CUSTOM' OR length(btrim(coalesce(custom_label, ''))) > 0",
            name=op.f("ck_payment_method_accounts_custom_has_label"),
        ),
        sa.CheckConstraint(
            "length(btrim(value)) > 0", name=op.f("ck_payment_method_accounts_value_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_payment_method_accounts_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_method_id"],
            ["payment_methods.tenant_id", "payment_methods.id"],
            name=op.f("fk_payment_method_accounts_tenant_id_payment_method_id_payment_methods"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_payment_method_accounts_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_payment_method_accounts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_payment_method_accounts_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_method_accounts")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_payment_method_accounts_tenant_id_id")
        ),
    )
    op.create_index(
        "ix_payment_method_accounts_method",
        "payment_method_accounts",
        ["tenant_id", "payment_method_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payment_method_accounts_tenant_id"),
        "payment_method_accounts",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_payment_method_accounts_active",
        "payment_method_accounts",
        ["tenant_id", "payment_method_id", "site_id", "identifier_type", "value"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        postgresql_nulls_not_distinct=True,
    )
    role = _app_role()
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT ON {TABLE} TO {role}")
    op.execute(f"GRANT UPDATE ({UPDATABLE}) ON {TABLE} TO {role}")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    if count:
        raise RuntimeError(
            f"Descente refusée : {count} identifiant(s) de réception seraient perdus."
        )
    op.drop_index("uq_payment_method_accounts_active", table_name=TABLE)
    op.drop_index(op.f("ix_payment_method_accounts_tenant_id"), table_name=TABLE)
    op.drop_index("ix_payment_method_accounts_method", table_name=TABLE)
    op.drop_table(TABLE)
