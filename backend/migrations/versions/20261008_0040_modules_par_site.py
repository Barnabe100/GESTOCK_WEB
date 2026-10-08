"""Profils / modules par site, palier C — activation des modules portée par le SITE.

- ``site_modules`` : activation d'un module sur UN site (``enabled``), **source de vérité** des
  activations ; une ligne par (tenant, site, module), jamais supprimée (désactivation =
  ``enabled`` faux). FK composite ``(tenant_id, site_id)`` → ``sites`` : un site d'un autre
  tenant n'est pas référençable.
- Reprise DÉTERMINISTE : chaque site existant reçoit une copie exacte des lignes
  ``tenant_modules`` de son tenant (module, ``enabled``) — activations et désactivations
  explicites conservées ; aucun module inventé, aucun profil, plan, abonnement ni donnée métier
  modifié. Un module copié reste effectif seulement s'il est proposé par le profil du site et
  inclus dans l'abonnement du site (inchangé : même filtre qu'avant, appliqué par site).
- ``tenant_modules`` est conservé INTACT comme historique legacy : plus lu ni écrit par
  l'application.
- RLS ``ENABLE`` + ``FORCE`` (politique ``tenant_isolation``) ; droits ``SELECT, INSERT, UPDATE``
  pour le rôle applicatif (aucune suppression) ; aucun droit pour le rôle de la console.
  Reprise en propriétaire avec ``FORCE`` levé le temps de la copie (procédé des migrations
  0038, 0039).
- Retour arrière : refusé si l'activation d'un site diffère de celle de son tenant dans
  ``tenant_modules`` (configuration propre à un site, ou site créé depuis, perdue) ; sinon la
  table est supprimée.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-08
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "site_modules"
SOURCE_TABLES = ("sites", "tenant_modules")
LOG = logging.getLogger("alembic.runtime.migration")


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("module_code", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_site_modules_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_site_modules_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_site_modules")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "module_code",
            name=op.f("uq_site_modules_tenant_id_site_id_module_code"),
        ),
    )
    op.create_index(op.f("ix_site_modules_tenant_id"), TABLE, ["tenant_id"], unique=False)

    # Reprise : copie exacte des activations du tenant pour chacun de ses sites.
    for source in SOURCE_TABLES:
        op.execute(f"ALTER TABLE {source} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"INSERT INTO {TABLE} (id, tenant_id, site_id, module_code, enabled) "
        "SELECT gen_random_uuid(), s.tenant_id, s.id, m.module_code, m.enabled "
        "FROM sites s JOIN tenant_modules m ON m.tenant_id = s.tenant_id"
    )
    without = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM sites s WHERE NOT EXISTS "
                "(SELECT 1 FROM tenant_modules m WHERE m.tenant_id = s.tenant_id)"
            )
        )
        .scalar_one()
    )
    for source in SOURCE_TABLES:
        op.execute(f"ALTER TABLE {source} FORCE ROW LEVEL SECURITY")
    copied = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
    LOG.info("Activations reprises par site : %s ligne(s)", copied)
    if without:
        # Aucune activation au niveau du tenant : aucun module métier n'était effectif ; la
        # reprise n'en invente aucun (configuration identique, désormais par site).
        LOG.warning("%s site(s) sans activation à reprendre (tenant sans tenant_modules)", without)

    role = _app_role()
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    # Jamais de suppression : une désactivation passe ``enabled`` à faux.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {TABLE} TO {role}")


def downgrade() -> None:
    bind = op.get_bind()
    for source in (*SOURCE_TABLES, TABLE):
        op.execute(f"ALTER TABLE {source} NO FORCE ROW LEVEL SECURITY")
    # Configuration propre à un site : ligne de site sans équivalent identique au tenant, ou
    # ligne du tenant absente d'un de ses sites.
    diverging = bind.execute(
        sa.text(
            f"""
            SELECT count(*) FROM (
                SELECT sm.site_id FROM {TABLE} sm
                LEFT JOIN tenant_modules m
                  ON m.tenant_id = sm.tenant_id AND m.module_code = sm.module_code
                WHERE m.module_code IS NULL OR m.enabled <> sm.enabled
                UNION
                SELECT s.id FROM sites s
                JOIN tenant_modules m ON m.tenant_id = s.tenant_id
                LEFT JOIN {TABLE} sm
                  ON sm.site_id = s.id AND sm.module_code = m.module_code
                WHERE sm.id IS NULL
            ) AS d
            """
        )
    ).scalar_one()
    for source in (*SOURCE_TABLES, TABLE):
        op.execute(f"ALTER TABLE {source} FORCE ROW LEVEL SECURITY")
    if diverging:
        raise RuntimeError(
            f"Retour arrière refusé : {diverging} site(s) ont une activation de modules propre "
            "(différente de tenant_modules) ; ces configurations seraient perdues."
        )
    op.drop_index(op.f("ix_site_modules_tenant_id"), table_name=TABLE)
    op.drop_table(TABLE)
