"""Livraison de ``restaurant.qr`` (statut ``available``, palier R7-F, ADR-0052).

Aucun changement de schéma : les structures existent depuis les migrations 0056 et 0057. Le
module était « Bientôt disponible » ; ses activations par site présentes à cette date sont
INERTES (profils où il est facultatif : écrit désactivé ; activation manuelle refusée tant
qu'il était planifié, palier E.1) et n'ont jamais ouvert d'accès (routes non montées,
permissions non accordées par un module planifié).

Décision (comme 0048 et 0050) : sur les sites EXISTANTS, ces activations sont remises à
``false`` — aucun site ne reçoit les commandes par QR Code sans une activation explicite par un
utilisateur autorisé (``organization.module.manage`` sur le site). Les sites créés ensuite
suivent les défauts de LEUR profil dans LEUR abonnement. Rien n'est supprimé.

Offre : ``restaurant.qr`` entre dans STANDARD (``plans.toml``, ``stockmanager catalog sync`` ;
ENTREPRISE l'inclut déjà) ; une licence en vigueur fige ses modules : un site STANDARD déjà
licencié l'obtient à sa licence suivante.

Rôle propriétaire : la politique de ``site_modules`` ne vise que le rôle applicatif.
Descente : sans effet (les activations inertes ne sont pas restaurées).

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE = "restaurant.qr"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE site_modules SET enabled = false WHERE module_code = :qr AND enabled"
        ).bindparams(qr=MODULE)
    )


def downgrade() -> None:
    # Les activations inertes remises à ``false`` ne sont pas restaurées (aucune donnée perdue).
    pass
