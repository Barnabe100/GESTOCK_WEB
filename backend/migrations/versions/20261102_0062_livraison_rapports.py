"""Livraison de ``reports`` (statut ``available``, palier R10, ADR-0055).

Aucun changement de schéma : les rapports n'ont aucune table (calculés sur les ventes, les
paiements et les mouvements de stock). Le module était « Bientôt disponible » ; ses activations
par site présentes à cette date sont INERTES (défauts des profils ; activation manuelle refusée
tant qu'il était planifié, palier E.1) et n'ont jamais ouvert d'accès.

Décision (comme 0048, 0050, 0058 et 0060) : sur les sites EXISTANTS, ces activations sont remises
à ``false`` — aucun rapport n'est exposé sans une activation explicite par un utilisateur
autorisé (``organization.module.manage`` sur le site). Les sites créés ensuite suivent les
défauts de LEUR profil dans LEUR abonnement. Rien n'est supprimé.

Offres inchangées : STANDARD et ENTREPRISE incluaient déjà ``reports``.

Rôle propriétaire : la politique de ``site_modules`` ne vise que le rôle applicatif.
Descente : sans effet (les activations inertes ne sont pas restaurées).

Revision ID: 0062
Revises: 0061
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE = "reports"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_modules SET enabled = false WHERE module_code = :reports AND enabled"
        ).bindparams(reports=MODULE)
    )


def downgrade() -> None:
    # Les activations inertes remises à ``false`` ne sont pas restaurées (aucune donnée perdue).
    pass
