"""Livraison de ``restaurant.kitchen`` (statut ``available``, palier R6, ADR-0050 K7).

Aucun changement de schéma : les structures existent depuis la migration 0049. Le module était
« Bientôt disponible » ; ses activations par site présentes à cette date sont INERTES (défauts
des profils Restaurant, Pizzeria, Traiteur et Restauration rapide appliqués aux sites créés
pendant cette période, politique E.1 d'ADR-0048) et n'ont jamais ouvert d'accès (routes non
montées, permissions non accordées par un module planifié, aucun routage des lignes).

Décision (comme 0048 pour les commandes et les tables) : sur les sites EXISTANTS, ces
activations sont remises à ``false`` — aucun site ne reçoit la cuisine sans une activation
explicite par un utilisateur autorisé (``organization.module.manage`` sur le site). Les sites
créés ensuite suivent les défauts de LEUR profil dans LEUR abonnement (règle commune à tous les
modules). Rien n'est supprimé : postes, affectations, priorités et commandes sont conservés.

Rôle propriétaire : la politique de ``site_modules`` ne vise que le rôle applicatif (comme 0048).
Descente : sans effet (les activations inertes ne sont pas restaurées).

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE = "restaurant.kitchen"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_modules SET enabled = false WHERE module_code = :kitchen AND enabled"
        ).bindparams(kitchen=MODULE)
    )


def downgrade() -> None:
    # Les activations inertes remises à ``false`` ne sont pas restaurées (aucune donnée perdue).
    pass
