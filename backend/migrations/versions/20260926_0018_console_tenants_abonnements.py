"""Console TechNova : tenants et abonnements (Phase 3.2-G, ADR-0031).

Droits **supplémentaires et minimaux** du rôle de la console (``SM_DB_PLATFORM_ROLE``), sans
aucun changement de schéma. Politiques RLS réservées à ce rôle (``TO``) ; le rôle applicatif des
tenants et ses politiques sont inchangés.

- ``tenants`` : lecture des seules colonnes d'identité plateforme (nom, nom commercial, slug,
  statut, profil, pays, devise, langue, fuseau, dates) — ni coordonnées, ni identifiants
  fiscaux ; modification du seul ``status`` (suspension / réactivation).
- ``subscriptions`` : lecture ; modification du plan, du statut, de la période et du prix figé
  (activation, prolongation, changement de plan). Jamais de suppression.
- ``sites`` et ``tenant_memberships`` : lecture de ``tenant_id`` et de l'état seulement
  (compteurs d'utilisation) — ni noms, ni utilisateurs, ni rôles.
- ``audit_logs`` : insertion seule d'entrées **miroir** (tenant renseigné, aucun utilisateur),
  aucune lecture.

Toujours aucun droit sur les tables métier (ventes, stock, clients, caisse…).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

from app.core.config import get_settings

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_COLUMNS = (
    "id",
    "name",
    "trade_name",
    "slug",
    "status",
    "business_profile_code",
    "country_code",
    "currency",
    "locale",
    "timezone",
    "created_at",
    "updated_at",
)
SUBSCRIPTION_UPDATE_COLUMNS = (
    "plan_code",
    "status",
    "current_period_start",
    "current_period_end",
    "price_at_subscription",
    "currency_at_subscription",
    "updated_at",
)


def _platform_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_platform_role)


def upgrade() -> None:
    role = _platform_role()
    op.execute(f"GRANT SELECT ({', '.join(TENANT_COLUMNS)}) ON tenants TO {role}")
    op.execute(f"GRANT UPDATE (status, updated_at) ON tenants TO {role}")
    op.execute(f"CREATE POLICY platform_read ON tenants FOR SELECT TO {role} USING (true)")
    op.execute(
        f"CREATE POLICY platform_status ON tenants FOR UPDATE TO {role} "
        "USING (true) WITH CHECK (true)"
    )

    op.execute(f"GRANT SELECT ON subscriptions TO {role}")
    op.execute(
        f"GRANT UPDATE ({', '.join(SUBSCRIPTION_UPDATE_COLUMNS)}) ON subscriptions TO {role}"
    )
    op.execute(f"CREATE POLICY platform_read ON subscriptions FOR SELECT TO {role} USING (true)")
    op.execute(
        f"CREATE POLICY platform_manage ON subscriptions FOR UPDATE TO {role} "
        "USING (true) WITH CHECK (true)"
    )

    op.execute(f"GRANT SELECT (tenant_id, is_active) ON sites TO {role}")
    op.execute(f"CREATE POLICY platform_count ON sites FOR SELECT TO {role} USING (true)")
    op.execute(f"GRANT SELECT (tenant_id, status) ON tenant_memberships TO {role}")
    op.execute(
        f"CREATE POLICY platform_count ON tenant_memberships FOR SELECT TO {role} USING (true)"
    )

    op.execute(f"GRANT INSERT ON audit_logs TO {role}")
    mirror = "tenant_id IS NOT NULL AND user_id IS NULL"
    op.execute(
        f"CREATE POLICY platform_mirror_insert ON audit_logs FOR INSERT TO {role} "
        f"WITH CHECK ({mirror})"
    )
    # Restrictive : s'ajoute (ET) à la politique permissive commune ``tenant_insert``, qui
    # autorise sinon les entrées sans tenant.
    op.execute(
        f"CREATE POLICY platform_mirror_only ON audit_logs AS RESTRICTIVE FOR INSERT TO {role} "
        f"WITH CHECK ({mirror})"
    )


def downgrade() -> None:
    role = _platform_role()
    op.execute("DROP POLICY IF EXISTS platform_mirror_only ON audit_logs")
    op.execute("DROP POLICY IF EXISTS platform_mirror_insert ON audit_logs")
    op.execute(f"REVOKE ALL ON audit_logs FROM {role}")
    op.execute("DROP POLICY IF EXISTS platform_count ON tenant_memberships")
    op.execute(f"REVOKE ALL ON tenant_memberships FROM {role}")
    op.execute("DROP POLICY IF EXISTS platform_count ON sites")
    op.execute(f"REVOKE ALL ON sites FROM {role}")
    op.execute("DROP POLICY IF EXISTS platform_manage ON subscriptions")
    op.execute("DROP POLICY IF EXISTS platform_read ON subscriptions")
    op.execute(f"REVOKE ALL ON subscriptions FROM {role}")
    op.execute("DROP POLICY IF EXISTS platform_status ON tenants")
    op.execute("DROP POLICY IF EXISTS platform_read ON tenants")
    op.execute(f"REVOKE ALL ON tenants FROM {role}")
