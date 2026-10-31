"""Commandes de restauration — libération explicite et délai entre prises de 5 ou 10 minutes.

- Type d'évènement ``RELEASED`` (historique en ajout seul) : libération d'une commande par son
  responsable, motif facultatif. Aucune colonne nouvelle : la libération remet à ``NULL`` les
  colonnes de prise en charge, déjà modifiables par le rôle applicatif (migration 0042).
- ``restaurant_site_settings.claim_cooldown_minutes`` : délai entre deux prises d'un même employé
  sur un site limité à **5 ou 10 minutes** (contrainte ``claim_cooldown_choice``, qui remplace
  ``claim_cooldown_range`` 0 à 1440), défaut 5 (fixé par le service). Valeurs existantes
  ramenées au choix le plus proche SANS jamais assouplir la règle : 0 à 5 → 5, au-delà → 10.
  La durée de protection d'une commande prise (``claim_protection_minutes``) est inchangée.

Données : réglages réécrits par le rôle propriétaire (RLS forcée levée le temps de la mise à
jour). Descente : rétablit l'ancienne contrainte (5 et 10 y restent valides, aucune valeur
d'origine restaurée) et refuse de perdre une libération enregistrée.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SETTINGS = "restaurant_site_settings"
EVENTS = "restaurant_order_events"
EVENT_CHECK = "ck_restaurant_order_events_restaurant_order_event_type"
COOLDOWN_RANGE = "ck_restaurant_site_settings_claim_cooldown_range"
COOLDOWN_CHOICE = "ck_restaurant_site_settings_claim_cooldown_choice"
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
)


def _event_check(values: tuple[str, ...]) -> None:
    op.execute(f"ALTER TABLE {EVENTS} DROP CONSTRAINT {EVENT_CHECK}")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"ALTER TABLE {EVENTS} ADD CONSTRAINT {EVENT_CHECK} CHECK (event_type IN ({allowed}))"
    )


def _without_rls(table: str, sql: str) -> int:
    """Instruction du rôle propriétaire hors RLS (forcée) ; renvoie le nombre de lignes."""
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    result = op.get_bind().execute(sa.text(sql))
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    return int(result.scalar_one()) if result.returns_rows else int(result.rowcount)


def upgrade() -> None:
    _event_check((*EVENT_TYPES, "RELEASED"))
    op.execute(f"ALTER TABLE {SETTINGS} DROP CONSTRAINT {COOLDOWN_RANGE}")
    _without_rls(
        SETTINGS,
        f"UPDATE {SETTINGS} SET claim_cooldown_minutes = "
        "CASE WHEN claim_cooldown_minutes <= 5 THEN 5 ELSE 10 END "
        "WHERE claim_cooldown_minutes NOT IN (5, 10)",
    )
    op.execute(
        f"ALTER TABLE {SETTINGS} ADD CONSTRAINT {COOLDOWN_CHOICE} "
        "CHECK (claim_cooldown_minutes IN (5, 10))"
    )


def downgrade() -> None:
    released = _without_rls(EVENTS, f"SELECT count(*) FROM {EVENTS} WHERE event_type = 'RELEASED'")
    if released:
        raise RuntimeError(
            f"Retour arrière refusé : {released} libération(s) de commande enregistrée(s) "
            "seraient perdues."
        )
    op.execute(f"ALTER TABLE {SETTINGS} DROP CONSTRAINT {COOLDOWN_CHOICE}")
    op.execute(
        f"ALTER TABLE {SETTINGS} ADD CONSTRAINT {COOLDOWN_RANGE} "
        "CHECK (claim_cooldown_minutes BETWEEN 0 AND 1440)"
    )
    _event_check(EVENT_TYPES)
