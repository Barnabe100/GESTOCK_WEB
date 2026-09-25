"""Profils d'activité : secteurs, profils UX, codes <secteur>.<activité> (Phase 3.1, ADR-0024).

- ``business_sectors`` et ``ux_profiles`` : catalogue global (hors tenant, synchronisé par
  ``stockmanager catalog sync`` avec le rôle propriétaire ; lecture seule pour le rôle
  applicatif, comme ``business_profiles``).
- ``business_profiles`` : secteur, profil UX, ordre, surcharges ``dashboard`` et ``theme`` ;
  un profil actif a toujours un secteur et un profil UX (``CHECK``).
- Données : les tenants existants passent des anciens codes aux nouveaux (``alimentation`` →
  ``retail.alimentation``…). Le nouveau profil est créé inactif par copie de l'ancien (mêmes
  modules proposés) ; la synchronisation du catalogue, exécutée après la migration, le
  complète et l'active, et désactive l'ancien (jamais supprimé). Aucune donnée métier touchée.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Anciens codes (Phases 1 à 3.0) → nouveaux codes.
RENAMED = {
    "alimentation": "retail.alimentation",
    "quincaillerie": "retail.quincaillerie",
    "commerce_general": "retail.specialise",
    "restaurant": "restaurant.restaurant",
}
LEGACY_NAMES = {
    "alimentation": "Alimentation / Supérette",
    "quincaillerie": "Quincaillerie",
    "commerce_general": "Commerce général / Boutique",
    "restaurant": "Restaurant / Maquis / Café / Bar / Fast-food",
}


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def _update_tenants(sql: str, params: dict[str, str]) -> None:
    """Mise à jour de ``tenants`` par le rôle propriétaire : la RLS forcée (FORCE) est levée le
    temps de l'instruction, dans la transaction de la migration, puis rétablie."""
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
    op.get_bind().execute(sa.text(sql), params)
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.create_table(
        "business_sectors",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("icon", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("code", name=op.f("pk_business_sectors")),
    )
    op.create_table(
        "ux_profiles",
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("navigation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dashboard", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("terminology", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("theme", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("code", name=op.f("pk_ux_profiles")),
    )
    op.add_column(
        "business_profiles", sa.Column("sector_code", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "business_profiles", sa.Column("ux_profile_code", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "business_profiles",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "business_profiles",
        sa.Column(
            "dashboard",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column(
        "business_profiles",
        sa.Column(
            "theme", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
    )
    op.create_foreign_key(
        op.f("fk_business_profiles_sector_code_business_sectors"),
        "business_profiles",
        "business_sectors",
        ["sector_code"],
        ["code"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_business_profiles_ux_profile_code_ux_profiles"),
        "business_profiles",
        "ux_profiles",
        ["ux_profile_code"],
        ["code"],
        ondelete="RESTRICT",
    )

    # Anciens codes : copie inactive sous le nouveau code, puis bascule des tenants.
    bind = op.get_bind()
    for old, new in RENAMED.items():
        params = {"old": old, "new": new}
        bind.execute(
            sa.text(
                "INSERT INTO business_profiles (code, name, description, is_active, navigation, "
                "terminology, settings) "
                "SELECT :new, name, description, false, '[]'::jsonb, '{}'::jsonb, settings "
                "FROM business_profiles WHERE code = :old ON CONFLICT (code) DO NOTHING"
            ),
            params,
        )
        bind.execute(
            sa.text(
                "INSERT INTO business_profile_modules (profile_code, module_code, default_enabled) "
                "SELECT :new, module_code, default_enabled FROM business_profile_modules "
                "WHERE profile_code = :old ON CONFLICT DO NOTHING"
            ),
            params,
        )
        _update_tenants(
            "UPDATE tenants SET business_profile_code = :new WHERE business_profile_code = :old",
            params,
        )
        bind.execute(
            sa.text("UPDATE business_profiles SET is_active = false WHERE code = :old"), params
        )

    op.create_check_constraint(
        "active_profile_classified",
        "business_profiles",
        "NOT is_active OR (sector_code IS NOT NULL AND ux_profile_code IS NOT NULL)",
    )
    op.execute(f"GRANT SELECT ON business_sectors, ux_profiles TO {_app_role()}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON business_sectors, ux_profiles FROM {_app_role()}")
    op.drop_constraint(
        op.f("ck_business_profiles_active_profile_classified"),
        "business_profiles",
        type_="check",
    )
    bind = op.get_bind()
    # Anciens codes rétablis (créés au besoin ; modules complétés par la synchronisation du
    # catalogue de la version précédente), tenants rebasculés, nouveaux profils retirés.
    for old, name in LEGACY_NAMES.items():
        bind.execute(
            sa.text(
                "INSERT INTO business_profiles (code, name, is_active, navigation, terminology, "
                "settings) VALUES (:old, :name, true, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb) "
                "ON CONFLICT (code) DO UPDATE SET is_active = true"
            ),
            {"old": old, "name": name},
        )
    for old, new in RENAMED.items():
        _update_tenants(
            "UPDATE tenants SET business_profile_code = :old WHERE business_profile_code = :new",
            {"old": old, "new": new},
        )
    _update_tenants(
        "UPDATE tenants SET business_profile_code = CASE WHEN business_profile_code LIKE "
        "'restaurant.%' THEN 'restaurant' ELSE 'commerce_general' END "
        "WHERE business_profile_code LIKE '%.%'",
        {},
    )
    op.execute("DELETE FROM business_profile_modules WHERE profile_code LIKE '%.%'")
    op.execute("DELETE FROM business_profiles WHERE code LIKE '%.%'")

    op.drop_constraint(
        op.f("fk_business_profiles_ux_profile_code_ux_profiles"),
        "business_profiles",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_business_profiles_sector_code_business_sectors"),
        "business_profiles",
        type_="foreignkey",
    )
    op.drop_column("business_profiles", "theme")
    op.drop_column("business_profiles", "dashboard")
    op.drop_column("business_profiles", "sort_order")
    op.drop_column("business_profiles", "ux_profile_code")
    op.drop_column("business_profiles", "sector_code")
    op.drop_table("ux_profiles")
    op.drop_table("business_sectors")
