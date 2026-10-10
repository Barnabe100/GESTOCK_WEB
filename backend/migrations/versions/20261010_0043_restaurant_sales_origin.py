"""Palier R2-D — origine des ventes et règlement des commandes (ADR-0049 D4, D6, D13, D14).

- ``sales.origin_type`` / ``sales.origin_id`` : document dont la vente est issue (commande de
  restauration : ``restaurant_order``), posés par le serveur à la création, **immuables**
  (déclencheur ``sales_origin_immutable`` : ni ajout, ni modification, ni effacement après
  l'insertion) ; complets ou absents (CHECK) ; ventes existantes : origine nulle, inchangées.
- ``uq_sales_active_origin`` : au plus UNE vente active (non annulée) par origine (index
  unique partiel) ; une nouvelle vente reste possible après annulation (procédure Z3).
- Canal ``RESTAURANT`` ajouté à la contrainte ``ck_sales_sale_channel`` ; une vente de ce canal
  a toujours une origine (CHECK).
- ``restaurant_orders.sale_id`` → ``sales (tenant_id, id, site_id)`` : vente du même tenant ET
  du même site que la commande (FK composite).

Droits inchangés (le rôle applicatif insère les ventes ; le déclencheur protège l'origine même
contre une écriture SQL directe). RLS déjà active sur ``sales``.

Descente REFUSÉE dès qu'une vente porte une origine ou le canal ``RESTAURANT`` (Q7) : aucune
perte silencieuse ; comptage hors RLS le temps du contrôle.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANNEL_CHECK = "ck_sales_sale_channel"

ORIGIN_IMMUTABLE_FUNCTION = """
CREATE FUNCTION sales_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.origin_type IS DISTINCT FROM OLD.origin_type
       OR NEW.origin_id IS DISTINCT FROM OLD.origin_id THEN
        RAISE EXCEPTION 'sales_origin_immutable: l''origine d''une vente est définitive'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$
"""


def _channel_check(values: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE sales DROP CONSTRAINT {CHANNEL_CHECK}")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.execute(f"ALTER TABLE sales ADD CONSTRAINT {CHANNEL_CHECK} CHECK (channel IN ({allowed}))")


def upgrade() -> None:
    op.add_column("sales", sa.Column("origin_type", sa.String(length=32), nullable=True))
    op.add_column("sales", sa.Column("origin_id", sa.Uuid(), nullable=True))
    _channel_check(("BACKOFFICE", "POS", "RESTAURANT"))
    op.create_check_constraint(
        op.f("ck_sales_origin_complete"), "sales", "(origin_type IS NULL) = (origin_id IS NULL)"
    )
    op.create_check_constraint(
        op.f("ck_sales_restaurant_has_origin"),
        "sales",
        "channel <> 'RESTAURANT' OR origin_type IS NOT NULL",
    )
    op.create_index(
        "uq_sales_active_origin",
        "sales",
        ["tenant_id", "origin_type", "origin_id"],
        unique=True,
        postgresql_where=sa.text("origin_id IS NOT NULL AND status <> 'CANCELLED'"),
    )
    op.execute(ORIGIN_IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER sales_origin_immutable BEFORE UPDATE OF origin_type, origin_id ON sales "
        "FOR EACH ROW EXECUTE FUNCTION sales_origin_immutable()"
    )
    op.create_foreign_key(
        op.f("fk_restaurant_orders_tenant_id_sale_id_site_id_sales"),
        "restaurant_orders",
        "sales",
        ["tenant_id", "sale_id", "site_id"],
        ["tenant_id", "id", "site_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # Jamais de perte silencieuse (Q7) : une vente issue d'une commande (origine ou canal
    # RESTAURANT) bloque le retour arrière, quelle que soit l'entreprise.
    bind = op.get_bind()
    op.execute("ALTER TABLE sales NO FORCE ROW LEVEL SECURITY")
    count = bind.execute(
        sa.text("SELECT count(*) FROM sales WHERE origin_id IS NOT NULL OR channel = 'RESTAURANT'")
    ).scalar_one()
    op.execute("ALTER TABLE sales FORCE ROW LEVEL SECURITY")
    if count:
        raise RuntimeError(
            f"Retour arrière refusé : {count} vente(s) issue(s) de commandes de restauration "
            "perdraient leur origine."
        )
    op.drop_constraint(
        op.f("fk_restaurant_orders_tenant_id_sale_id_site_id_sales"),
        "restaurant_orders",
        type_="foreignkey",
    )
    op.execute("DROP TRIGGER IF EXISTS sales_origin_immutable ON sales")
    op.execute("DROP FUNCTION IF EXISTS sales_origin_immutable()")
    op.drop_index(
        "uq_sales_active_origin",
        table_name="sales",
        postgresql_where=sa.text("origin_id IS NOT NULL AND status <> 'CANCELLED'"),
    )
    op.drop_constraint(op.f("ck_sales_restaurant_has_origin"), "sales", type_="check")
    op.drop_constraint(op.f("ck_sales_origin_complete"), "sales", type_="check")
    _channel_check(("BACKOFFICE", "POS"))
    op.drop_column("sales", "origin_id")
    op.drop_column("sales", "origin_type")
