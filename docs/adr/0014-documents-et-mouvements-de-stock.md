# ADR-0014 — Documents de stock, mouvements et état des niveaux

- **Statut** : Proposée (Phase 2.2, à valider)
- **Date** : 2026-09-24

## Contexte

La sous-phase 2.2 réalise l'ADR-0004 (service central de stock) pour les premiers
documents : entrées (achat, stock initial) et sorties. D'autres documents viendront
(inventaires, transferts, ventes) et devront produire des mouvements par le même service.
Plusieurs choix de conception, non tranchés par les décisions Q1 à Q7, engagent ces modules.

## Décision

1. **Source polymorphe des mouvements** : chaque mouvement référence son document par
   `source_type` (`stock_entry`, `stock_exit`, puis `inventory_count`, `sale`…),
   `source_id` et `source_line_id`, **sans clé étrangère**. L'unicité
   `(tenant_id, source_line_id, movement_type)` garantit qu'une ligne de document n'est
   appliquée qu'une fois par type de mouvement (validation, puis annulation).
   L'annulation référence le mouvement d'origine (`origin_movement_id`, FK composite).
2. **Cycle de vie des documents** : `DRAFT` → `VALIDATED` → `CANCELLED`. Le brouillon
   (numéroté dès sa création) n'a aucun effet sur le stock ; ses lignes sont remplacées en
   bloc. La validation et l'annulation verrouillent le document puis contrôlent son statut
   (double validation concurrente : `409`). Une annulation crée des mouvements inverses
   (`CANCELLATION`) et ne recalcule jamais le CMUP ; elle est refusée si le stock
   deviendrait négatif.
3. **Moment des contrôles** : article et motif actifs sont contrôlés à la saisie du
   brouillon ; l'article est **recontrôlé à la validation** (désactivé entre-temps : refus) ;
   le stock suffisant n'est contrôlé **qu'à la validation**, au moment où il change (la
   saisie d'un brouillon reste possible quel que soit le stock). Un motif désactivé après la
   saisie n'empêche pas la validation du brouillon.
4. **Un article au plus une fois par document** (`duplicate_article_line`) : un
   mouvement par ligne, lecture simple des documents.
5. **État d'un niveau** calculé à la lecture (jamais stocké) : `not_stocked` (aucune
   ligne `stock_levels` pour le site), `out` (stock nul), `low` (0 < stock ≤ minimum
   effectif), `ok`. **Alertes** = `out` + `low` des articles actifs. Un article jamais
   géré sur un site ne déclenche pas d'alerte de rupture sur ce site. Définir un seuil
   pour un article sur un site crée son niveau (stock nul) : l'article devient géré sur ce
   site et apparaît en rupture tant qu'il n'est pas approvisionné.
6. **Interface publique** : les autres modules lisent le stock par `modules/stock/api.py`
   (`list_levels`, `count_alerts`…) ; `alerts` n'importe aucun modèle du stock.

## Conséquences

- Nouveaux documents (inventaire, transfert, vente) : un `source_type` de plus, aucune
  migration du journal ; le transfert produira `TRANSFER_OUT` au CMUP du site source puis
  `TRANSFER_IN` (types déjà réservés).
- Pas d'intégrité référentielle en base entre un mouvement et son document : compensée
  par l'écriture exclusive via `StockService`, dans la transaction du document, et par
  l'unicité anti double application.
- Les alertes ne coûtent aucune écriture ; à fort volume, un index ou une vue matérialisée
  pourra être ajouté sans changer l'API.

## Alternatives écartées

- **Une colonne FK par type de document** dans `stock_movements` : migration et
  colonne nullable à chaque nouveau document.
- **Stock vérifié dès l'enregistrement du brouillon** : faux sentiment de garantie
  (le stock change entre la saisie et la validation).
- **Alerte de rupture pour tout article actif non stocké sur un site** : bruit sur les
  tenants multi-sites dont les sites ne vendent pas tous les mêmes articles.
