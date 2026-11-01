"""Pays de rattachement des identifiants de réception (R6.5, précision ADR-0051).

- ``payment_method_accounts.country_code`` (référentiel ``geo_countries``) : pays de
  rattachement de l'identifiant ; repris du moyen pour les identifiants existants. Un téléphone
  au format international (« + ») doit commencer par l'indicatif de ce pays ; un numéro local
  reste tel quel (l'indicatif n'est jamais ajouté).
- ``payments.account_country_code`` : instantané du pays de rattachement de l'identifiant
  utilisé (insertion seulement : paiement immuable).

Droits : ``country_code`` ajouté aux colonnes modifiables des identifiants (RLS inchangée).
Descente refusée si un pays de rattachement diffère de celui du moyen ou si un paiement en porte
un (aucune perte silencieuse).

Revision ID: 0055
Revises: 0054
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNTS = "payment_method_accounts"
UPDATABLE = (
    "custom_label, value, holder_name, instructions, sort_order, is_active, updated_at, updated_by"
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.add_column(ACCOUNTS, sa.Column("country_code", sa.String(length=2)))
    op.add_column("payments", sa.Column("account_country_code", sa.String(length=2)))
    op.execute(f"ALTER TABLE {ACCOUNTS} NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment_methods NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"UPDATE {ACCOUNTS} a SET country_code = m.country_code FROM payment_methods m "
        "WHERE m.id = a.payment_method_id AND m.tenant_id = a.tenant_id"
    )
    op.execute("ALTER TABLE payment_methods FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {ACCOUNTS} FORCE ROW LEVEL SECURITY")
    op.create_foreign_key(
        op.f("fk_payment_method_accounts_country_code_geo_countries"),
        ACCOUNTS,
        "geo_countries",
        ["country_code"],
        ["code"],
        ondelete="RESTRICT",
    )
    op.execute(f"GRANT UPDATE ({UPDATABLE}, country_code) ON {ACCOUNTS} TO {_app_role()}")


def downgrade() -> None:
    bind = op.get_bind()
    for table in (ACCOUNTS, "payment_methods", "payments"):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    lost = bind.execute(
        sa.text(
            f"SELECT (SELECT count(*) FROM {ACCOUNTS} a JOIN payment_methods m "
            "ON m.id = a.payment_method_id "
            "WHERE a.country_code IS DISTINCT FROM m.country_code) "
            "+ (SELECT count(*) FROM payments WHERE account_country_code IS NOT NULL)"
        )
    ).scalar_one()
    for table in (ACCOUNTS, "payment_methods", "payments"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if lost:
        raise RuntimeError(
            f"Descente refusée : {lost} pays de rattachement d'identifiants ou de paiements "
            "seraient perdus."
        )
    role = _app_role()
    op.execute(f"REVOKE UPDATE ON {ACCOUNTS} FROM {role}")
    op.execute(f"GRANT UPDATE ({UPDATABLE}) ON {ACCOUNTS} TO {role}")
    op.drop_constraint(
        op.f("fk_payment_method_accounts_country_code_geo_countries"), ACCOUNTS, type_="foreignkey"
    )
    op.drop_column("payments", "account_country_code")
    op.drop_column(ACCOUNTS, "country_code")
