"""Palier R2-E — association tardive d'un client à une commande (ADR-0049 Z1).

- Droit ``UPDATE (customer_id)`` sur ``restaurant_orders`` pour le rôle applicatif (jusqu'ici le
  client n'était posé qu'à la création) ; le déclencheur ``trg_restaurant_final_state`` protège
  toujours les commandes closes, annulées ou refusées.
- Type d'évènement ``CUSTOMER_SET`` ajouté à la contrainte ``restaurant_order_event_type``
  (historique en ajout seul).

Aucune table nouvelle, RLS inchangée. Descente REFUSÉE dès qu'un évènement ``CUSTOMER_SET``
existe (Q7 : aucune perte silencieuse de l'historique) ; comptage hors RLS le temps du contrôle.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _event_check(values: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE restaurant_order_events DROP CONSTRAINT {EVENT_CHECK}")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"ALTER TABLE restaurant_order_events ADD CONSTRAINT {EVENT_CHECK} "
        f"CHECK (event_type IN ({allowed}))"
    )


def upgrade() -> None:
    _event_check((*EVENT_TYPES, "CUSTOMER_SET"))
    op.execute(f"GRANT UPDATE (customer_id) ON restaurant_orders TO {_app_role()}")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("ALTER TABLE restaurant_order_events NO FORCE ROW LEVEL SECURITY")
    count = bind.execute(
        sa.text("SELECT count(*) FROM restaurant_order_events WHERE event_type = 'CUSTOMER_SET'")
    ).scalar_one()
    op.execute("ALTER TABLE restaurant_order_events FORCE ROW LEVEL SECURITY")
    if count:
        raise RuntimeError(
            f"Retour arrière refusé : {count} association(s) de client perdraient leur trace "
            "dans l'historique des commandes."
        )
    op.execute(f"REVOKE UPDATE (customer_id) ON restaurant_orders FROM {_app_role()}")
    _event_check(EVENT_TYPES)
