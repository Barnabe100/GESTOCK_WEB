"""Lot 3-B (validation) — prix de conditionnement non configuré (ADR-0040).

``catalog_packagings.sale_price`` devient nullable : ``NULL`` = prix NON CONFIGURÉ (conditionnement
créé sans ``catalog.article.price_update``), invendable tant qu'un utilisateur habilité aux prix
ne l'a pas fixé ; ``0`` = prix réellement configuré à zéro. Les conditionnements existants
gardent leur prix (aucune donnée inventée). ``CHECK sale_price >= 0`` inchangé (vrai pour
``NULL``). Aucun nouveau droit : RLS et droits de la migration 0027 inchangés.

Retour arrière refusé s'il existe des prix non configurés : les ramener à 0 les rendrait
vendables à un prix jamais fixé.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "catalog_packagings",
        "sale_price",
        existing_type=sa.Numeric(precision=18, scale=2),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("ALTER TABLE catalog_packagings NO FORCE ROW LEVEL SECURITY")
    unset = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM catalog_packagings WHERE sale_price IS NULL"))
        .scalar_one()
    )
    op.execute("ALTER TABLE catalog_packagings FORCE ROW LEVEL SECURITY")
    if unset:
        raise RuntimeError(
            f"Retour arrière impossible : {unset} conditionnement(s) au prix non configuré — "
            "l'ancien schéma les rendrait vendables à 0."
        )
    op.alter_column(
        "catalog_packagings",
        "sale_price",
        existing_type=sa.Numeric(precision=18, scale=2),
        nullable=False,
    )
