"""Abonnement par site (Phase 3.3-B1, ADR-0033) : 1 site = 1 abonnement.

- ``subscriptions.site_id`` : clé étrangère composite ``(tenant_id, site_id) → sites`` (jamais
  le site d'un autre tenant) ; unicité ``(tenant_id, site_id)`` ; ``site_id`` nul seulement pour
  l'abonnement pris à l'inscription avant la création du premier site (au plus un par
  entreprise : index unique partiel ``uq_subscriptions_unattached``). L'unicité ``tenant_id``
  (un abonnement par entreprise) disparaît.
- ``subscriptions.requested_activations`` : nombre de postes demandé (défaut 1, ≥ 1).
- **Données existantes** (aucune entreprise ne perd son accès) : l'abonnement actuel de chaque
  entreprise est rattaché à son site le plus ancien ; chaque autre site reçoit une copie (plan,
  période, statut, prix figé) ; une entreprise sans site garde un abonnement non rattaché.
- Rôle de la console : lecture du nom et du code des sites, et des accès des appartenances
  (compteurs d'utilisateurs par site : ``is_owner``, ``all_sites``, ``membership_sites``) ;
  aucune donnée métier.

Retour arrière (perte d'information assumée) : un seul abonnement par entreprise est conservé
(celui du site le plus ancien, ou le non rattaché) ; les paiements des autres y sont rattachés.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COPIED_COLUMNS = (
    "plan_code",
    "billing_period",
    "status",
    "started_at",
    "current_period_start",
    "current_period_end",
    "cancelled_at",
    "price_at_subscription",
    "currency_at_subscription",
)


def _platform_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_platform_role)


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("site_id", sa.Uuid(), nullable=True))
    op.add_column(
        "subscriptions",
        sa.Column(
            "requested_activations", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_subscriptions_requested_activations_positive"),
        "subscriptions",
        "requested_activations >= 1",
    )

    op.drop_constraint(op.f("uq_subscriptions_tenant_id"), "subscriptions", type_="unique")
    # Rattachement de l'abonnement actuel au site le plus ancien, copies pour les autres sites.
    op.execute(
        """
        UPDATE subscriptions s SET site_id = (
            SELECT st.id FROM sites st WHERE st.tenant_id = s.tenant_id
            ORDER BY st.created_at, st.id LIMIT 1
        )
        """
    )
    columns = ", ".join(COPIED_COLUMNS)
    selected = ", ".join(f"s.{c}" for c in COPIED_COLUMNS)
    op.execute(
        f"""
        INSERT INTO subscriptions (id, tenant_id, site_id, {columns}, requested_activations,
                                   created_at, updated_at)
        SELECT gen_random_uuid(), s.tenant_id, st.id, {selected}, 1, now(), now()
        FROM subscriptions s JOIN sites st ON st.tenant_id = s.tenant_id
        WHERE s.site_id IS NOT NULL AND st.id <> s.site_id
        """
    )

    op.create_unique_constraint(
        op.f("uq_subscriptions_tenant_id_site_id"), "subscriptions", ["tenant_id", "site_id"]
    )
    op.create_index(
        "uq_subscriptions_unattached",
        "subscriptions",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("site_id IS NULL"),
    )
    op.create_foreign_key(
        op.f("fk_subscriptions_tenant_id_site_id_sites"),
        "subscriptions",
        "sites",
        ["tenant_id", "site_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    role = _platform_role()
    op.execute(f"GRANT SELECT (id, name, code) ON sites TO {role}")
    op.execute(f"GRANT SELECT (id, is_owner, all_sites) ON tenant_memberships TO {role}")
    op.execute(f"GRANT SELECT (tenant_id, membership_id, site_id) ON membership_sites TO {role}")
    op.execute(
        f"CREATE POLICY platform_count ON membership_sites FOR SELECT TO {role} USING (true)"
    )


def downgrade() -> None:
    role = _platform_role()
    op.execute("DROP POLICY IF EXISTS platform_count ON membership_sites")
    op.execute(f"REVOKE ALL ON membership_sites FROM {role}")
    op.execute(f"REVOKE SELECT (id, is_owner, all_sites) ON tenant_memberships FROM {role}")
    op.execute(f"REVOKE SELECT (id, name, code) ON sites FROM {role}")

    op.drop_constraint(
        op.f("fk_subscriptions_tenant_id_site_id_sites"), "subscriptions", type_="foreignkey"
    )
    op.drop_index("uq_subscriptions_unattached", table_name="subscriptions")
    op.drop_constraint(op.f("uq_subscriptions_tenant_id_site_id"), "subscriptions", type_="unique")
    # Un abonnement conservé par entreprise : non rattaché, sinon celui du site le plus ancien.
    op.execute(
        """
        CREATE TEMPORARY TABLE kept_subscriptions ON COMMIT DROP AS
        SELECT DISTINCT ON (s.tenant_id) s.tenant_id, s.id
        FROM subscriptions s LEFT JOIN sites st ON st.id = s.site_id
        ORDER BY s.tenant_id, (s.site_id IS NOT NULL), st.created_at, st.id
        """
    )
    # Déclencheur de finalité des paiements (0019) : rattachement technique du retour arrière.
    op.execute("ALTER TABLE subscription_payments DISABLE TRIGGER subscription_payments_final")
    op.execute(
        """
        UPDATE subscription_payments p SET subscription_id = k.id
        FROM kept_subscriptions k
        WHERE p.tenant_id = k.tenant_id AND p.subscription_id <> k.id
        """
    )
    op.execute("ALTER TABLE subscription_payments ENABLE TRIGGER subscription_payments_final")
    op.execute(
        "DELETE FROM subscriptions s WHERE NOT EXISTS "
        "(SELECT 1 FROM kept_subscriptions k WHERE k.id = s.id)"
    )
    op.create_unique_constraint(op.f("uq_subscriptions_tenant_id"), "subscriptions", ["tenant_id"])
    op.drop_constraint(
        op.f("ck_subscriptions_requested_activations_positive"), "subscriptions", type_="check"
    )
    op.drop_column("subscriptions", "requested_activations")
    op.drop_column("subscriptions", "site_id")
