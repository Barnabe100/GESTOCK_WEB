"""Palier R5-A — zones et tables des sites de restauration (ADR-0049, ``RESTAURANT.md`` §4).

- ``restaurant_zones`` : zone d'UN site, nom unique par site insensible à la casse, jamais
  supprimée (désactivation).
- ``restaurant_tables`` : table d'UN site, libellé unique par site insensible à la casse (toutes
  zones confondues), zone FACULTATIVE du MÊME site (FK composite, MATCH SIMPLE), places
  facultatives (1 à 999), jamais supprimée.
- ``restaurant_orders`` : clé étrangère composite ``(tenant_id, site_id, table_id)`` vers les
  tables (``RESTRICT`` : une table d'un autre site ou d'une autre entreprise est impossible en
  base), contrainte ``table_id IS NULL OR service_mode = 'ON_SITE'`` (A3, B5), index partiel
  des commandes portant une table, droit ``UPDATE (table_id)`` du rôle applicatif (changement
  de table, palier R5-B ; le déclencheur des états finaux protège toujours les commandes
  closes) ; type d'évènement ``TABLE_CHANGED`` (historique en ajout seul).

RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : ``SELECT, INSERT`` + ``UPDATE`` des seules
colonnes modifiables (jamais de suppression) ; aucun droit pour les rôles de la console et de
la purge.

Données : la migration échoue si une commande portait déjà un ``table_id`` (aucune route ne
l'écrivait avant R5). ``restaurant.tables`` est remis à ``false`` sur les sites EXISTANTS
(A11 ; activations héritées de la période « Bientôt disponible », inertes). Les activations
créées ensuite pour les nouveaux sites suivent la politique E.1 d'ADR-0048 (conservées). La
descente ne restaure pas les activations et refuse de perdre une zone, une table ou un
changement de table.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("restaurant_zones", "restaurant_tables")
MODULE = "restaurant.tables"
ORDER_TABLE_FK = "fk_restaurant_orders_tenant_id_site_id_table_id_restaurant_tables"
ORDER_TABLE_CHECK = "ck_restaurant_orders_table_only_on_site"
EVENT_CHECK = "ck_restaurant_order_events_restaurant_order_event_type"
EVENT_TYPES = (
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
    "CUSTOMER_SET",
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _count_without_rls(table: str, where: str = "") -> int:
    """Comptage hors RLS (rôle propriétaire, RLS forcée) le temps d'un contrôle."""
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table} {where}")).scalar_one()
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    return int(count)


def _event_check(values: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE restaurant_order_events DROP CONSTRAINT {EVENT_CHECK}")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"ALTER TABLE restaurant_order_events ADD CONSTRAINT {EVENT_CHECK} "
        f"CHECK (event_type IN ({allowed}))"
    )


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
        f"GRANT UPDATE (name, sort_order, is_active, updated_at) ON restaurant_zones TO {role}"
    )
    op.execute(
        "GRANT UPDATE (zone_id, label, seats, sort_order, is_active, updated_at) "
        f"ON restaurant_tables TO {role}"
    )
    op.execute(f"GRANT UPDATE (table_id) ON restaurant_orders TO {role}")


def upgrade() -> None:
    attached = _count_without_rls("restaurant_orders", "WHERE table_id IS NOT NULL")
    if attached:
        raise RuntimeError(
            f"Migration refusée : {attached} commande(s) portent déjà une table, alors qu'aucune "
            "table n'existe encore."
        )
    op.create_table(
        "restaurant_zones",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "btrim(name) = name AND name <> ''", name=op.f("ck_restaurant_zones_name_trimmed")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_zones_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_zones_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_zones")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_zones_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "id", name=op.f("uq_restaurant_zones_tenant_id_site_id_id")
        ),
    )
    op.create_index(
        op.f("ix_restaurant_zones_site_id"), "restaurant_zones", ["site_id"], unique=False
    )
    op.create_index(
        op.f("ix_restaurant_zones_tenant_id"), "restaurant_zones", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_restaurant_zones_site_name",
        "restaurant_zones",
        ["tenant_id", "site_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "restaurant_tables",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("zone_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.String(length=40), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "btrim(label) = label AND label <> ''", name=op.f("ck_restaurant_tables_label_trimmed")
        ),
        sa.CheckConstraint(
            "seats IS NULL OR seats BETWEEN 1 AND 999",
            name=op.f("ck_restaurant_tables_seats_range"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "zone_id"],
            ["restaurant_zones.tenant_id", "restaurant_zones.site_id", "restaurant_zones.id"],
            name=op.f("fk_restaurant_tables_tenant_id_site_id_zone_id_restaurant_zones"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_tables_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_tables_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_tables")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_restaurant_tables_tenant_id_id")),
        sa.UniqueConstraint(
            "tenant_id", "site_id", "id", name=op.f("uq_restaurant_tables_tenant_id_site_id_id")
        ),
    )
    op.create_index(
        op.f("ix_restaurant_tables_site_id"), "restaurant_tables", ["site_id"], unique=False
    )
    op.create_index(
        op.f("ix_restaurant_tables_tenant_id"), "restaurant_tables", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_restaurant_tables_zone", "restaurant_tables", ["tenant_id", "zone_id"], unique=False
    )
    op.create_index(
        "uq_restaurant_tables_site_label",
        "restaurant_tables",
        ["tenant_id", "site_id", sa.literal_column("lower(label)")],
        unique=True,
    )
    # Commandes : table du même tenant ET du même site, seulement « sur place ».
    op.create_index(
        "ix_restaurant_orders_table",
        "restaurant_orders",
        ["tenant_id", "site_id", "table_id"],
        unique=False,
        postgresql_where=sa.text("table_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f(ORDER_TABLE_FK),
        "restaurant_orders",
        "restaurant_tables",
        ["tenant_id", "site_id", "table_id"],
        ["tenant_id", "site_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f(ORDER_TABLE_CHECK),
        "restaurant_orders",
        "table_id IS NULL OR service_mode = 'ON_SITE'",
    )
    _event_check((*EVENT_TYPES, "TABLE_CHANGED"))

    _enable_rls_and_grants()
    # A11 : sites existants — tables désactivées jusqu'à une activation explicite (rôle
    # propriétaire ; la politique de ``site_modules`` ne vise que le rôle applicatif).
    op.execute(
        sa.text("UPDATE site_modules SET enabled = false WHERE module_code = :code").bindparams(
            code=MODULE
        )
    )


def downgrade() -> None:
    # Jamais de perte silencieuse : zones, tables et changements de table saisis bloquent le
    # retour arrière (comme le menu, 0041, et l'association du client, 0044).
    saved = sum(_count_without_rls(table) for table in TABLES)
    changes = _count_without_rls("restaurant_order_events", "WHERE event_type = 'TABLE_CHANGED'")
    if saved or changes:
        raise RuntimeError(
            f"Retour arrière refusé : {saved} zone(s) ou table(s) et {changes} changement(s) de "
            "table saisis par les utilisateurs seraient perdus."
        )
    role = _app_role()
    op.execute(f"REVOKE UPDATE (table_id) ON restaurant_orders FROM {role}")
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    _event_check(EVENT_TYPES)
    op.drop_constraint(op.f(ORDER_TABLE_CHECK), "restaurant_orders", type_="check")
    op.drop_constraint(op.f(ORDER_TABLE_FK), "restaurant_orders", type_="foreignkey")
    op.drop_index(
        "ix_restaurant_orders_table",
        table_name="restaurant_orders",
        postgresql_where=sa.text("table_id IS NOT NULL"),
    )
    op.drop_index("uq_restaurant_tables_site_label", table_name="restaurant_tables")
    op.drop_index("ix_restaurant_tables_zone", table_name="restaurant_tables")
    op.drop_index(op.f("ix_restaurant_tables_tenant_id"), table_name="restaurant_tables")
    op.drop_index(op.f("ix_restaurant_tables_site_id"), table_name="restaurant_tables")
    op.drop_table("restaurant_tables")
    op.drop_index("uq_restaurant_zones_site_name", table_name="restaurant_zones")
    op.drop_index(op.f("ix_restaurant_zones_tenant_id"), table_name="restaurant_zones")
    op.drop_index(op.f("ix_restaurant_zones_site_id"), table_name="restaurant_zones")
    op.drop_table("restaurant_zones")
