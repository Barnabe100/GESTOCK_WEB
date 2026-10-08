"""Profils / modules par site, palier A — profil d'activité porté par le SITE.

- ``sites.business_profile_code`` : profil d'activité du site (catalogue global
  ``business_profiles``), ``NOT NULL``, clé étrangère ``ON DELETE RESTRICT`` (un profil retiré du
  catalogue est désactivé, jamais supprimé).
- Reprise : chaque site existant reçoit le profil ACTUEL de son tenant. Aucun profil n'est déduit
  de ``sites.kind`` (nature physique du site, distincte du profil).
- ``tenants.business_profile_code`` est conservé (profil d'origine / d'inscription : l'inscription
  crée l'entreprise sans site).
- RLS : ``sites`` reste sous ``tenant_isolation`` (ENABLE + FORCE) ; la nouvelle colonne en
  hérite. Les droits du rôle applicatif portent sur la table entière (colonne comprise). Le rôle
  de la console reçoit la LECTURE de cette seule colonne (consultation du profil de chaque site,
  jamais sa modification). La reprise lit ``tenants`` et écrit ``sites`` en propriétaire avec
  ``FORCE`` levé dans cette transaction (procédé des migrations 0004, 0029, 0038…).
- Retour arrière : refusé si le profil d'un site diffère de celui de son tenant (information
  perdue) ; sinon la colonne est supprimée.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN = "business_profile_code"
FK_NAME = "fk_sites_business_profile_code_business_profiles"
INDEX_NAME = "ix_sites_business_profile_code"


def _platform_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_platform_role)


def _lift_force() -> None:
    op.execute("ALTER TABLE sites NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")


def _restore_force() -> None:
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sites FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.add_column("sites", sa.Column(COLUMN, sa.String(length=50), nullable=True))
    _lift_force()
    # Reprise : profil actuel du tenant, jamais déduit de ``sites.kind``.
    op.execute(
        f"UPDATE sites s SET {COLUMN} = t.business_profile_code "
        "FROM tenants t WHERE t.id = s.tenant_id"
    )
    _restore_force()
    op.alter_column("sites", COLUMN, nullable=False)
    op.create_foreign_key(
        op.f(FK_NAME),
        "sites",
        "business_profiles",
        [COLUMN],
        ["code"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f(INDEX_NAME), "sites", [COLUMN], unique=False)
    # Console TechNova : lecture du profil de chaque site (ADR-0031 : métadonnée, aucune
    # donnée métier ; aucune écriture).
    role = _platform_role()
    op.execute(f"GRANT SELECT ({COLUMN}) ON sites TO {role}")


def downgrade() -> None:
    bind = op.get_bind()
    _lift_force()
    diverging = bind.execute(
        sa.text(
            f"SELECT count(*) FROM sites s JOIN tenants t ON t.id = s.tenant_id "
            f"WHERE s.{COLUMN} <> t.business_profile_code"
        )
    ).scalar_one()
    _restore_force()
    if diverging:
        raise RuntimeError(
            f"Retour arrière refusé : {diverging} site(s) ont un profil d'activité différent de "
            "celui de leur entreprise ; ces profils seraient perdus."
        )
    role = _platform_role()
    op.execute(f"REVOKE SELECT ({COLUMN}) ON sites FROM {role}")
    op.drop_index(op.f(INDEX_NAME), table_name="sites")
    op.drop_constraint(op.f(FK_NAME), "sites", type_="foreignkey")
    op.drop_column("sites", COLUMN)
