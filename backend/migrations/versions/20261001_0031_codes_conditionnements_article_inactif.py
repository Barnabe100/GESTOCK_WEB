"""Lot 3-D (validation) — codes des conditionnements libérés par un article inactif (ADR-0042).

Règle validée : un code de conditionnement n'est réservé (et reconnu au scan) que si l'article ET
le conditionnement sont actifs. ``catalog_barcodes.is_active`` d'un code ``PACKAGING`` vaut
désormais « article actif ET conditionnement actif » :

- déclencheur de l'article : sa (dés)activation met à jour TOUS ses codes (unité de base et
  conditionnements, ces derniers selon l'état de leur conditionnement) ;
- déclencheur du conditionnement : son état combiné à celui de son article ;
- reprise : codes de conditionnement des articles inactifs libérés.

Aucun changement de schéma. Retour arrière : déclencheurs du Lot 3-D d'origine (0030) et état
des codes de conditionnement ramené à celui du seul conditionnement.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARTICLE_SYNC = """
CREATE OR REPLACE FUNCTION catalog_article_barcodes_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Article (dés)activé : tous ses codes suivent ; ceux d'un conditionnement restent libérés
    -- tant que ce conditionnement est inactif.
    IF TG_OP = 'UPDATE' AND NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        UPDATE catalog_barcodes b
           SET is_active = NEW.is_active AND COALESCE(
                   (SELECT p.is_active FROM catalog_packagings p
                     WHERE p.tenant_id = b.tenant_id AND p.id = b.packaging_id), true),
               updated_at = now()
         WHERE b.tenant_id = NEW.tenant_id AND b.article_id = NEW.id;
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
CREATE OR REPLACE FUNCTION catalog_packaging_barcodes_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE catalog_barcodes
       SET is_active = NEW.is_active AND COALESCE(
               (SELECT a.is_active FROM catalog_articles a
                 WHERE a.tenant_id = NEW.tenant_id AND a.id = NEW.article_id), false),
           updated_at = now()
     WHERE tenant_id = NEW.tenant_id AND packaging_id = NEW.id;
    RETURN NULL;
END;
$$
"""

ARTICLE_SYNC_0030 = """
CREATE OR REPLACE FUNCTION catalog_article_barcodes_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.is_active IS DISTINCT FROM OLD.is_active THEN
        UPDATE catalog_barcodes SET is_active = NEW.is_active, updated_at = now()
         WHERE tenant_id = NEW.tenant_id AND article_id = NEW.id AND packaging_id IS NULL;
    END IF;
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

PACKAGING_SYNC_0030 = """
CREATE OR REPLACE FUNCTION catalog_packaging_barcodes_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE catalog_barcodes SET is_active = NEW.is_active, updated_at = now()
     WHERE tenant_id = NEW.tenant_id AND packaging_id = NEW.id;
    RETURN NULL;
END;
$$
"""

# Reprise (propriétaire ; FORCE levé dans cette transaction). Libérer un code ne peut créer
# aucun conflit ; l'inverse (retour arrière) peut en créer : voir ``downgrade``.
REALIGN = (
    "UPDATE catalog_barcodes b SET is_active = p.is_active{article}, updated_at = now() "
    "FROM catalog_packagings p JOIN catalog_articles a "
    "ON a.tenant_id = p.tenant_id AND a.id = p.article_id "
    "WHERE b.tenant_id = p.tenant_id AND b.packaging_id = p.id "
    "AND b.is_active IS DISTINCT FROM (p.is_active{article})"
)
TABLES = ("catalog_barcodes", "catalog_packagings", "catalog_articles")


def _realign(with_article: bool) -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(REALIGN.format(article=" AND a.is_active" if with_article else ""))
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.execute(ARTICLE_SYNC)
    op.execute(PACKAGING_SYNC)
    _realign(with_article=True)


def downgrade() -> None:
    op.execute(ARTICLE_SYNC_0030)
    op.execute(PACKAGING_SYNC_0030)
    # Codes de conditionnement des articles inactifs de nouveau réservés (règle 0030) : un
    # conflit éventuel avec un code repris entre-temps fait échouer le retour arrière (index).
    _realign(with_article=False)
