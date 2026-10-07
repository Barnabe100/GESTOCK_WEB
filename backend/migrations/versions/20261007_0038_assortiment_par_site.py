"""Recette, étape 1 — assortiment par site (ADR-0046, décisions D1 à D6).

- ``catalog_site_articles`` : assortiment d'un site = articles du catalogue GLOBAL du tenant
  proposés sur ce site. Distinct du catalogue et du stock (``stock_levels``). Une ligne par
  (tenant, site, article) ; retrait = ``is_active`` faux (jamais supprimée, D1, D4) ;
  ``added_by`` nul = association reprise par cette migration. FK composites : site et article
  du même tenant.
- Reprise (D2, option A — USAGE RÉEL SEULEMENT) : un couple (site, article) est repris s'il a un
  niveau de stock, un mouvement, une ligne de vente, une ligne de brouillon d'entrée ou de
  sortie, une ligne de brouillon de transfert (source ET destination), une ligne d'inventaire non
  clôturé ou un emplacement courant. Aucun article sans usage, aucun ajout « partout ».

RLS ``ENABLE`` + ``FORCE`` sur la nouvelle table ; droits ``SELECT, INSERT, UPDATE`` pour le rôle
applicatif (aucune suppression), aucun droit pour le rôle de la console. Les tables sources sont
lues par le propriétaire avec ``FORCE`` levé dans cette transaction (procédé des migrations
0004, 0029…). Retour arrière : refusé si une association a été ajoutée, réactivée ou retirée par
un utilisateur depuis la migration (choix perdus) ; sinon la table est supprimée (les
associations reprises se reconstituent à partir de l'usage).

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-07
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "catalog_site_articles"
LOG = logging.getLogger("alembic.runtime.migration")

# Sources de l'usage réel (D2) : (tenant, site, article).
USAGE_SOURCES = (
    "SELECT tenant_id, site_id, article_id FROM stock_levels",
    "SELECT tenant_id, site_id, article_id FROM stock_movements",
    "SELECT s.tenant_id, s.site_id, l.article_id FROM sale_lines l "
    "JOIN sales s ON s.tenant_id = l.tenant_id AND s.id = l.sale_id",
    "SELECT d.tenant_id, d.site_id, l.article_id FROM stock_entry_lines l "
    "JOIN stock_entries d ON d.tenant_id = l.tenant_id AND d.id = l.entry_id "
    "WHERE d.status = 'DRAFT'",
    "SELECT d.tenant_id, d.site_id, l.article_id FROM stock_exit_lines l "
    "JOIN stock_exits d ON d.tenant_id = l.tenant_id AND d.id = l.exit_id "
    "WHERE d.status = 'DRAFT'",
    "SELECT d.tenant_id, d.source_site_id, l.article_id FROM stock_transfer_lines l "
    "JOIN stock_transfers d ON d.tenant_id = l.tenant_id AND d.id = l.transfer_id "
    "WHERE d.status = 'DRAFT'",
    "SELECT d.tenant_id, d.destination_site_id, l.article_id FROM stock_transfer_lines l "
    "JOIN stock_transfers d ON d.tenant_id = l.tenant_id AND d.id = l.transfer_id "
    "WHERE d.status = 'DRAFT'",
    "SELECT d.tenant_id, d.site_id, l.article_id FROM inventory_lines l "
    "JOIN inventories d ON d.tenant_id = l.tenant_id AND d.id = l.inventory_id "
    "WHERE d.status IN ('DRAFT', 'COUNTING', 'READY_TO_VALIDATE')",
    "SELECT tenant_id, site_id, article_id FROM stock_article_locations",
)
SOURCE_TABLES = (
    "stock_levels",
    "stock_movements",
    "sales",
    "sale_lines",
    "stock_entries",
    "stock_entry_lines",
    "stock_exits",
    "stock_exit_lines",
    "stock_transfers",
    "stock_transfer_lines",
    "inventories",
    "inventory_lines",
    "stock_article_locations",
)


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "is_active = (removed_at IS NULL)",
            name=op.f("ck_catalog_site_articles_active_not_removed"),
        ),
        sa.CheckConstraint(
            "removed_at IS NOT NULL OR removed_by IS NULL",
            name=op.f("ck_catalog_site_articles_removed_by_dated"),
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], name=op.f("fk_catalog_site_articles_added_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["removed_by"], ["users.id"], name=op.f("fk_catalog_site_articles_removed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_catalog_site_articles_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["sites.tenant_id", "sites.id"],
            name=op.f("fk_catalog_site_articles_tenant_id_site_id_sites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_catalog_site_articles_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_site_articles")),
        sa.UniqueConstraint(
            "tenant_id",
            "site_id",
            "article_id",
            name=op.f("uq_catalog_site_articles_tenant_id_site_id_article_id"),
        ),
    )
    op.create_index(
        "ix_catalog_site_articles_tenant_article", TABLE, ["tenant_id", "article_id"], unique=False
    )
    op.create_index(op.f("ix_catalog_site_articles_tenant_id"), TABLE, ["tenant_id"], unique=False)

    # Reprise de l'usage réel (D2, option A), avant l'activation de la RLS sur la table.
    for source in SOURCE_TABLES:
        op.execute(f"ALTER TABLE {source} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"INSERT INTO {TABLE} (id, tenant_id, site_id, article_id, is_active) "
        "SELECT gen_random_uuid(), u.tenant_id, u.site_id, u.article_id, true "
        f"FROM ({' UNION '.join(USAGE_SOURCES)}) AS u (tenant_id, site_id, article_id) "
        "ON CONFLICT (tenant_id, site_id, article_id) DO NOTHING"
    )
    for source in SOURCE_TABLES:
        op.execute(f"ALTER TABLE {source} FORCE ROW LEVEL SECURITY")
    for tenant_id, count in op.get_bind().execute(
        sa.text(f"SELECT tenant_id, count(*) FROM {TABLE} GROUP BY tenant_id ORDER BY tenant_id")
    ):
        LOG.info("Assortiment repris : tenant %s — %s couple(s) (site, article)", tenant_id, count)

    role = _app_role()
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    # Jamais de suppression : un retrait est une désactivation (D1, D4).
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {TABLE} TO {role}")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    changed = bind.execute(
        sa.text(
            f"SELECT count(*) FROM {TABLE} "
            "WHERE added_by IS NOT NULL OR removed_at IS NOT NULL OR NOT is_active"
        )
    ).scalar_one()
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    if changed:
        raise RuntimeError(
            f"Retour arrière refusé : {changed} association(s) d'assortiment choisie(s) par les "
            "utilisateurs (ajout, réactivation ou retrait) seraient perdues."
        )
    op.drop_index(op.f("ix_catalog_site_articles_tenant_id"), table_name=TABLE)
    op.drop_index("ix_catalog_site_articles_tenant_article", table_name=TABLE)
    op.drop_table(TABLE)
