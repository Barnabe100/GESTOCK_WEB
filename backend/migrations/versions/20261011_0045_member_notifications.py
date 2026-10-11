"""Palier R4 — notifications des membres (ADR-0049 D8).

- ``member_notifications`` (plateforme, générique ; distincte des rappels d'abonnement) : site,
  destinataire, type, évènement source, entité, contexte d'affichage sans donnée sensible,
  ``read_at``. Unicité ``(entreprise, destinataire, type de source, source)`` : une seule
  notification par destinataire et par évènement (clé anti-doublon).
- RLS ``ENABLE`` + ``FORCE``. Rôle applicatif : isolation par entreprise (lecture, insertion par
  l'auteur de l'action, mise à jour) **et** politiques RESTRICTIVES : lecture et mise à jour par
  le seul destinataire (``app.user_id``). Droits : ``SELECT``, ``INSERT``, ``UPDATE (read_at)`` ;
  jamais ``DELETE``.
- Rôle de purge dédié (``stockmanager_purge``, créé par l'exploitation :
  ``docker/postgres/init/03-purge-role.sh``) : ``SELECT (id, created_at)`` et ``DELETE``, limités
  par sa politique aux notifications de plus de 30 jours (aucune autre table, aucune autre
  colonne, aucune notification récente).

Descente : supprime la table (notifications éphémères, purgées à 30 jours ; aucune donnée
métier ni historique de référence).

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "member_notifications"
# Conservation (ADR-0049 D8) — figée dans cette migration : la politique du rôle de purge ne
# laisse ni lire ni supprimer une notification plus récente.
EXPIRED = "created_at < now() - interval '30 days'"


def _quote(role: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote(role)


def _roles() -> tuple[str, str]:
    settings = get_settings()
    return _quote(settings.db_app_role), _quote(settings.db_purge_role)


def _require_purge_role() -> None:
    role = get_settings().db_purge_role
    found = op.get_bind().execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r AND NOT rolbypassrls"), {"r": role}
    )
    if found.first() is None:
        raise RuntimeError(
            f"Rôle SQL « {role} » absent (ou BYPASSRLS) : le créer avec "
            "docker/postgres/init/03-purge-role.sh avant la migration."
        )


def upgrade() -> None:
    _require_purge_role()
    op.create_table(
        TABLE,
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name=op.f("fk_member_notifications_recipient_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_member_notifications_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_member_notifications_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_member_notifications")),
        sa.UniqueConstraint(
            "tenant_id",
            "recipient_user_id",
            "source_type",
            "source_id",
            name="uq_member_notifications_source",
        ),
    )
    op.create_index(
        "ix_member_notifications_recipient",
        TABLE,
        ["tenant_id", "recipient_user_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_member_notifications_created_at", TABLE, ["created_at"], unique=False)
    op.create_index(op.f("ix_member_notifications_tenant_id"), TABLE, ["tenant_id"], unique=False)

    app_role, purge_role = _roles()
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    # Rôle applicatif : entreprise courante (l'auteur d'une action insère les notifications de
    # ses destinataires) ; lecture et mise à jour RESTREINTES au destinataire.
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} TO {app_role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(
        f"CREATE POLICY recipient_read ON {TABLE} AS RESTRICTIVE FOR SELECT TO {app_role} "
        "USING (recipient_user_id = app_current_user_id())"
    )
    op.execute(
        f"CREATE POLICY recipient_update ON {TABLE} AS RESTRICTIVE FOR UPDATE TO {app_role} "
        "USING (recipient_user_id = app_current_user_id()) "
        "WITH CHECK (recipient_user_id = app_current_user_id())"
    )
    op.execute(f"GRANT SELECT, INSERT ON {TABLE} TO {app_role}")
    op.execute(f"GRANT UPDATE (read_at) ON {TABLE} TO {app_role}")
    # Rôle de purge : notifications de plus de 30 jours seulement, identifiant et date.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {purge_role}")
    op.execute(f"GRANT SELECT (id, created_at), DELETE ON {TABLE} TO {purge_role}")
    op.execute(f"CREATE POLICY purge_read ON {TABLE} FOR SELECT TO {purge_role} USING ({EXPIRED})")
    op.execute(
        f"CREATE POLICY purge_delete ON {TABLE} FOR DELETE TO {purge_role} USING ({EXPIRED})"
    )


def downgrade() -> None:
    app_role, purge_role = _roles()
    op.execute(f"REVOKE ALL ON {TABLE} FROM {app_role}")
    op.execute(f"REVOKE ALL ON {TABLE} FROM {purge_role}")
    for policy in ("purge_delete", "purge_read", "recipient_update", "recipient_read"):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {TABLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {purge_role}")
    op.drop_index(op.f("ix_member_notifications_tenant_id"), table_name=TABLE)
    op.drop_index("ix_member_notifications_created_at", table_name=TABLE)
    op.drop_index("ix_member_notifications_recipient", table_name=TABLE)
    op.drop_table(TABLE)
