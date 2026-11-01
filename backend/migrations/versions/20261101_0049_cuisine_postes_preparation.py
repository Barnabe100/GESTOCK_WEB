"""Palier R6 — cuisine : postes de préparation, affectations du menu, poste des lignes, priorité
(ADR-0050 ; ``RESTAURANT.md`` §4).

- ``restaurant_prep_stations`` : poste d'UN site, nom unique par site insensible à la casse,
  jamais supprimé (désactivation) ; cible des clés étrangères composites (même site).
- ``restaurant_station_routes`` : affectation d'une section OU d'un élément du menu du MÊME site
  à un poste (une au plus par cible, index uniques partiels) ; retirer une affectation supprime
  sa ligne (configuration, audit).
- ``restaurant_order_lines.station_id`` (facultatif, FK composite ``RESTRICT``, index partiel) et
  droit ``UPDATE (station_id)`` du rôle applicatif (le déclencheur des états finaux protège
  toujours les lignes servies ou annulées).
- ``restaurant_orders.priority`` (``NORMAL`` | ``HIGH`` | ``URGENT``, défaut ``NORMAL`` pour les
  commandes existantes) et droit ``UPDATE (priority)``.
- Types d'évènement ``PRIORITY_CHANGED`` et ``LINES_ROUTED`` (historique en ajout seul).

RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : postes ``SELECT, INSERT, UPDATE`` des colonnes
modifiables ; affectations ``SELECT, INSERT, UPDATE (station_id, updated_at), DELETE`` ; aucun
droit pour les rôles de la console et de la purge. Le module reste planifié (aucune activation
modifiée ici : la migration de livraison s'en charge).

Descente refusée si elle perdait un poste, une affectation, une priorité ou un routage saisis.

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("restaurant_prep_stations", "restaurant_station_routes")
LINE_STATION_FK = "fk_restaurant_order_lines_tenant_id_site_id_station_id_restaurant_prep_stations"
PRIORITY_CHECK = "ck_restaurant_orders_restaurant_order_priority"
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
    "TABLE_CHANGED",
    "RELEASED",
)
NEW_EVENT_TYPES = ("PRIORITY_CHANGED", "LINES_ROUTED")


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


def upgrade() -> None:
    op.create_table(
        "restaurant_prep_stations",
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
            "btrim(name) = name AND name <> ''",
            name=op.f("ck_restaurant_prep_stations_name_trimmed"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_prep_stations_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_prep_stations_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_prep_stations")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_prep_stations_tenant_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "id",
            name=op.f("uq_restaurant_prep_stations_tenant_id_site_id_id"),
        ),
    )
    op.create_index(
        op.f("ix_restaurant_prep_stations_site_id"), "restaurant_prep_stations", ["site_id"]
    )
    op.create_index(
        op.f("ix_restaurant_prep_stations_tenant_id"), "restaurant_prep_stations", ["tenant_id"]
    )
    op.create_index(
        "uq_restaurant_prep_stations_site_name",
        "restaurant_prep_stations",
        ["tenant_id", "site_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "restaurant_station_routes",
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("station_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=True),
        sa.Column("menu_item_id", sa.Uuid(), nullable=True),
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
            "num_nonnulls(section_id, menu_item_id) = 1",
            name=op.f("ck_restaurant_station_routes_one_target"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "menu_item_id"],
            [
                "restaurant_menu_items.tenant_id",
                "restaurant_menu_items.site_id",
                "restaurant_menu_items.id",
            ],
            name=op.f(
                "fk_restaurant_station_routes_tenant_id_site_id_menu_item_id_restaurant_menu_items"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "section_id"],
            [
                "restaurant_menu_sections.tenant_id",
                "restaurant_menu_sections.site_id",
                "restaurant_menu_sections.id",
            ],
            name=op.f(
                "fk_restaurant_station_routes_tenant_id_site_id_section_id_restaurant_menu_sections"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id", "station_id"],
            [
                "restaurant_prep_stations.tenant_id",
                "restaurant_prep_stations.site_id",
                "restaurant_prep_stations.id",
            ],
            name=op.f(
                "fk_restaurant_station_routes_tenant_id_site_id_station_id_restaurant_prep_stations"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_restaurant_station_routes_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_restaurant_station_routes_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restaurant_station_routes")),
        sa.UniqueConstraint(
            "tenant_id", "id", name=op.f("uq_restaurant_station_routes_tenant_id_id")
        ),
    )
    op.create_index(
        op.f("ix_restaurant_station_routes_site_id"), "restaurant_station_routes", ["site_id"]
    )
    op.create_index(
        "ix_restaurant_station_routes_station",
        "restaurant_station_routes",
        ["tenant_id", "station_id"],
    )
    op.create_index(
        op.f("ix_restaurant_station_routes_tenant_id"), "restaurant_station_routes", ["tenant_id"]
    )
    op.create_index(
        "uq_restaurant_station_routes_item",
        "restaurant_station_routes",
        ["tenant_id", "site_id", "menu_item_id"],
        unique=True,
        postgresql_where=sa.text("menu_item_id IS NOT NULL"),
    )
    op.create_index(
        "uq_restaurant_station_routes_section",
        "restaurant_station_routes",
        ["tenant_id", "site_id", "section_id"],
        unique=True,
        postgresql_where=sa.text("section_id IS NOT NULL"),
    )
    op.add_column("restaurant_order_lines", sa.Column("station_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_restaurant_order_lines_station",
        "restaurant_order_lines",
        ["tenant_id", "site_id", "station_id"],
        postgresql_where=sa.text("station_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f(LINE_STATION_FK),
        "restaurant_order_lines",
        "restaurant_prep_stations",
        ["tenant_id", "site_id", "station_id"],
        ["tenant_id", "site_id", "id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "restaurant_orders",
        sa.Column("priority", sa.String(length=32), server_default="NORMAL", nullable=False),
    )
    op.create_check_constraint(
        op.f(PRIORITY_CHECK),
        "restaurant_orders",
        "priority IN ('NORMAL', 'HIGH', 'URGENT')",
    )
    _event_check((*EVENT_TYPES, *NEW_EVENT_TYPES))

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
        "GRANT UPDATE (name, sort_order, is_active, updated_at) "
        f"ON restaurant_prep_stations TO {role}"
    )
    op.execute(
        f"GRANT UPDATE (station_id, updated_at), DELETE ON restaurant_station_routes TO {role}"
    )
    op.execute(f"GRANT UPDATE (station_id) ON restaurant_order_lines TO {role}")
    op.execute(f"GRANT UPDATE (priority) ON restaurant_orders TO {role}")


def downgrade() -> None:
    # Jamais de perte silencieuse : postes, affectations, priorités, routages et évènements de la
    # cuisine bloquent le retour arrière (comme 0041, 0044, 0046).
    saved = sum(_count_without_rls(table) for table in TABLES)
    routed = _count_without_rls("restaurant_order_lines", "WHERE station_id IS NOT NULL")
    urgent = _count_without_rls("restaurant_orders", "WHERE priority <> 'NORMAL'")
    events = _count_without_rls(
        "restaurant_order_events",
        "WHERE event_type IN ('PRIORITY_CHANGED', 'LINES_ROUTED')",
    )
    if saved or routed or urgent or events:
        raise RuntimeError(
            f"Retour arrière refusé : {saved} poste(s) ou affectation(s), {routed} ligne(s) "
            f"routée(s), {urgent} priorité(s) et {events} évènement(s) de la cuisine seraient "
            "perdus."
        )
    role = _app_role()
    op.execute(f"REVOKE UPDATE (priority) ON restaurant_orders FROM {role}")
    op.execute(f"REVOKE UPDATE (station_id) ON restaurant_order_lines FROM {role}")
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {role}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    _event_check(EVENT_TYPES)
    op.drop_constraint(op.f(PRIORITY_CHECK), "restaurant_orders", type_="check")
    op.drop_column("restaurant_orders", "priority")
    op.drop_constraint(op.f(LINE_STATION_FK), "restaurant_order_lines", type_="foreignkey")
    op.drop_index(
        "ix_restaurant_order_lines_station",
        table_name="restaurant_order_lines",
        postgresql_where=sa.text("station_id IS NOT NULL"),
    )
    op.drop_column("restaurant_order_lines", "station_id")
    op.drop_index(
        "uq_restaurant_station_routes_section",
        table_name="restaurant_station_routes",
        postgresql_where=sa.text("section_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_restaurant_station_routes_item",
        table_name="restaurant_station_routes",
        postgresql_where=sa.text("menu_item_id IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_restaurant_station_routes_tenant_id"), table_name="restaurant_station_routes"
    )
    op.drop_index("ix_restaurant_station_routes_station", table_name="restaurant_station_routes")
    op.drop_index(
        op.f("ix_restaurant_station_routes_site_id"), table_name="restaurant_station_routes"
    )
    op.drop_table("restaurant_station_routes")
    op.drop_index("uq_restaurant_prep_stations_site_name", table_name="restaurant_prep_stations")
    op.drop_index(
        op.f("ix_restaurant_prep_stations_tenant_id"), table_name="restaurant_prep_stations"
    )
    op.drop_index(
        op.f("ix_restaurant_prep_stations_site_id"), table_name="restaurant_prep_stations"
    )
    op.drop_table("restaurant_prep_stations")
