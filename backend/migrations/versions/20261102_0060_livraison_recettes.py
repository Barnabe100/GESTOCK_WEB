"""Livraison de ``restaurant.recipes`` (statut ``available``, palier R8, ADR-0053 D15).

Aucun changement de schéma : les structures existent depuis la migration 0059. Le module était
« Bientôt disponible » ; ses activations par site présentes à cette date sont INERTES (défauts
des profils de restauration ; activation manuelle refusée tant qu'il était planifié, palier
E.1) et n'ont jamais ouvert d'accès ni déduit de stock.

Décision (comme 0048, 0050 et 0058) : sur les sites EXISTANTS, ces activations sont remises à
``false`` — aucune vente de plat ne déduit d'ingrédient sans une activation explicite par un
utilisateur autorisé (``organization.module.manage`` sur le site). Les sites créés ensuite
suivent les défauts de LEUR profil dans LEUR abonnement. Rien n'est supprimé.

Offres inchangées : STANDARD et ENTREPRISE incluaient déjà ``restaurant.recipes``.

Rôle propriétaire : la politique de ``site_modules`` ne vise que le rôle applicatif.
Descente : sans effet (les activations inertes ne sont pas restaurées).

Revision ID: 0060
Revises: 0059
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE = "restaurant.recipes"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_modules SET enabled = false WHERE module_code = :recipes AND enabled"
        ).bindparams(recipes=MODULE)
    )


def downgrade() -> None:
    # Les activations inertes remises à ``false`` ne sont pas restaurées (aucune donnée perdue).
    pass
