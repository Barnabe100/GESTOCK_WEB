"""Opérateur, pays et devise des moyens de réception (R6.5-C, ADR-0051).

``payment_methods`` reçoit :

- ``provider_code`` (catalogue TechNova, FK ``ref_payment_providers``) OU ``provider_name``
  (opérateur personnalisé saisi par l'entreprise) OU aucun (``one_provider``) ;
- ``country_code`` (référentiel ``geo_countries`` ; ``NULL`` : international) ;
- ``currency`` (ISO 4217, obligatoire) : un moyen n'encaisse une vente que dans SA devise.

Reprise : les moyens EXISTANTS reçoivent le pays et la devise de leur entreprise (comportement
inchangé) ; aucun opérateur n'est deviné depuis un libellé. Droits : colonnes modifiables
ajoutées à la liste du rôle applicatif (RLS ``FORCE`` inchangée ; levée le temps de la reprise
par le propriétaire, comme 0025).

Descente refusée si elle perdait une donnée : opérateur renseigné, ou moyen dans une autre
devise ou un autre pays que ceux de l'entreprise.

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPDATABLE = (
    "label, integration_mode, reference_required, is_active, sort_order, updated_at, "
    "provider_code, provider_name, country_code, currency"
)
PREVIOUS_UPDATABLE = (
    "label, integration_mode, reference_required, is_active, sort_order, updated_at"
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.add_column("payment_methods", sa.Column("provider_code", sa.String(length=50)))
    op.add_column("payment_methods", sa.Column("provider_name", sa.String(length=100)))
    op.add_column("payment_methods", sa.Column("country_code", sa.String(length=2)))
    op.add_column("payment_methods", sa.Column("currency", sa.String(length=3)))
    op.execute("ALTER TABLE payment_methods NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE payment_methods pm SET currency = t.currency, country_code = t.country_code "
        "FROM tenants t WHERE t.id = pm.tenant_id"
    )
    op.execute("ALTER TABLE payment_methods FORCE ROW LEVEL SECURITY")
    op.alter_column("payment_methods", "currency", nullable=False)
    op.create_foreign_key(
        op.f("fk_payment_methods_country_code_geo_countries"),
        "payment_methods",
        "geo_countries",
        ["country_code"],
        ["code"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_payment_methods_provider_code_ref_payment_providers"),
        "payment_methods",
        "ref_payment_providers",
        ["provider_code"],
        ["code"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_payment_methods_one_provider"),
        "payment_methods",
        "num_nonnulls(provider_code, provider_name) <= 1",
    )
    op.create_check_constraint(
        op.f("ck_payment_methods_provider_name_not_blank"),
        "payment_methods",
        "provider_name IS NULL OR length(btrim(provider_name)) > 0",
    )
    op.create_check_constraint(
        op.f("ck_payment_methods_iso_currency"), "payment_methods", "currency ~ '^[A-Z]{3}$'"
    )
    op.execute(f"GRANT UPDATE ({UPDATABLE}) ON payment_methods TO {_app_role()}")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("ALTER TABLE payment_methods NO FORCE ROW LEVEL SECURITY")
    lost = bind.execute(
        sa.text(
            "SELECT count(*) FROM payment_methods pm JOIN tenants t ON t.id = pm.tenant_id "
            "WHERE pm.provider_code IS NOT NULL OR pm.provider_name IS NOT NULL "
            "OR pm.currency <> t.currency "
            "OR pm.country_code IS DISTINCT FROM t.country_code"
        )
    ).scalar_one()
    op.execute("ALTER TABLE payment_methods FORCE ROW LEVEL SECURITY")
    if lost:
        raise RuntimeError(
            f"Descente refusée : {lost} moyen(s) de réception perdraient leur opérateur, leur "
            "pays ou leur devise."
        )
    role = _app_role()
    op.execute(f"REVOKE UPDATE ON payment_methods FROM {role}")
    op.execute(f"GRANT UPDATE ({PREVIOUS_UPDATABLE}) ON payment_methods TO {role}")
    for name in ("iso_currency", "provider_name_not_blank", "one_provider"):
        op.drop_constraint(op.f(f"ck_payment_methods_{name}"), "payment_methods", type_="check")
    op.drop_constraint(
        op.f("fk_payment_methods_provider_code_ref_payment_providers"),
        "payment_methods",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_payment_methods_country_code_geo_countries"),
        "payment_methods",
        type_="foreignkey",
    )
    op.drop_column("payment_methods", "currency")
    op.drop_column("payment_methods", "country_code")
    op.drop_column("payment_methods", "provider_name")
    op.drop_column("payment_methods", "provider_code")
