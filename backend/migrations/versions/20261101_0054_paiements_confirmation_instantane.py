"""Confirmation manuelle et instantané des coordonnées sur les paiements (R6.5-E, ADR-0051).

``payments`` reçoit :

- ``currency`` (ISO 4217, obligatoire) : devise du paiement (= devise du moyen = devise de la
  vente, contrôlée par le serveur ; aucune conversion) ;
- ``confirmed_by`` / ``confirmed_at`` : utilisateur et horodatage de la confirmation MANUELLE
  de la réception des fonds ; un paiement encaissé l'est toujours par une confirmation
  (``completed_is_confirmed`` ; ``PENDING`` reste réservé aux encaissements asynchrones) ;
- ``payment_method_account_id`` (FK composite) et l'instantané des coordonnées RÉELLEMENT
  utilisées : ``provider_label``, ``account_identifier_type``, ``account_label``,
  ``account_value``, ``account_holder`` (le reçu ne relit jamais la configuration).

Reprise : devise de l'entreprise ; paiements existants confirmés par leur auteur à leur date
d'encaissement (l'enregistrement valait confirmation). Paiement immuable : aucune de ces
colonnes n'est modifiable par le rôle applicatif (insertion seulement, comme 0025).

Descente refusée si elle perdait une information : identifiant de réception ou opérateur
figé, devise autre que celle de l'entreprise, confirmation par un autre utilisateur que l'auteur.

Revision ID: 0054
Revises: 0053
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SNAPSHOT = (
    ("provider_label", 100),
    ("account_identifier_type", 32),
    ("account_label", 60),
    ("account_value", 100),
    ("account_holder", 100),
)


def upgrade() -> None:
    op.add_column("payments", sa.Column("currency", sa.String(length=3)))
    op.add_column("payments", sa.Column("confirmed_by", sa.Uuid()))
    op.add_column("payments", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("payment_method_account_id", sa.Uuid()))
    for name, length in SNAPSHOT:
        op.add_column("payments", sa.Column(name, sa.String(length=length)))
    op.execute("ALTER TABLE payments NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE payments p SET currency = t.currency, confirmed_by = p.created_by, "
        "confirmed_at = p.paid_at FROM tenants t WHERE t.id = p.tenant_id"
    )
    op.execute("ALTER TABLE payments FORCE ROW LEVEL SECURITY")
    op.alter_column("payments", "currency", nullable=False)
    op.create_foreign_key(
        op.f("fk_payments_confirmed_by_users"), "payments", "users", ["confirmed_by"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_payments_tenant_id_payment_method_account_id_payment_method_accounts"),
        "payments",
        "payment_method_accounts",
        ["tenant_id", "payment_method_account_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_payments_completed_is_confirmed"),
        "payments",
        "status = 'PENDING' OR confirmed_at IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_payments_iso_currency"), "payments", "currency ~ '^[A-Z]{3}$'"
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("ALTER TABLE payments NO FORCE ROW LEVEL SECURITY")
    lost = bind.execute(
        sa.text(
            "SELECT count(*) FROM payments p JOIN tenants t ON t.id = p.tenant_id "
            "WHERE p.payment_method_account_id IS NOT NULL OR p.provider_label IS NOT NULL "
            "OR p.account_value IS NOT NULL OR p.currency <> t.currency "
            "OR p.confirmed_by IS DISTINCT FROM p.created_by"
        )
    ).scalar_one()
    op.execute("ALTER TABLE payments FORCE ROW LEVEL SECURITY")
    if lost:
        raise RuntimeError(
            f"Descente refusée : {lost} paiement(s) perdraient leur confirmation, leur devise "
            "ou les coordonnées de réception utilisées."
        )
    op.drop_constraint(op.f("ck_payments_iso_currency"), "payments", type_="check")
    op.drop_constraint(op.f("ck_payments_completed_is_confirmed"), "payments", type_="check")
    op.drop_constraint(
        op.f("fk_payments_tenant_id_payment_method_account_id_payment_method_accounts"),
        "payments",
        type_="foreignkey",
    )
    op.drop_constraint(op.f("fk_payments_confirmed_by_users"), "payments", type_="foreignkey")
    for name, _ in reversed(SNAPSHOT):
        op.drop_column("payments", name)
    op.drop_column("payments", "payment_method_account_id")
    op.drop_column("payments", "confirmed_at")
    op.drop_column("payments", "confirmed_by")
    op.drop_column("payments", "currency")
