"""Lot 3-C — Conditionnements dans les opérations de stock (ADR-0041).

- ``stock_entry_lines``, ``stock_exit_lines``, ``stock_transfer_lines`` : présentation saisie
  (``packaging_id``, instantané ``packaging_name`` / ``packaging_conversion``) et
  ``base_quantity`` (quantité en unité de base, celle qui touche le stock ; reprise : la quantité
  saisie, toutes les lignes existantes étant en unité de base). ``quantity`` est désormais
  exprimée dans la présentation choisie. L'unicité « un article par document » devient « une
  ligne par présentation » (unité de base OU conditionnement), comme les lignes de vente (0027).
- ``stock_movements`` : présentation de l'opération (« 3 Carton 24 » pour −72) ; nulle pour les
  mouvements existants, les ajustements d'inventaire et les saisies en unité de base.
- ``inventory_lines`` : comptage saisi dans un conditionnement (quantité de conditionnements +
  unités de base en vrac) ; ``quantity_physical`` reste la quantité en unité de base.

Aucune table nouvelle : RLS (``ENABLE`` + ``FORCE``) et droits existants inchangés (lignes de
documents et d'inventaire : lecture, insertion, mise à jour, suppression des brouillons ;
mouvements : lecture et insertion seulement, append-only). FK composites ``(tenant_id,
packaging_id)`` vers ``catalog_packagings``.

Retour arrière refusé s'il existe des saisies en conditionnement (l'ancien schéma les lirait
comme des quantités en unité de base).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table des lignes, colonne du document)
LINE_TABLES = (
    ("stock_entry_lines", "entry_id"),
    ("stock_exit_lines", "exit_id"),
    ("stock_transfer_lines", "transfer_id"),
)
QUANTITY = sa.Numeric(precision=18, scale=3)
SNAPSHOT_COMPLETE = (
    "(packaging_id IS NULL) = (packaging_name IS NULL) "
    "AND (packaging_id IS NULL) = (packaging_conversion IS NULL)"
)


def _packaging_fk(table: str, column: str = "packaging_id") -> None:
    op.create_foreign_key(
        op.f(f"fk_{table}_tenant_id_{column}_catalog_packagings"),
        table,
        "catalog_packagings",
        ["tenant_id", column],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    for table, document in LINE_TABLES:
        op.add_column(table, sa.Column("packaging_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("packaging_name", sa.String(length=50), nullable=True))
        op.add_column(table, sa.Column("packaging_conversion", QUANTITY, nullable=True))
        op.add_column(table, sa.Column("base_quantity", QUANTITY, nullable=True))
        # Reprise (propriétaire de la table ; FORCE levé dans cette transaction).
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"UPDATE {table} SET base_quantity = quantity")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.alter_column(table, "base_quantity", existing_type=QUANTITY, nullable=False)
        op.drop_constraint(op.f(f"uq_{table}_{document}_article_id"), table, type_="unique")
        op.create_index(
            f"uq_{table}_article_base",
            table,
            [document, "article_id"],
            unique=True,
            postgresql_where=sa.text("packaging_id IS NULL"),
        )
        op.create_index(
            f"uq_{table}_packaging",
            table,
            [document, "packaging_id"],
            unique=True,
            postgresql_where=sa.text("packaging_id IS NOT NULL"),
        )
        _packaging_fk(table)
        op.create_check_constraint("packaging_snapshot_complete", table, SNAPSHOT_COMPLETE)
        op.create_check_constraint(
            "packaging_conversion_positive",
            table,
            "packaging_conversion IS NULL OR packaging_conversion > 0",
        )
        op.create_check_constraint(
            "base_quantity_consistent",
            table,
            "base_quantity = quantity * COALESCE(packaging_conversion, 1)",
        )

    op.add_column("stock_movements", sa.Column("packaging_id", sa.Uuid(), nullable=True))
    op.add_column(
        "stock_movements", sa.Column("packaging_name", sa.String(length=50), nullable=True)
    )
    op.add_column("stock_movements", sa.Column("packaging_conversion", QUANTITY, nullable=True))
    op.add_column("stock_movements", sa.Column("packaging_quantity", QUANTITY, nullable=True))
    _packaging_fk("stock_movements")
    op.create_check_constraint(
        "packaging_snapshot_complete",
        "stock_movements",
        SNAPSHOT_COMPLETE + " AND (packaging_id IS NULL) = (packaging_quantity IS NULL)",
    )
    op.create_check_constraint(
        "packaging_quantity_consistent",
        "stock_movements",
        "packaging_quantity IS NULL OR (packaging_quantity > 0 "
        "AND abs(quantity) = packaging_quantity * packaging_conversion)",
    )

    for column, type_ in (
        ("count_packaging_id", sa.Uuid()),
        ("count_packaging_name", sa.String(length=50)),
        ("count_packaging_conversion", QUANTITY),
        ("count_packaging_quantity", QUANTITY),
        ("count_unit_quantity", QUANTITY),
    ):
        op.add_column("inventory_lines", sa.Column(column, type_, nullable=True))
    _packaging_fk("inventory_lines", "count_packaging_id")
    op.create_check_constraint(
        "count_packaging_complete",
        "inventory_lines",
        "(count_packaging_id IS NULL) = (count_packaging_name IS NULL) "
        "AND (count_packaging_id IS NULL) = (count_packaging_conversion IS NULL) "
        "AND (count_packaging_id IS NULL) = (count_packaging_quantity IS NULL) "
        "AND (count_packaging_id IS NULL) = (count_unit_quantity IS NULL)",
    )
    op.create_check_constraint(
        "count_packaging_consistent",
        "inventory_lines",
        "count_packaging_id IS NULL OR (count_packaging_conversion > 0 "
        "AND count_packaging_quantity >= 0 AND count_unit_quantity >= 0 "
        "AND quantity_physical = count_packaging_quantity * count_packaging_conversion "
        "+ count_unit_quantity)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    checks = [(table, "packaging_id") for table, _ in LINE_TABLES] + [
        ("stock_movements", "packaging_id"),
        ("inventory_lines", "count_packaging_id"),
    ]
    used = 0
    for table, column in checks:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        used += bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL")
        ).scalar_one()
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if used:
        raise RuntimeError(
            f"Retour arrière impossible : {used} saisie(s) de stock en conditionnement — "
            "l'ancien schéma les lirait comme des quantités en unité de base."
        )

    for name in ("count_packaging_consistent", "count_packaging_complete"):
        op.drop_constraint(op.f(f"ck_inventory_lines_{name}"), "inventory_lines", type_="check")
    op.drop_constraint(
        op.f("fk_inventory_lines_tenant_id_count_packaging_id_catalog_packagings"),
        "inventory_lines",
        type_="foreignkey",
    )
    for column in (
        "count_unit_quantity",
        "count_packaging_quantity",
        "count_packaging_conversion",
        "count_packaging_name",
        "count_packaging_id",
    ):
        op.drop_column("inventory_lines", column)

    for name in ("packaging_quantity_consistent", "packaging_snapshot_complete"):
        op.drop_constraint(op.f(f"ck_stock_movements_{name}"), "stock_movements", type_="check")
    op.drop_constraint(
        op.f("fk_stock_movements_tenant_id_packaging_id_catalog_packagings"),
        "stock_movements",
        type_="foreignkey",
    )
    for column in ("packaging_quantity", "packaging_conversion", "packaging_name", "packaging_id"):
        op.drop_column("stock_movements", column)

    for table, document in LINE_TABLES:
        for name in (
            "base_quantity_consistent",
            "packaging_conversion_positive",
            "packaging_snapshot_complete",
        ):
            op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
        op.drop_constraint(
            op.f(f"fk_{table}_tenant_id_packaging_id_catalog_packagings"),
            table,
            type_="foreignkey",
        )
        op.drop_index(f"uq_{table}_packaging", table_name=table)
        op.drop_index(f"uq_{table}_article_base", table_name=table)
        op.create_unique_constraint(
            op.f(f"uq_{table}_{document}_article_id"), table, [document, "article_id"]
        )
        for column in ("base_quantity", "packaging_conversion", "packaging_name", "packaging_id"):
            op.drop_column(table, column)
