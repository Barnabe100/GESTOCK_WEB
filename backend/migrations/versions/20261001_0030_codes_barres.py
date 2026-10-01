"""Lot 3-D — Codes-barres multiples et codes-barres des conditionnements (ADR-0042).

- ``catalog_barcodes`` : registre unique des codes-barres du tenant. Chaque code identifie UNE
  présentation : l'article en unité de base (``PRIMARY`` = miroir de
  ``catalog_articles.barcode``, ``ADDITIONAL`` = codes supplémentaires) ou un conditionnement
  (``PACKAGING``, FK composite garantissant qu'il appartient à l'article). ``is_active`` reflète
  l'état de l'élément porteur ; index unique partiel ``(tenant_id, code) WHERE is_active`` :
  un code ne désigne qu'une présentation active (deux tenants peuvent partager un code) ; un
  élément désactivé libère ses codes, qui restent enregistrés.
- Déclencheurs : le code principal de l'article (colonne conservée, API inchangée) et l'état
  de l'article / du conditionnement sont reportés dans le registre, dans la même instruction —
  la contrainte d'unicité s'applique donc aussi au code principal et aux réactivations.
- Reprise : un code ``PRIMARY`` par article ayant un code-barres (aucune perte, aucun conflit
  possible : l'unicité parmi les articles actifs existait déjà).
- RLS ``ENABLE`` + ``FORCE`` ; rôle applicatif : lecture, insertion, mise à jour de l'état,
  suppression (retrait d'un code, audité).

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _app_role() -> str:
    return op.get_bind().dialect.identifier_preparer.quote(get_settings().db_app_role)


ARTICLE_SYNC = """
CREATE FUNCTION catalog_article_barcodes_sync() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Article (dés)activé : ses codes (principal et supplémentaires) suivent son état.
    IF TG_OP = 'UPDATE' AND NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        UPDATE catalog_barcodes SET is_active = NEW.is_active, updated_at = now()
         WHERE tenant_id = NEW.tenant_id AND article_id = NEW.id AND packaging_id IS NULL;
    END IF;
    -- Code principal créé, modifié ou retiré : miroir dans le registre.
    IF TG_OP = 'INSERT' OR NEW.barcode IS DISTINCT FROM OLD.barcode THEN
        DELETE FROM catalog_barcodes
         WHERE tenant_id = NEW.tenant_id AND article_id = NEW.id AND kind = 'PRIMARY';
        IF NEW.barcode IS NOT NULL AND btrim(NEW.barcode) <> '' THEN
            INSERT INTO catalog_barcodes
                (id, tenant_id, article_id, packaging_id, code, kind, is_active,
                 created_at, updated_at)
            VALUES (gen_random_uuid(), NEW.tenant_id, NEW.id, NULL, btrim(NEW.barcode),
                    'PRIMARY', NEW.is_active, now(), now());
        END IF;
    END IF;
    RETURN NULL;
END;
$$
"""

PACKAGING_SYNC = """
CREATE FUNCTION catalog_packaging_barcodes_sync() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE catalog_barcodes SET is_active = NEW.is_active, updated_at = now()
     WHERE tenant_id = NEW.tenant_id AND packaging_id = NEW.id;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_catalog_packagings_tenant_id_article_id_id"),
        "catalog_packagings",
        ["tenant_id", "article_id", "id"],
    )
    op.create_table(
        "catalog_barcodes",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("packaging_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            "(kind = 'PACKAGING') = (packaging_id IS NOT NULL)",
            name=op.f("ck_catalog_barcodes_packaging_consistent"),
        ),
        sa.CheckConstraint(
            "code = btrim(code) AND code <> ''", name=op.f("ck_catalog_barcodes_code_not_blank")
        ),
        sa.CheckConstraint(
            "kind IN ('PRIMARY', 'ADDITIONAL', 'PACKAGING')",
            name=op.f("ck_catalog_barcodes_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id", "packaging_id"],
            [
                "catalog_packagings.tenant_id",
                "catalog_packagings.article_id",
                "catalog_packagings.id",
            ],
            name=op.f("fk_catalog_barcodes_tenant_id_article_id_packaging_id_catalog_packagings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "article_id"],
            ["catalog_articles.tenant_id", "catalog_articles.id"],
            name=op.f("fk_catalog_barcodes_tenant_id_article_id_catalog_articles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_catalog_barcodes_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_barcodes")),
    )
    op.create_index(
        op.f("ix_catalog_barcodes_article_id"), "catalog_barcodes", ["article_id"], unique=False
    )
    op.create_index(
        op.f("ix_catalog_barcodes_packaging_id"), "catalog_barcodes", ["packaging_id"], unique=False
    )
    op.create_index(
        op.f("ix_catalog_barcodes_tenant_id"), "catalog_barcodes", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_catalog_barcodes_article_primary",
        "catalog_barcodes",
        ["tenant_id", "article_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'PRIMARY'"),
    )
    op.create_index(
        "uq_catalog_barcodes_tenant_code_active",
        "catalog_barcodes",
        ["tenant_id", "code"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    # Reprise (propriétaire ; FORCE levé dans cette transaction) : code actuel = code principal.
    op.execute("ALTER TABLE catalog_articles NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "INSERT INTO catalog_barcodes "
        "(id, tenant_id, article_id, packaging_id, code, kind, is_active, created_at, updated_at) "
        "SELECT gen_random_uuid(), tenant_id, id, NULL, btrim(barcode), 'PRIMARY', is_active, "
        "now(), now() FROM catalog_articles "
        "WHERE barcode IS NOT NULL AND btrim(barcode) <> ''"
    )
    op.execute("ALTER TABLE catalog_articles FORCE ROW LEVEL SECURITY")

    op.execute(ARTICLE_SYNC)
    op.execute(
        "CREATE TRIGGER catalog_article_barcodes_sync "
        "AFTER INSERT OR UPDATE OF barcode, is_active ON catalog_articles "
        "FOR EACH ROW EXECUTE FUNCTION catalog_article_barcodes_sync()"
    )
    op.execute(PACKAGING_SYNC)
    op.execute(
        "CREATE TRIGGER catalog_packaging_barcodes_sync "
        "AFTER UPDATE OF is_active ON catalog_packagings FOR EACH ROW "
        "WHEN (NEW.is_active IS DISTINCT FROM OLD.is_active) "
        "EXECUTE FUNCTION catalog_packaging_barcodes_sync()"
    )

    role = _app_role()
    op.execute("ALTER TABLE catalog_barcodes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE catalog_barcodes FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON catalog_barcodes TO {role} "
        "USING (tenant_id = app_current_tenant_id()) "
        "WITH CHECK (tenant_id = app_current_tenant_id())"
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON catalog_barcodes TO {role}")
    op.execute(f"GRANT UPDATE (is_active, updated_at) ON catalog_barcodes TO {role}")


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("ALTER TABLE catalog_barcodes NO FORCE ROW LEVEL SECURITY")
    extra = bind.execute(
        sa.text("SELECT count(*) FROM catalog_barcodes WHERE kind <> 'PRIMARY'")
    ).scalar_one()
    op.execute("ALTER TABLE catalog_barcodes FORCE ROW LEVEL SECURITY")
    if extra:
        raise RuntimeError(
            f"Retour arrière impossible : {extra} code(s)-barres supplémentaire(s) ou de "
            "conditionnement seraient perdus (l'ancien schéma n'a qu'un code par article)."
        )
    op.execute("DROP TRIGGER IF EXISTS catalog_packaging_barcodes_sync ON catalog_packagings")
    op.execute("DROP FUNCTION IF EXISTS catalog_packaging_barcodes_sync()")
    op.execute("DROP TRIGGER IF EXISTS catalog_article_barcodes_sync ON catalog_articles")
    op.execute("DROP FUNCTION IF EXISTS catalog_article_barcodes_sync()")
    op.execute(f"REVOKE ALL ON catalog_barcodes FROM {_app_role()}")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON catalog_barcodes")
    op.drop_index(
        "uq_catalog_barcodes_tenant_code_active",
        table_name="catalog_barcodes",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(
        "uq_catalog_barcodes_article_primary",
        table_name="catalog_barcodes",
        postgresql_where=sa.text("kind = 'PRIMARY'"),
    )
    op.drop_index(op.f("ix_catalog_barcodes_tenant_id"), table_name="catalog_barcodes")
    op.drop_index(op.f("ix_catalog_barcodes_packaging_id"), table_name="catalog_barcodes")
    op.drop_index(op.f("ix_catalog_barcodes_article_id"), table_name="catalog_barcodes")
    op.drop_table("catalog_barcodes")
    op.drop_constraint(
        op.f("uq_catalog_packagings_tenant_id_article_id_id"), "catalog_packagings", type_="unique"
    )
