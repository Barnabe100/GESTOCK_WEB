"""Palier R7-A — points QR d'un site (ADR-0052 ; ``RESTAURANT.md`` §10).

- ``restaurant_qr_points`` : point d'accès public d'UN site, TABLE existante du même site (FK
  composite ``RESTRICT``) ou COMPTOIR (libellé unique par site, insensible à la casse) ; un seul
  point ACTIF par table ; jeton jamais stocké — empreinte SHA-256 seule (``token_hash``, unique
  sur toute la plateforme, format contrôlé) ; jamais supprimé.
- RLS ``ENABLE`` + ``FORCE`` : politique ``tenant_isolation`` et politique ``qr_token_lookup``
  (lecture seule de la ligne dont l'empreinte est ``app.qr_token_hash``, accès public R7-B).
- Rôle applicatif : ``SELECT, INSERT`` et ``UPDATE`` des seules colonnes modifiables (libellé,
  jeton, état) ; aucune suppression ; aucun droit pour la console ni la purge.

Le module reste planifié (aucune activation modifiée : la migration de livraison s'en charge).
Descente refusée dès qu'un point existe.

Revision ID: 0056
Revises: 0055
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "restaurant_qr_points"


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_table(
        "restaurant_qr_points",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "TABLE",
                "COUNTER",
                name="restaurant_qr_point_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("table_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_generated_by", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
            "(kind = 'TABLE' AND table_id IS NOT NULL AND label IS NULL) OR "
            "(kind = 'COUNTER' AND table_id IS NULL AND label IS NOT NULL)",
            name=op.f("ck_restaurant_qr_points_kind_target"),
        ),
        sa.CheckConstraint(
            "label IS NULL OR (btrim(label) = label AND label <> '')",
            name=op.f("ck_restaurant_qr_points_label_trimmed"),
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_restaurant_qr_points_token_hash_format")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_restaurant_qr_points_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "table_id"],
            ["restaurant_tables.tenant_id", "restaurant_tables.site_id", "restaurant_tables.id"],
            name=op.f("fk_restaurant_qr_points_tenant_id_site_id_table_id_restaurant_tables"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_qr_points_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_qr_points_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["token_generated_by"],
            ["users.id"],
            name=op.f("fk_restaurant_qr_points_token_generated_by_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_qr_points")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_qr_points_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "id", name=op.f("uq_restaurant_qr_points_tenant_id_site_id_id")
        ),
    )
    op.create_index(
        op.f("ix_restaurant_qr_points_site_id"), "restaurant_qr_points", ["site_id"], unique=False
    )
    op.create_index(
        op.f("ix_restaurant_qr_points_tenant_id"),
        "restaurant_qr_points",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_restaurant_qr_points_active_table",
        "restaurant_qr_points",
        ["tenant_id", "table_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND table_id IS NOT NULL"),
    )
    op.create_index(
        "uq_restaurant_qr_points_site_label",
        "restaurant_qr_points",
        ["tenant_id", "site_id", sa.literal_column("lower(label)")],
        unique=True,
        postgresql_where=sa.text("label IS NOT NULL"),
    )
    op.create_index(
        "uq_restaurant_qr_points_token_hash", "restaurant_qr_points", ["token_hash"], unique=True
    )

    role = _app_role()
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    # Accès public (R7-B) : lecture de la SEULE ligne dont l'empreinte est celle du jeton
    # présenté (``app.qr_token_hash``, fixée par le serveur pour la transaction ; vide : aucune
    # ligne). Aucune écriture, aucun autre point visible, aucun contournement de la RLS.
    op.execute(
        f"CREATE POLICY qr_token_lookup ON {TABLE} FOR SELECT TO {role} "
        "USING (token_hash = NULLIF(current_setting('app.qr_token_hash', true), ''))"
    )
    op.execute(f"GRANT SELECT, INSERT ON {TABLE} TO {role}")
    op.execute(
        "GRANT UPDATE (label, token_hash, token_generated_at, token_generated_by, is_active, "
        f"updated_at) ON {TABLE} TO {role}"
    )


def downgrade() -> None:
    # Jamais de perte silencieuse : un point QR créé bloque le retour arrière.
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    if count:
        raise RuntimeError(f"Retour arrière refusé : {count} point(s) QR saisi(s) seraient perdus.")
    role = _app_role()
    op.execute(f"REVOKE ALL ON {TABLE} FROM {role}")
    op.execute(f"DROP POLICY IF EXISTS qr_token_lookup ON {TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.drop_index("uq_restaurant_qr_points_token_hash", table_name="restaurant_qr_points")
    op.drop_index(
        "uq_restaurant_qr_points_site_label",
        table_name="restaurant_qr_points",
        postgresql_where=sa.text("label IS NOT NULL"),
    )
    op.drop_index(
        "uq_restaurant_qr_points_active_table",
        table_name="restaurant_qr_points",
        postgresql_where=sa.text("is_active AND table_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_restaurant_qr_points_tenant_id"), table_name="restaurant_qr_points")
    op.drop_index(op.f("ix_restaurant_qr_points_site_id"), table_name="restaurant_qr_points")
    op.drop_table("restaurant_qr_points")
