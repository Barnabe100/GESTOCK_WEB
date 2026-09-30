"""Lot 1 — Encaissement (ADR-0037).

- ``payment_methods`` : moyens de paiement **configurés** par l'entreprise (libellé, type qui
  gouverne le comportement, saisie manuelle / API future, référence obligatoire, actif) ;
  ``payment_method_sites`` : disponibilité par site (ligne absente : disponible). Chaque
  entreprise existante reçoit les moyens par défaut (Espèces, Mobile Money, Carte bancaire,
  Virement, Autre), modifiables ensuite.
- ``payments`` : moyen configuré + libellé **figé** au paiement, montant reçu et monnaie
  (espèces). Reprise : chaque paiement existant est relié au moyen par défaut de son type ; son
  libellé figé est la précision saisie autrefois (``provider``), sinon le libellé historique du
  type — aucune donnée inventée, ``provider`` est conservé.
- ``sales`` : numéro nul pour un brouillon (attribué à la validation : ``VENT-…``), jusqu'à 64
  caractères ; les numéros historiques ``VTE-…`` restent inchangés. ``is_credit`` (reprise :
  reste dû à la validation d'après les paiements datés au plus tard de la validation) et
  exception de limite de crédit (autorisateur, date, justification, montant).
- ``cash_site_settings`` : caisse activée ou non **par site** ; reprise : activée pour les sites
  qui ont déjà des caisses (comportement inchangé), non activée ailleurs.

RLS ``ENABLE`` + ``FORCE`` sur les nouvelles tables ; rôle applicatif : lecture, insertion et
mise à jour des seules colonnes modifiables (le type d'un moyen de paiement est immuable ;
aucune suppression).

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Moyens par défaut (données de départ, modifiables par l'entreprise) : type, libellé, ordre.
DEFAULT_METHODS = (
    ("CASH", "Espèces", 10),
    ("MOBILE_MONEY", "Mobile Money", 20),
    ("CARD", "Carte bancaire", 30),
    ("BANK_TRANSFER", "Virement", 40),
    ("OTHER", "Autre", 50),
)
GRANTS = {
    "payment_methods": (
        "SELECT, INSERT",
        "label, integration_mode, reference_required, is_active, sort_order, updated_at",
    ),
    "payment_method_sites": ("SELECT, INSERT", "is_enabled, updated_at, updated_by"),
    "cash_site_settings": ("SELECT, INSERT", "enabled, updated_at, updated_by"),
}
# Colonnes d'un paiement modifiables par l'application : son annulation seulement.
PAYMENT_UPDATABLE = "status, cancelled_at, cancelled_by, cancellation_reason, updated_at"
BACKFILLED = (
    "tenants",
    "payment_methods",
    "payments",
    "sales",
    "cash_registers",
    "cash_site_settings",
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _kind_enum(name: str) -> sa.Enum:
    return sa.Enum(
        "CASH",
        "MOBILE_MONEY",
        "CARD",
        "BANK_TRANSFER",
        "OTHER",
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
    )


def upgrade() -> None:
    op.create_table(
        "payment_methods",
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("kind", _kind_enum("payment_method_kind"), nullable=False),
        sa.Column(
            "integration_mode",
            sa.Enum(
                "MANUAL",
                "API",
                name="payment_integration",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="MANUAL",
            nullable=False,
        ),
        sa.Column("reference_required", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
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
            "length(btrim(label)) > 0", name=op.f("ck_payment_methods_label_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_payment_methods_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_payment_methods_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_methods")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_payment_methods_tenant_id_id")),
        sa.UniqueConstraint("tenant_id", "label", name=op.f("uq_payment_methods_tenant_id_label")),
    )
    op.create_index(
        op.f("ix_payment_methods_tenant_id"), "payment_methods", ["tenant_id"], unique=False
    )
    op.create_table(
        "cash_site_settings",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_cash_site_settings_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_cash_site_settings_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_cash_site_settings_updated_by_users")
        ),
        sa.PrimaryKeyConstraint("site_id", name=op.f("pk_cash_site_settings")),
    )
    op.create_index(
        op.f("ix_cash_site_settings_tenant_id"), "cash_site_settings", ["tenant_id"], unique=False
    )
    op.create_table(
        "payment_method_sites",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_method_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_method_id"],
            ["payment_methods.tenant_id", "payment_methods.id"],
            name=op.f("fk_payment_method_sites_tenant_id_payment_method_id_payment_methods"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_payment_method_sites_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_payment_method_sites_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_payment_method_sites_updated_by_users")
        ),
        sa.PrimaryKeyConstraint(
            "payment_method_id", "site_id", name=op.f("pk_payment_method_sites")
        ),
    )
    op.create_index(
        op.f("ix_payment_method_sites_tenant_id"),
        "payment_method_sites",
        ["tenant_id"],
        unique=False,
    )
    op.add_column("payments", sa.Column("payment_method_id", sa.Uuid(), nullable=True))
    op.add_column("payments", sa.Column("method_label", sa.String(length=60), nullable=True))
    op.add_column(
        "payments", sa.Column("amount_received", sa.Numeric(precision=18, scale=2), nullable=True)
    )
    op.add_column(
        "payments", sa.Column("change_given", sa.Numeric(precision=18, scale=2), nullable=True)
    )
    op.create_foreign_key(
        op.f("fk_payments_tenant_id_payment_method_id_payment_methods"),
        "payments",
        "payment_methods",
        ["tenant_id", "payment_method_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "sales", sa.Column("is_credit", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column("sales", sa.Column("credit_override_by", sa.Uuid(), nullable=True))
    op.add_column(
        "sales", sa.Column("credit_override_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "sales", sa.Column("credit_override_reason", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "sales",
        sa.Column("credit_override_amount", sa.Numeric(precision=18, scale=2), nullable=True),
    )
    op.alter_column(
        "sales",
        "number",
        existing_type=sa.VARCHAR(length=20),
        type_=sa.String(length=64),
        nullable=True,
    )
    op.create_foreign_key(
        op.f("fk_sales_credit_override_by_users"), "sales", "users", ["credit_override_by"], ["id"]
    )

    # --- Reprise des données (propriétaire des tables ; FORCE levé dans cette transaction) ----
    for table in BACKFILLED:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    for kind, label, order in DEFAULT_METHODS:
        op.execute(
            sa.text(
                "INSERT INTO payment_methods (id, tenant_id, label, kind, sort_order) "
                "SELECT gen_random_uuid(), t.id, :label, :kind, :order FROM tenants t "
                "ON CONFLICT DO NOTHING"
            ).bindparams(label=label, kind=kind, order=order)
        )
        op.execute(
            sa.text(
                "UPDATE payments p SET payment_method_id = pm.id, "
                "method_label = COALESCE(NULLIF(btrim(p.provider), ''), :label) "
                "FROM payment_methods pm "
                "WHERE pm.tenant_id = p.tenant_id AND pm.kind = :kind AND pm.label = :label "
                "AND p.method = :kind"
            ).bindparams(label=label, kind=kind)
        )
    op.execute(
        "UPDATE sales s SET is_credit = true "
        "WHERE s.validated_at IS NOT NULL AND s.total > COALESCE((SELECT sum(p.amount) "
        "FROM payments p WHERE p.sale_id = s.id AND p.paid_at <= s.validated_at), 0)"
    )
    op.execute(
        "INSERT INTO cash_site_settings (tenant_id, site_id, enabled, updated_at) "
        "SELECT DISTINCT tenant_id, site_id, true, now() FROM cash_registers"
    )
    for table in BACKFILLED:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.alter_column("payments", "method_label", existing_type=sa.String(length=60), nullable=False)

    op.create_check_constraint(
        "validated_has_number", "sales", "validated_at IS NULL OR number IS NOT NULL"
    )
    op.create_check_constraint(
        "credit_override_complete",
        "sales",
        "(credit_override_by IS NULL) = (credit_override_at IS NULL) "
        "AND (credit_override_by IS NULL) = (credit_override_reason IS NULL) "
        "AND (credit_override_by IS NULL) = (credit_override_amount IS NULL)",
    )
    op.create_check_constraint(
        "cash_change_consistent",
        "payments",
        "(amount_received IS NULL AND change_given IS NULL) OR (method = 'CASH' "
        "AND amount_received >= amount AND change_given = amount_received - amount)",
    )

    role = _app_role()
    for table, (privileges, columns) in GRANTS.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {role} "
            "USING (tenant_id = app_current_tenant_id()) "
            "WITH CHECK (tenant_id = app_current_tenant_id())"
        )
        op.execute(f"GRANT {privileges} ON {table} TO {role}")
        op.execute(f"GRANT UPDATE ({columns}) ON {table} TO {role}")

    # Paiement immuable (montant, moyen, instantané, monnaie) : seule son annulation s'écrit.
    op.execute(f"REVOKE UPDATE ON payments FROM {role}")
    op.execute(f"GRANT UPDATE ({PAYMENT_UPDATABLE}) ON payments TO {role}")
    # Numéro d'une vente validée : jamais modifié (défense en profondeur, en base).
    op.execute(
        """
        CREATE FUNCTION sales_number_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.validated_at IS NOT NULL AND NEW.number IS DISTINCT FROM OLD.number THEN
                RAISE EXCEPTION 'Le numéro d''une vente validée est définitif'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER sales_number_immutable BEFORE UPDATE OF number ON sales "
        "FOR EACH ROW EXECUTE FUNCTION sales_number_immutable()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    too_long = bind.execute(
        sa.text("SELECT count(*) FROM sales WHERE length(number) > 20")
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"Retour arrière impossible : {too_long} numéro(s) de vente de plus de 20 caractères "
            "(VENT-…) ne tiennent pas dans l'ancien schéma."
        )
    role = _app_role()
    op.execute("DROP TRIGGER IF EXISTS sales_number_immutable ON sales")
    op.execute("DROP FUNCTION IF EXISTS sales_number_immutable()")
    op.execute(f"GRANT UPDATE ON payments TO {role}")
    for table in GRANTS:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for name, table in (
        ("cash_change_consistent", "payments"),
        ("credit_override_complete", "sales"),
        ("validated_has_number", "sales"),
    ):
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    # Brouillons sans numéro : numéro de brouillon provisoire (l'ancien schéma l'exige).
    op.execute("ALTER TABLE sales NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE sales SET number = 'BRO-' || left(replace(id::text, '-', ''), 12) "
        "WHERE number IS NULL"
    )
    op.execute("ALTER TABLE sales FORCE ROW LEVEL SECURITY")
    op.drop_constraint(op.f("fk_sales_credit_override_by_users"), "sales", type_="foreignkey")
    op.alter_column(
        "sales",
        "number",
        existing_type=sa.String(length=64),
        type_=sa.VARCHAR(length=20),
        nullable=False,
    )
    op.drop_column("sales", "credit_override_amount")
    op.drop_column("sales", "credit_override_reason")
    op.drop_column("sales", "credit_override_at")
    op.drop_column("sales", "credit_override_by")
    op.drop_column("sales", "is_credit")
    op.drop_constraint(
        op.f("fk_payments_tenant_id_payment_method_id_payment_methods"),
        "payments",
        type_="foreignkey",
    )
    op.drop_column("payments", "change_given")
    op.drop_column("payments", "amount_received")
    op.drop_column("payments", "method_label")
    op.drop_column("payments", "payment_method_id")
    op.drop_index(op.f("ix_payment_method_sites_tenant_id"), table_name="payment_method_sites")
    op.drop_table("payment_method_sites")
    op.drop_index(op.f("ix_cash_site_settings_tenant_id"), table_name="cash_site_settings")
    op.drop_table("cash_site_settings")
    op.drop_index(op.f("ix_payment_methods_tenant_id"), table_name="payment_methods")
    op.drop_table("payment_methods")
