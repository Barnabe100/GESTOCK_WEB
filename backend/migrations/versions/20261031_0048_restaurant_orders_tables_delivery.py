"""Livraison de ``restaurant.orders`` et ``restaurant.tables`` (statut ``available``).

Aucun changement de schéma : les structures existent depuis les migrations 0042 à 0047. Ces deux
modules étaient « Bientôt disponible » ; leurs activations par site présentes à cette date sont
INERTES (défauts des profils appliqués aux sites créés pendant cette période, politique E.1
d'ADR-0048) et n'ont jamais ouvert d'accès (routes non montées, permissions non accordées par un
module planifié).

Décision (D10, A11) : sur les sites EXISTANTS, ces activations sont remises à ``false`` — aucun
site ne reçoit les commandes ni les tables sans une activation explicite par un utilisateur
autorisé (``organization.module.manage`` sur le site). Les sites créés ensuite suivent les
défauts de LEUR profil dans LEUR abonnement (règle commune à tous les modules). Rien n'est
supprimé : réglages, commandes, zones et tables éventuels sont conservés.

Rôle propriétaire : la politique de ``site_modules`` ne vise que le rôle applicatif (comme 0042 et
0046). Descente : sans effet (les activations inertes ne sont pas restaurées).

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULES = ("restaurant.orders", "restaurant.tables")


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_modules SET enabled = false "
            "WHERE module_code IN (:orders, :tables) AND enabled"
        ).bindparams(orders=MODULES[0], tables=MODULES[1])
    )


def downgrade() -> None:
    # Les activations inertes remises à ``false`` ne sont pas restaurées (aucune donnée perdue).
    pass
