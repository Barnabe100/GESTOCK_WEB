"""Palier R7-C / R7-E — commandes par QR Code : réglages, commandes reçues, suivi, téléphone
(ADR-0052 ; ``RESTAURANT.md`` §10).

- ``restaurant_qr_site_settings`` : réglages QR d'UN site (plafonds par commande, par ligne et
  par point ; nom et téléphone demandés ou non) bornés par les plafonds de la PLATEFORME
  (contraintes ``CHECK`` : 100 lignes, 99 unités, 20 commandes en attente).
- ``restaurant_qr_orders`` : commande créée par un point QR (FK composites, même site) ; clé du
  client unique PAR POINT, empreinte de la requête, empreinte du secret de suivi (unique, jamais
  le secret). Politique ``qr_tracking_lookup`` : lecture de la SEULE ligne dont l'empreinte est
  ``app.qr_tracking_hash`` (suivi public).
- ``restaurant_orders.contact_phone`` (facultatif) ; droit ``UPDATE (contact_phone)`` du rôle
  applicatif.
- Rôle de purge (``stockmanager_purge``, déjà requis par 0045) : ``SELECT`` des seules colonnes
  utiles et ``UPDATE (contact_phone)`` des commandes FINIES depuis plus de 30 jours qui portent un
  téléphone (politiques restrictives à ce rôle ; ``WITH CHECK`` : effacement seulement). Durée
  figée ici, comme 0045.
- Déclencheur des états finaux : une commande finie reste immuable, SAUF l'effacement de son
  téléphone (aucune autre colonne ne peut changer en même temps).

RLS ``ENABLE`` + ``FORCE`` ; aucune suppression ; aucun droit pour la console. Descente refusée
dès qu'un réglage, une commande QR ou un téléphone existe.

Revision ID: 0057
Revises: 0056
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("restaurant_qr_site_settings", "restaurant_qr_orders")
# Conservation du téléphone d'un client (décision de l'utilisateur, R7) — figée ici.
CONTACT_RETENTION_DAYS = 30
# Commandes FINIES depuis plus de 30 jours. Lecture : ces seules commandes (la ligne mise à jour
# doit rester visible au rôle, téléphone effacé compris) ; écriture : celles qui portent encore
# un téléphone, et seulement pour l'effacer.
ENDED_LONG_AGO = (
    "status IN ('CLOSED', 'CANCELLED', 'REJECTED') "
    "AND coalesce(closed_at, cancelled_at) < now() - make_interval(days => "
    f"{CONTACT_RETENTION_DAYS})"
)
CONTACT_EXPIRED = f"contact_phone IS NOT NULL AND {ENDED_LONG_AGO}"

_LINES_BRANCH = """
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
        END IF;"""

# Version de 0042 (restaurée à la descente).
FINAL_STATE_V1 = (
    """
CREATE OR REPLACE FUNCTION restaurant_final_state() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN"""
    + _LINES_BRANCH
    + """
    ELSIF OLD.status IN ('CLOSED', 'CANCELLED', 'REJECTED') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'restaurant_final_state: commande % définitive', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$
"""
)

# R7 : seul l'effacement du téléphone (et l'horodatage de mise à jour) est permis sur une
# commande finie ; toute autre différence reste refusée.
FINAL_STATE_WITH_ERASURE = (
    """
CREATE OR REPLACE FUNCTION restaurant_final_state() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    erased restaurant_orders%ROWTYPE;
BEGIN"""
    + _LINES_BRANCH
    + """
    ELSIF OLD.status IN ('CLOSED', 'CANCELLED', 'REJECTED') AND NEW IS DISTINCT FROM OLD THEN
        IF TG_TABLE_NAME = 'restaurant_orders' AND OLD.contact_phone IS NOT NULL
           AND NEW.contact_phone IS NULL THEN
            erased := OLD;
            erased.contact_phone := NULL;
            erased.updated_at := NEW.updated_at;
            IF NEW IS NOT DISTINCT FROM erased THEN
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION 'restaurant_final_state: commande % définitive', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$
"""
)


def _quote(role: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote(role)


def _roles() -> tuple[str, str]:
    settings = get_settings()
    return _quote(settings.db_app_role), _quote(settings.db_purge_role)


def _count_without_rls(table: str, where: str = "") -> int:
    """Comptage hors RLS (rôle propriétaire, RLS forcée) le temps d'un contrôle."""
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table} {where}")).scalar_one()
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    return int(count)


def upgrade() -> None:
    op.create_table(
        "restaurant_qr_site_settings",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("max_lines", sa.Integer(), nullable=False),
        sa.Column("max_quantity_per_line", sa.Integer(), nullable=False),
        sa.Column("max_pending_per_point", sa.Integer(), nullable=False),
        sa.Column(
            "customer_name_mode",
            sa.Enum(
                "HIDDEN",
                "OPTIONAL",
                "REQUIRED",
                name="restaurant_qr_name_mode",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "customer_phone_mode",
            sa.Enum(
                "HIDDEN",
                "OPTIONAL",
                "REQUIRED",
                name="restaurant_qr_phone_mode",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
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
            "max_lines BETWEEN 1 AND 100",
            name=op.f("ck_restaurant_qr_site_settings_max_lines_range"),
        ),
        sa.CheckConstraint(
            "max_pending_per_point BETWEEN 1 AND 20",
            name=op.f("ck_restaurant_qr_site_settings_max_pending_range"),
        ),
        sa.CheckConstraint(
            "max_quantity_per_line BETWEEN 1 AND 99",
            name=op.f("ck_restaurant_qr_site_settings_max_quantity_range"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_qr_site_settings_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_qr_site_settings_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_restaurant_qr_site_settings_updated_by_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_qr_site_settings")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_qr_site_settings_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id", "site_id", name=op.f("uq_restaurant_qr_site_settings_tenant_id_site_id")
        ),
    )
    op.create_index(
        op.f("ix_restaurant_qr_site_settings_tenant_id"),
        "restaurant_qr_site_settings",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "restaurant_qr_orders",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("point_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("client_key", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("tracking_hash", sa.String(length=64), nullable=False),
        sa.Column("tracking_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_restaurant_qr_orders_request_hash_format"),
        ),
        sa.CheckConstraint(
            "tracking_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_restaurant_qr_orders_tracking_hash_format"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "order_id"],
            ["restaurant_orders.tenant_id", "restaurant_orders.site_id", "restaurant_orders.id"],
            name=op.f("fk_restaurant_qr_orders_tenant_id_site_id_order_id_restaurant_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "point_id"],
            [
                "restaurant_qr_points.tenant_id",
                "restaurant_qr_points.site_id",
                "restaurant_qr_points.id",
            ],
            name=op.f("fk_restaurant_qr_orders_tenant_id_site_id_point_id_restaurant_qr_points"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_qr_orders_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_qr_orders")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_qr_orders_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "order_id", name=op.f("uq_restaurant_qr_orders_tenant_id_order_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "point_id",
            "client_key",
            name="uq_restaurant_qr_orders_tenant_id_point_id_client_key",
        ),
    )
    op.create_index(
        "ix_restaurant_qr_orders_point",
        "restaurant_qr_orders",
        ["tenant_id", "point_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_restaurant_qr_orders_tenant_id"),
        "restaurant_qr_orders",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "uq_restaurant_qr_orders_tracking_hash",
        "restaurant_qr_orders",
        ["tracking_hash"],
        unique=True,
    )
    op.add_column(
        "restaurant_orders", sa.Column("contact_phone", sa.String(length=30), nullable=True)
    )

    app_role, purge_role = _roles()
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} TO {app_role} "
            "USING (tenant_id = app_current_tenant_id()) "
            "WITH CHECK (tenant_id = app_current_tenant_id())"
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {app_role}")
    op.execute(
        "GRANT UPDATE (max_lines, max_quantity_per_line, max_pending_per_point, "
        "customer_name_mode, customer_phone_mode, updated_by, updated_at) "
        f"ON restaurant_qr_site_settings TO {app_role}"
    )
    # Lien de suivi : seule l'empreinte du secret se renouvelle (soumission rejouée, R7-E).
    op.execute(
        f"GRANT UPDATE (tracking_hash, tracking_issued_at) ON restaurant_qr_orders TO {app_role}"
    )
    # Suivi public (R7-E) : lecture de la SEULE ligne dont l'empreinte est celle du secret
    # présenté (``app.qr_tracking_hash`` ; vide : aucune ligne).
    op.execute(
        f"CREATE POLICY qr_tracking_lookup ON restaurant_qr_orders FOR SELECT TO {app_role} "
        "USING (tracking_hash = NULLIF(current_setting('app.qr_tracking_hash', true), ''))"
    )
    op.execute(f"GRANT UPDATE (contact_phone) ON restaurant_orders TO {app_role}")
    # Effacement des téléphones (rôle de purge) : il ne voit que les commandes FINIES depuis plus
    # de 30 jours (colonnes utiles seulement) et ne peut qu'effacer un téléphone encore présent.
    op.execute(
        f"GRANT SELECT (id, status, closed_at, cancelled_at, contact_phone), "
        f"UPDATE (contact_phone) ON restaurant_orders TO {purge_role}"
    )
    op.execute(
        f"CREATE POLICY contact_purge_read ON restaurant_orders FOR SELECT TO {purge_role} "
        f"USING ({ENDED_LONG_AGO})"
    )
    op.execute(
        f"CREATE POLICY contact_purge_update ON restaurant_orders FOR UPDATE TO {purge_role} "
        f"USING ({CONTACT_EXPIRED}) WITH CHECK (contact_phone IS NULL)"
    )
    op.execute(FINAL_STATE_WITH_ERASURE)


def downgrade() -> None:
    # Jamais de perte silencieuse : réglages QR, commandes reçues par QR ou téléphones saisis
    # bloquent le retour arrière.
    saved = sum(_count_without_rls(table) for table in TABLES)
    phones = _count_without_rls("restaurant_orders", "WHERE contact_phone IS NOT NULL")
    if saved or phones:
        raise RuntimeError(
            f"Retour arrière refusé : {saved} réglage(s) ou commande(s) QR et {phones} "
            "téléphone(s) de client seraient perdus."
        )
    app_role, purge_role = _roles()
    op.execute(FINAL_STATE_V1)
    op.execute("DROP POLICY IF EXISTS contact_purge_update ON restaurant_orders")
    op.execute("DROP POLICY IF EXISTS contact_purge_read ON restaurant_orders")
    op.execute(f"REVOKE ALL ON restaurant_orders FROM {purge_role}")
    op.execute(f"REVOKE UPDATE (contact_phone) ON restaurant_orders FROM {app_role}")
    op.execute("DROP POLICY IF EXISTS qr_tracking_lookup ON restaurant_qr_orders")
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {app_role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.drop_column("restaurant_orders", "contact_phone")
    op.drop_index("uq_restaurant_qr_orders_tracking_hash", table_name="restaurant_qr_orders")
    op.drop_index(op.f("ix_restaurant_qr_orders_tenant_id"), table_name="restaurant_qr_orders")
    op.drop_index("ix_restaurant_qr_orders_point", table_name="restaurant_qr_orders")
    op.drop_table("restaurant_qr_orders")
    op.drop_index(
        op.f("ix_restaurant_qr_site_settings_tenant_id"), table_name="restaurant_qr_site_settings"
    )
    op.drop_table("restaurant_qr_site_settings")
