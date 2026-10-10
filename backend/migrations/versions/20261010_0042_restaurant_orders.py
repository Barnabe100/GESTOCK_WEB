"""Palier R2-B — commandes de restauration (ADR-0049 D14, ``RESTAURANT.md`` §4).

- ``restaurant_site_settings`` : réglages des commandes d'UN site (une ligne par site).
- ``restaurant_orders`` : commande, numéro court unique par site et par jour de l'entreprise,
  clé d'idempotence unique par site, mode de paiement recopié, motifs d'annulation obligatoires.
- ``restaurant_order_lines`` : instantané figé (libellé, conversion, prix) d'une présentation du
  menu du MÊME site (FK composite vers ``restaurant_menu_items`` : ``UNIQUE (tenant_id,
  site_id, id)`` ajouté, seule retouche du schéma R1) ; seul l'état évolue.
- ``restaurant_order_events`` : historique en ajout seul (``SELECT`` + ``INSERT``).
- Déclencheur ``trg_restaurant_final_state`` : ligne servie ou annulée, commande close, annulée
  ou refusée immuables ; instantané d'une ligne jamais modifié — même par une écriture SQL
  directe.
- ``business_profiles.module_settings`` : réglages par défaut des modules (données du
  catalogue, resynchronisées par ``catalog sync``).

RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : ``SELECT, INSERT`` + ``UPDATE`` des seules
colonnes d'état ; jamais de suppression ; aucun droit pour le rôle de la console.

Données (D10) : les activations de ``restaurant.orders`` héritées de la période « Bientôt
disponible » sont remises à ``false`` sur les sites EXISTANTS (le module reste planifié en
production jusqu'au commit R2-E). La descente ne les restaure pas et REFUSE de perdre des
données saisies : une commande ou une ligne de réglages (y compris des réglages conservés
après une désactivation) bloque le retour arrière (Q7, N5).

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "restaurant_site_settings",
    "restaurant_orders",
    "restaurant_order_lines",
    "restaurant_order_events",
)
# Tables dont une ligne est une donnée saisie : leur présence bloque la descente.
GUARDED = ("restaurant_orders", "restaurant_site_settings")
MODULE = "restaurant.orders"

FINAL_STATE_FUNCTION = """
CREATE OR REPLACE FUNCTION restaurant_final_state() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'restaurant_order_lines' THEN
        IF OLD.status IN ('SERVED', 'CANCELLED') AND NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'restaurant_final_state: ligne % définitive', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.order_id, NEW.site_id, NEW.line_no, NEW.menu_item_id, NEW.article_id,
            NEW.packaging_id, NEW.label, NEW.packaging_name, NEW.unit, NEW.conversion,
            NEW.unit_price, NEW.quantity, NEW.base_quantity, NEW.line_total, NEW.note)
           IS DISTINCT FROM
           (OLD.order_id, OLD.site_id, OLD.line_no, OLD.menu_item_id, OLD.article_id,
            OLD.packaging_id, OLD.label, OLD.packaging_name, OLD.unit, OLD.conversion,
            OLD.unit_price, OLD.quantity, OLD.base_quantity, OLD.line_total, OLD.note) THEN
            RAISE EXCEPTION 'restaurant_final_state: instantané de la ligne % figé', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF OLD.status IN ('CLOSED', 'CANCELLED', 'REJECTED') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'restaurant_final_state: commande % définitive', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$
"""


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _enable_rls_and_grants() -> None:
    role = _app_role()
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {role} "
            "USING (tenant_id = app_current_tenant_id()) "
            "WITH CHECK (tenant_id = app_current_tenant_id())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {role}")
    op.execute(
        "GRANT UPDATE (payment_timing, claim_protection_minutes, claim_cooldown_minutes, "
        f"qr_auto_accept, updated_at) ON restaurant_site_settings TO {role}"
    )
    op.execute(
        "GRANT UPDATE (status, prep_status, settlement_status, assigned_user_id, assigned_at, "
        "assigned_by, sale_id, confirmed_at, confirmed_by, closed_at, cancelled_at, "
        f"cancelled_by, cancel_reason, version, updated_at) ON restaurant_orders TO {role}"
    )
    op.execute(
        "GRANT UPDATE (status, prepared_at, prepared_by, ready_at, served_at, served_by, "
        f"cancelled_at, cancelled_by, cancel_reason) ON restaurant_order_lines TO {role}"
    )


def _revoke() -> None:
    role = _app_role()
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")


def upgrade() -> None:
    # Cible de la FK composite des lignes (élément du menu du MÊME site) : créée en premier.
    op.create_unique_constraint(
        op.f("uq_restaurant_menu_items_tenant_id_site_id_id"),
        "restaurant_menu_items",
        ["tenant_id", "site_id", "id"],
    )
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "restaurant_orders",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("daily_number", sa.Integer(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "STAFF",
                "POS",
                "QR",
                name="restaurant_order_channel",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "service_mode",
            sa.Enum(
                "ON_SITE",
                "COUNTER",
                "TAKEAWAY",
                name="restaurant_service_mode",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("table_id", sa.Uuid(), nullable=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("call_name", sa.String(length=40), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING_CONFIRMATION",
                "OPEN",
                "CLOSED",
                "CANCELLED",
                "REJECTED",
                name="restaurant_order_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "prep_status",
            sa.Enum(
                "RECEIVED",
                "IN_PREPARATION",
                "READY",
                "SERVED",
                "NONE",
                name="restaurant_prep_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "settlement_status",
            sa.Enum(
                "UNSETTLED",
                "SETTLED",
                name="restaurant_settlement_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "payment_timing",
            sa.Enum(
                "AT_END",
                "AT_ORDER",
                name="restaurant_order_payment_timing",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("sale_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
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
            "(status NOT IN ('CANCELLED', 'REJECTED')) OR (cancelled_at IS NOT NULL "
            "AND cancelled_by IS NOT NULL AND cancel_reason IS NOT NULL)",
            name=op.f("ck_restaurant_orders_cancelled_has_reason"),
        ),
        sa.CheckConstraint(
            "call_name IS NULL OR (btrim(call_name) = call_name AND call_name <> '')",
            name=op.f("ck_restaurant_orders_call_name_trimmed"),
        ),
        sa.CheckConstraint(
            "cancel_reason IS NULL OR btrim(cancel_reason) <> ''",
            name=op.f("ck_restaurant_orders_cancel_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "settlement_status <> 'SETTLED' OR sale_id IS NOT NULL",
            name=op.f("ck_restaurant_orders_settled_has_sale"),
        ),
        sa.CheckConstraint(
            "status <> 'CLOSED' OR (settlement_status = 'SETTLED' AND closed_at IS NOT NULL)",
            name=op.f("ck_restaurant_orders_closed_is_settled"),
        ),
        sa.CheckConstraint(
            "(assigned_user_id IS NULL) = (assigned_at IS NULL)",
            name=op.f("ck_restaurant_orders_assignment_complete"),
        ),
        sa.CheckConstraint(
            "daily_number > 0", name=op.f("ck_restaurant_orders_daily_number_positive")
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_restaurant_orders_version_positive")),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], name=op.f("fk_restaurant_orders_assigned_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["assigned_user_id"],
            ["users.id"],
            name=op.f("fk_restaurant_orders_assigned_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"], ["users.id"], name=op.f("fk_restaurant_orders_cancelled_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name=op.f("fk_restaurant_orders_confirmed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_restaurant_orders_created_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            name=op.f("fk_restaurant_orders_tenant_id_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_orders_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_orders_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_orders")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_orders_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "business_date",
            "daily_number",
            name="uq_restaurant_orders_site_day_number",
        ),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "id", name=op.f("uq_restaurant_orders_tenant_id_site_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "idempotency_key", name="uq_restaurant_orders_site_idempotency"
        ),
    )
    op.create_index(
        "ix_restaurant_orders_backlog",
        "restaurant_orders",
        ["tenant_id", "site_id", "settlement_status", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_orders_site_id"), "restaurant_orders", ["site_id"], unique=False
    )
    op.create_index(
        op.f("ix_restaurant_orders_tenant_id"), "restaurant_orders", ["tenant_id"], unique=False
    )
    op.create_table(
        "restaurant_site_settings",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column(
            "payment_timing",
            sa.Enum(
                "AT_END",
                "AT_ORDER",
                name="restaurant_payment_timing",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("claim_protection_minutes", sa.Integer(), nullable=False),
        sa.Column("claim_cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("qr_auto_accept", sa.Boolean(), nullable=False),
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
            "claim_cooldown_minutes BETWEEN 0 AND 1440",
            name=op.f("ck_restaurant_site_settings_claim_cooldown_range"),
        ),
        sa.CheckConstraint(
            "claim_protection_minutes BETWEEN 0 AND 1440",
            name=op.f("ck_restaurant_site_settings_claim_protection_range"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_site_settings_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_site_settings_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_site_settings")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_site_settings_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "site_id", name=op.f("uq_restaurant_site_settings_tenant_id_site_id")
        ),
    )
    op.create_index(
        op.f("ix_restaurant_site_settings_tenant_id"),
        "restaurant_site_settings",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "restaurant_order_events",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "CREATED",
                "CONFIRMED",
                "REJECTED",
                "CLAIMED",
                "REASSIGNED",
                "LINES_ADDED",
                "PREP_STARTED",
                "READY",
                "READY_REVERTED",
                "SERVED",
                "LINE_CANCELLED",
                "SETTLED",
                "SALE_CANCELLED",
                "CLOSED",
                "CANCELLED",
                name="restaurant_order_event_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_kind",
            sa.Enum(
                "STAFF",
                "PUBLIC",
                "SYSTEM",
                name="restaurant_actor_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("line_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(actor_kind = 'STAFF') = (actor_user_id IS NOT NULL)",
            name=op.f("ck_restaurant_order_events_staff_actor_known"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_restaurant_order_events_actor_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "order_id"],
            ["restaurant_orders.tenant_id", "restaurant_orders.site_id", "restaurant_orders.id"],
            name=op.f("fk_restaurant_order_events_tenant_id_site_id_order_id_restaurant_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_order_events_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_order_events")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_order_events_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "idempotency_key",
            name="uq_restaurant_order_events_idempotency",
        ),
    )
    op.create_index(
        "ix_restaurant_order_events_actor",
        "restaurant_order_events",
        [
            "tenant_id",
            "site_id",
            "actor_user_id",
            "event_type",
            sa.literal_column("occurred_at DESC"),
        ],
        unique=False,
    )
    op.create_index(
        "ix_restaurant_order_events_order",
        "restaurant_order_events",
        ["tenant_id", "order_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_order_events_tenant_id"),
        "restaurant_order_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "restaurant_order_lines",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("menu_item_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("packaging_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("packaging_name", sa.String(length=100), nullable=True),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("conversion", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("unit_price", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("base_quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("line_total", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "IN_PREPARATION",
                "READY",
                "SERVED",
                "CANCELLED",
                name="restaurant_line_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prepared_by", sa.Uuid(), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("served_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("served_by", sa.Uuid(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(status NOT IN ('CANCELLED')) OR (cancelled_at IS NOT NULL "
            "AND cancelled_by IS NOT NULL AND cancel_reason IS NOT NULL)",
            name=op.f("ck_restaurant_order_lines_cancelled_has_reason"),
        ),
        sa.CheckConstraint(
            "cancel_reason IS NULL OR btrim(cancel_reason) <> ''",
            name=op.f("ck_restaurant_order_lines_cancel_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "status <> 'SERVED' OR (served_at IS NOT NULL AND served_by IS NOT NULL)",
            name=op.f("ck_restaurant_order_lines_served_complete"),
        ),
        sa.CheckConstraint(
            "(packaging_id IS NULL) = (conversion IS NULL)",
            name=op.f("ck_restaurant_order_lines_packaging_snapshot"),
        ),
        sa.CheckConstraint("line_no > 0", name=op.f("ck_restaurant_order_lines_line_no_positive")),
        sa.CheckConstraint(
            "quantity > 0 AND base_quantity > 0",
            name=op.f("ck_restaurant_order_lines_quantities_positive"),
        ),
        sa.CheckConstraint(
            "unit_price >= 0 AND line_total >= 0",
            name=op.f("ck_restaurant_order_lines_amounts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["users.id"],
            name=op.f("fk_restaurant_order_lines_cancelled_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["prepared_by"], ["users.id"], name=op.f("fk_restaurant_order_lines_prepared_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["served_by"], ["users.id"], name=op.f("fk_restaurant_order_lines_served_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "packaging_id"],
            [
                "catalog_packagings.tenant_id",
                "catalog_packagings.article_id",
                "catalog_packagings.id",
            ],
            name=op.f(
                "fk_restaurant_order_lines_tenant_id_article_id_packaging_id_catalog_packagings"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_restaurant_order_lines_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "menu_item_id"],
            [
                "restaurant_menu_items.tenant_id",
                "restaurant_menu_items.site_id",
                "restaurant_menu_items.id",
            ],
            name=op.f(
                "fk_restaurant_order_lines_tenant_id_site_id_menu_item_id_restaurant_menu_items"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "order_id"],
            ["restaurant_orders.tenant_id", "restaurant_orders.site_id", "restaurant_orders.id"],
            name=op.f("fk_restaurant_order_lines_tenant_id_site_id_order_id_restaurant_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_order_lines_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_order_lines")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_order_lines_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id",
            "order_id",
            "line_no",
            name=op.f("uq_restaurant_order_lines_tenant_id_order_id_line_no"),
        ),
    )
    op.create_index(
        "ix_restaurant_order_lines_article",
        "restaurant_order_lines",
        ["tenant_id", "site_id", "article_id"],
        unique=False,
    )
    op.create_index(
        "ix_restaurant_order_lines_order",
        "restaurant_order_lines",
        ["tenant_id", "order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_order_lines_tenant_id"),
        "restaurant_order_lines",
        ["tenant_id"],
        unique=False,
    )
    op.add_column(
        "business_profiles",
        sa.Column(
            "module_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    # ### end Alembic commands ###
    op.execute(FINAL_STATE_FUNCTION)
    for table in ("restaurant_orders", "restaurant_order_lines"):
        op.execute(
            f"CREATE TRIGGER trg_restaurant_final_state BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION restaurant_final_state()"
        )
    _enable_rls_and_grants()
    # D10 : sites existants désactivés jusqu'à une activation explicite (inertes jusqu'ici).
    op.execute(f"UPDATE site_modules SET enabled = false WHERE module_code = '{MODULE}'")


def downgrade() -> None:
    # Jamais de perte silencieuse (Q7, N5) : une commande ou des réglages saisis (même conservés
    # après une désactivation) bloquent le retour arrière. Comptage hors RLS le temps du
    # contrôle ; aucune suppression n'a lieu avant ce contrôle.
    bind = op.get_bind()
    counts: dict[str, int] = {}
    for table in GUARDED:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        counts[table] = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if any(counts.values()):
        raise RuntimeError(
            f"Retour arrière refusé : {counts['restaurant_orders']} commande(s) et "
            f"{counts['restaurant_site_settings']} réglage(s) de site saisis seraient perdus."
        )
    _revoke()
    for table in ("restaurant_orders", "restaurant_order_lines"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_restaurant_final_state ON {table}")
    op.execute("DROP FUNCTION IF EXISTS restaurant_final_state()")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("business_profiles", "module_settings")
    op.drop_index(op.f("ix_restaurant_order_lines_tenant_id"), table_name="restaurant_order_lines")
    op.drop_index("ix_restaurant_order_lines_order", table_name="restaurant_order_lines")
    op.drop_index("ix_restaurant_order_lines_article", table_name="restaurant_order_lines")
    op.drop_table("restaurant_order_lines")
    op.drop_index(
        op.f("ix_restaurant_order_events_tenant_id"), table_name="restaurant_order_events"
    )
    op.drop_index("ix_restaurant_order_events_order", table_name="restaurant_order_events")
    op.drop_index("ix_restaurant_order_events_actor", table_name="restaurant_order_events")
    op.drop_table("restaurant_order_events")
    op.drop_index(
        op.f("ix_restaurant_site_settings_tenant_id"), table_name="restaurant_site_settings"
    )
    op.drop_table("restaurant_site_settings")
    op.drop_index(op.f("ix_restaurant_orders_tenant_id"), table_name="restaurant_orders")
    op.drop_index(op.f("ix_restaurant_orders_site_id"), table_name="restaurant_orders")
    op.drop_index("ix_restaurant_orders_backlog", table_name="restaurant_orders")
    op.drop_table("restaurant_orders")
    # ### end Alembic commands ###
    # Dernière : la FK des lignes (supprimées ci-dessus) en dépendait.
    op.drop_constraint(
        op.f("uq_restaurant_menu_items_tenant_id_site_id_id"),
        "restaurant_menu_items",
        type_="unique",
    )
