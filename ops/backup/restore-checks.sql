-- Invariants métier contrôlés sur la COPIE restaurée par sm-restore-test.sh (jamais sur la
-- production). Chaque requête renvoie « libellé|nombre d'écarts » ; tout nombre > 0 = échec.
-- Les contraintes déclarées (clés étrangères, CHECK, unicité) sont contrôlées génériquement par
-- le script ; ce fichier ajoute les invariants qui ne sont pas des contraintes SQL.

-- Stock : niveau = somme des mouvements (append-only, StockService seul).
SELECT 'stock_level_equals_movements', count(*)
FROM stock_levels l
LEFT JOIN (SELECT tenant_id, site_id, article_id, sum(quantity) AS q
           FROM stock_movements GROUP BY 1, 2, 3) m
  ON m.tenant_id = l.tenant_id AND m.site_id = l.site_id AND m.article_id = l.article_id
WHERE l.quantity <> coalesce(m.q, 0);

-- Stock : chaque mouvement est arithmétiquement cohérent.
SELECT 'movement_before_plus_quantity_equals_after', count(*)
FROM stock_movements WHERE quantity_before + quantity <> quantity_after;

-- Lots : somme des soldes par lot = stock de l'article suivi par lot (ADR-0045).
SELECT 'lot_balances_equal_stock', count(*)
FROM stock_levels l
JOIN catalog_articles a ON a.tenant_id = l.tenant_id AND a.id = l.article_id AND a.lot_tracked
LEFT JOIN (SELECT tenant_id, site_id, article_id, sum(quantity) AS q
           FROM stock_lot_levels GROUP BY 1, 2, 3) s
  ON s.tenant_id = l.tenant_id AND s.site_id = l.site_id AND s.article_id = l.article_id
WHERE l.quantity <> coalesce(s.q, 0);

-- Isolation : aucun niveau de stock rattaché à un site d'une autre entreprise.
SELECT 'stock_level_site_same_tenant', count(*)
FROM stock_levels l JOIN sites s ON s.id = l.site_id WHERE s.tenant_id <> l.tenant_id;
