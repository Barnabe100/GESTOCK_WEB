"""Inscription publique (Phase 3.2-A, ADR-0025).

- ``subscriptions.status`` accepte ``pending_activation`` : souscription commerciale
  enregistrée, sans paiement confirmé ni licence activée (accès administratif seulement ;
  politique d'accès : ``subscription_policies.toml``, synchronisée par ``catalog sync``).
- Le propriétaire de chaque tenant existant reçoit aussi le rôle protégé d'administration de
  son tenant (propriété ≠ rôle : ``is_owner`` reste la seule source de la propriété).
  La RLS forcée est levée le temps de l'instruction, dans la transaction de la migration.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE = ("trial", "active", "past_due", "expired", "suspended", "cancelled")
_TABLES = ("tenant_memberships", "roles", "membership_roles", "subscriptions")


def _status_check(statuses: tuple[str, ...]) -> None:
    op.drop_constraint(op.f("ck_subscriptions_subscription_status"), "subscriptions", type_="check")
    values = ", ".join(f"'{s}'" for s in statuses)
    op.create_check_constraint("subscription_status", "subscriptions", f"status IN ({values})")


def _without_forced_rls(sql: str) -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(sql)
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    _status_check(("pending_activation", *_BASE))
    _without_forced_rls(
        """
        INSERT INTO membership_roles (id, tenant_id, membership_id, role_id, site_id)
        SELECT gen_random_uuid(), m.tenant_id, m.id, r.id, NULL
        FROM tenant_memberships m
        JOIN roles r ON r.tenant_id = m.tenant_id AND r.template_code = 'administrator'
        WHERE m.is_owner
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    # Un abonnement en attente d'activation n'existe pas avant 0015 : il devient suspendu
    # (aucun accès métier, régularisation seulement), jamais actif.
    _without_forced_rls(
        "UPDATE subscriptions SET status = 'suspended' WHERE status = 'pending_activation'"
    )
    _status_check(_BASE)
    # Les rôles d'administration des propriétaires ne sont attribués que par le provisioning
    # (le propriétaire n'est pas modifiable par les membres) : retrait sans ambiguïté.
    _without_forced_rls(
        """
        DELETE FROM membership_roles mr
        USING tenant_memberships m, roles r
        WHERE mr.membership_id = m.id AND m.is_owner AND mr.role_id = r.id
          AND mr.site_id IS NULL AND r.template_code = 'administrator'
        """
    )
