"""Formule du tarif par poste verrouillée (Phase 3.3-B4, décision finale, ADR-0036).

Décision TechNova : montant = prix du premier poste + (postes − 1) × prix d'un poste
supplémentaire. Seuls ces paramètres (et la période, la devise, la publication) sont
configurables ; la forme de la formule ne l'est pas. Le paramètre « postes compris dans le prix
de base » introduit par 0023 (toujours 1 par défaut) est donc retiré :

- ``plans.included_activations`` ;
- ``subscriptions.included_activations_at_subscription`` (et sa contrainte).

Garde-fou : si une valeur différente de 1 existe (tarif figé ou paramétré autrement), la
migration s'arrête plutôt que de modifier silencieusement un tarif figé. Retour arrière :
colonnes recréées avec la valeur 1 (seule valeur possible avant cette migration en usage).

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _platform_role() -> str:
    quote = op.get_bind().dialect.identifier_preparer.quote
    return quote(get_settings().db_platform_role)


def upgrade() -> None:
    bind = op.get_bind()
    plans = bind.execute(
        sa.text("SELECT count(*) FROM plans WHERE included_activations <> 1")
    ).scalar_one()
    subscriptions = bind.execute(
        sa.text(
            "SELECT count(*) FROM subscriptions WHERE included_activations_at_subscription <> 1"
        )
    ).scalar_one()
    if plans or subscriptions:
        raise RuntimeError(
            "Tarif par poste non standard (postes compris ≠ 1) : "
            f"{plans} plan(s), {subscriptions} abonnement(s). Décision commerciale requise "
            "avant de verrouiller la formule premier poste + (postes − 1) × poste supplémentaire."
        )
    op.drop_constraint(
        op.f("ck_subscriptions_included_activations_snapshot_complete"),
        "subscriptions",
        type_="check",
    )
    op.drop_constraint(op.f("ck_plans_included_activations_positive"), "plans", type_="check")
    op.drop_column("subscriptions", "included_activations_at_subscription")
    op.drop_column("plans", "included_activations")


def downgrade() -> None:
    platform_role = _platform_role()
    op.add_column(
        "plans", sa.Column("included_activations", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "subscriptions",
        sa.Column("included_activations_at_subscription", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE subscriptions SET included_activations_at_subscription = 1 "
        "WHERE price_at_subscription IS NOT NULL"
    )
    op.create_check_constraint(
        "included_activations_positive", "plans", "included_activations >= 1"
    )
    op.create_check_constraint(
        "included_activations_snapshot_complete",
        "subscriptions",
        "(included_activations_at_subscription IS NULL) = (price_at_subscription IS NULL)",
    )
    op.execute(f"GRANT UPDATE (included_activations) ON plans TO {platform_role}")
    op.execute(
        f"GRANT UPDATE (included_activations_at_subscription) ON subscriptions TO {platform_role}"
    )
