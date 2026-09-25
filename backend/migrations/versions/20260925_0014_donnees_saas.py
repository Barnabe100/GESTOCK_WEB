"""Données SaaS (Phase 3.2) : référentiel des pays, entreprise du tenant, paramètres commerciaux
des plans, prix figé de l'abonnement, limitation de fréquence.

- ``geo_countries`` : ISO 3166-1 alpha-2 (catalogue global synchronisé par ``catalog sync``),
  lecture seule pour le rôle applicatif.
- ``tenants`` : pays (clé étrangère ; nul seulement pour les tenants antérieurs, aucun
  remplissage artificiel) et informations d'entreprise, toutes facultatives en base ; la RLS
  existante de ``tenants`` s'applique.
- ``plans`` : paramètres commerciaux aux valeurs neutres (non publié, prix masqués, périodes
  désactivées, aucun essai) ; ``catalog sync`` ne les modifie jamais.
- ``subscriptions`` : prix et devise figés à la souscription.
- ``rate_limit_hits`` : tentatives (clé hachée) ; SELECT, INSERT, DELETE pour l'application.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-25 11:51:00.551149+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PLAN_CHECKS = {
    "monthly_price_positive": "monthly_price IS NULL OR monthly_price >= 0",
    "annual_price_positive": "annual_price IS NULL OR annual_price >= 0",
    "trial_days_positive": "trial_days >= 0",
    "iso_currency": "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
    "monthly_enabled_has_price": (
        "NOT monthly_price_enabled OR (monthly_price IS NOT NULL AND currency IS NOT NULL)"
    ),
    "annual_enabled_has_price": (
        "NOT annual_price_enabled OR (annual_price IS NOT NULL AND currency IS NOT NULL)"
    ),
}


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_table(
        "geo_countries",
        sa.Column("code", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("calling_code", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("code ~ '^[A-Z]{2}$'", name=op.f("ck_geo_countries_iso_code")),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_geo_countries_iso_currency"),
        ),
        sa.CheckConstraint(
            "NOT is_active OR (currency IS NOT NULL AND timezone IS NOT NULL)",
            name=op.f("ck_geo_countries_active_has_defaults"),
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_geo_countries")),
    )
    op.create_table(
        "rate_limit_hits",
        sa.Column("bucket", sa.String(length=50), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_limit_hits")),
    )
    op.create_index(
        "ix_rate_limit_hits_bucket_key_at",
        "rate_limit_hits",
        ["bucket", "key_hash", "created_at"],
        unique=False,
    )
    op.add_column(
        "plans", sa.Column("listed", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "plans",
        sa.Column("price_display_enabled", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "plans", sa.Column("monthly_price", sa.Numeric(precision=18, scale=2), nullable=True)
    )
    op.add_column(
        "plans",
        sa.Column("monthly_price_enabled", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "plans", sa.Column("annual_price", sa.Numeric(precision=18, scale=2), nullable=True)
    )
    op.add_column(
        "plans",
        sa.Column("annual_price_enabled", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("plans", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column(
        "plans", sa.Column("contact_required", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column("plans", sa.Column("commercial_description", sa.Text(), nullable=True))
    op.add_column(
        "plans", sa.Column("display_order", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "plans", sa.Column("trial_days", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "subscriptions",
        sa.Column("price_at_subscription", sa.Numeric(precision=18, scale=2), nullable=True),
    )
    op.add_column(
        "subscriptions", sa.Column("currency_at_subscription", sa.String(length=3), nullable=True)
    )
    op.add_column("tenants", sa.Column("country_code", sa.String(length=2), nullable=True))
    op.add_column("tenants", sa.Column("trade_name", sa.String(length=150), nullable=True))
    op.add_column("tenants", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("tenants", sa.Column("phone", sa.String(length=30), nullable=True))
    op.add_column("tenants", sa.Column("address", sa.String(length=255), nullable=True))
    op.add_column("tenants", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("tenants", sa.Column("region", sa.String(length=100), nullable=True))
    op.add_column("tenants", sa.Column("website", sa.String(length=500), nullable=True))
    op.add_column("tenants", sa.Column("tax_id", sa.String(length=50), nullable=True))
    op.add_column("tenants", sa.Column("trade_register", sa.String(length=50), nullable=True))
    op.add_column("tenants", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("logo_url", sa.String(length=500), nullable=True))
    op.create_foreign_key(
        op.f("fk_tenants_country_code_geo_countries"),
        "tenants",
        "geo_countries",
        ["country_code"],
        ["code"],
        ondelete="RESTRICT",
    )

    for name, condition in PLAN_CHECKS.items():
        op.create_check_constraint(name, "plans", condition)
    op.create_check_constraint(
        "price_snapshot_complete",
        "subscriptions",
        "(price_at_subscription IS NULL) = (currency_at_subscription IS NULL)",
    )
    op.execute(f"GRANT SELECT ON geo_countries TO {_app_role()}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON rate_limit_hits TO {_app_role()}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON geo_countries, rate_limit_hits FROM {_app_role()}")
    op.drop_constraint(
        op.f("ck_subscriptions_price_snapshot_complete"), "subscriptions", type_="check"
    )
    for name in PLAN_CHECKS:
        op.drop_constraint(op.f(f"ck_plans_{name}"), "plans", type_="check")
    op.drop_constraint(op.f("fk_tenants_country_code_geo_countries"), "tenants", type_="foreignkey")
    op.drop_column("tenants", "logo_url")
    op.drop_column("tenants", "description")
    op.drop_column("tenants", "trade_register")
    op.drop_column("tenants", "tax_id")
    op.drop_column("tenants", "website")
    op.drop_column("tenants", "region")
    op.drop_column("tenants", "city")
    op.drop_column("tenants", "address")
    op.drop_column("tenants", "phone")
    op.drop_column("tenants", "email")
    op.drop_column("tenants", "trade_name")
    op.drop_column("tenants", "country_code")
    op.drop_column("subscriptions", "currency_at_subscription")
    op.drop_column("subscriptions", "price_at_subscription")
    op.drop_column("plans", "trial_days")
    op.drop_column("plans", "display_order")
    op.drop_column("plans", "commercial_description")
    op.drop_column("plans", "contact_required")
    op.drop_column("plans", "currency")
    op.drop_column("plans", "annual_price_enabled")
    op.drop_column("plans", "annual_price")
    op.drop_column("plans", "monthly_price_enabled")
    op.drop_column("plans", "monthly_price")
    op.drop_column("plans", "price_display_enabled")
    op.drop_column("plans", "listed")
    op.drop_index("ix_rate_limit_hits_bucket_key_at", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
    op.drop_table("geo_countries")
