# ADR-0042 — Codes-barres multiples et codes-barres des conditionnements

- **Statut** : Acceptée et validée (Lot 3-D — catalogue, scan des écrans opérationnels)
- **Date** : 2026-10-01
- **Prolonge** : [ADR-0039](0039-catalogue-stock-gere-prix-couts.md) (scan exact),
  [ADR-0040](0040-quantites-decimales-conditionnements.md) et
  [ADR-0041](0041-presentations-operations-de-stock.md) (présentations)

## Contexte

Avant ce lot, un article n'avait qu'un code-barres (`catalog_articles.barcode`, unique parmi les
articles actifs du tenant) et le scan, au point de vente seulement, ajoutait toujours l'article
dans son unité de base. Or un même produit porte souvent plusieurs codes (EAN, code fournisseur,
code interne) et chaque conditionnement a les siens (EAN du carton, du pack) : scanner un carton
doit ajouter **un carton**, pas une bouteille ni 24 bouteilles. Le Desktop (référence) n'a qu'un
code par article, sans conditionnement ; le scan y existe dans les ventes et les inventaires.

## Décision

1. **Principe** : un code-barres identifie **une présentation** précise d'un article — l'article
   en unité de base, ou l'article + un conditionnement.
2. **Modèle** : registre relationnel unique `catalog_barcodes` (aucune colonne `barcode_2`…) :
   `article_id`, `packaging_id` (nul = unité de base), `code` (50 caractères, texte libre),
   `kind` (`PRIMARY` / `ADDITIONAL` / `PACKAGING`), `is_active` (état de l'élément porteur).
   - **Code principal** : le champ existant `catalog_articles.barcode` est conservé (API, recherche
     et scans inchangés) ; sa ligne `PRIMARY` du registre en est le **miroir**, tenu par
     déclencheur dans la même instruction (création, modification, retrait).
   - **Codes supplémentaires** (`ADDITIONAL`) et **codes de conditionnement** (`PACKAGING`) :
     nombre libre par élément ; FK composite `(tenant_id, article_id, packaging_id)` → un code de
     conditionnement appartient toujours à l'article de ce conditionnement.
   - Reprise (migration 0030) : chaque code actuel devient le code principal, sans perte.
3. **Unicité commune au tenant** parmi les présentations ACTIVES : index unique partiel
   `(tenant_id, code) WHERE is_active` — un code ne désigne jamais deux articles, un article et
   un conditionnement, ni deux conditionnements. Contrôle du service (message explicite, codes
   en cause) **et** garantie en base (concurrence comprise). Deux tenants peuvent partager un
   code (RLS).
4. **Éléments inactifs** (règle validée) : `is_active` est tenu par déclencheurs — codes en
   unité de base : article actif ; code d'un conditionnement : article **et** conditionnement
   actifs. Un article désactivé libère donc ses codes **et ceux de ses conditionnements** ; un
   conditionnement désactivé libère les siens. Les codes restent enregistrés (historique), ne
   sont plus reconnus au scan et ne bloquent aucun autre élément actif. La **réactivation**
   revérifie qu'ils sont toujours libres — article : ses codes et ceux de ses conditionnements
   actifs (`409 article_barcode_taken`, contrat existant) ; conditionnement d'un article actif :
   ses codes (`409 barcode_taken`) ; conditionnement d'un article inactif : rien n'est réservé —,
   avec la liste des codes en cause ; l'index protège la réactivation concurrente (migration
   0031 : déclencheurs et reprise des codes déjà enregistrés).
5. **Format** : texte libre, 50 caractères au plus, espaces de bord retirés ; aucune validation
   EAN-13 / EAN-8 imposée, aucune génération, pas de codes à poids variable ni de balance. Le
   lecteur est un périphérique qui se comporte comme un clavier (code puis Entrée).
6. **Scan exact** (`catalog.api.resolve_barcode`, seule résolution) : égalité stricte sur un code
   du registre porté par une présentation active (article actif ; conditionnement actif), jamais
   de recherche partielle, de premier résultat ni de repli sur la référence / la désignation.
   Inconnu : `404 barcode_unknown`.
   - `GET /catalog/barcodes/resolve?code=` (`catalog.article.view`) : article + conditionnement
     éventuel (écrans de vente et de stock).
   - Point de vente (`GET /pos/articles/by-barcode`) : `scanned_packaging_id` ; le panier ajoute
     **1 conditionnement** (présentation conservée, conversion par la logique du Lot 3-B).
     Conditionnement au prix non configuré : `422 packaging_price_not_set` (règle existante),
     rien n'est ajouté.
7. **Scan dans les écrans opérationnels** : POS et vente du back-office (ajout direct d'une
   présentation, quantité 1, incrémentée si la ligne existe ; prix non configuré refusé) ;
   entrées, sorties, transferts (article + présentation présélectionnés, quantité et coût à
   saisir ; article non géré en stock refusé) ; inventaires (ligne exacte de l'article —
   filtre `article_id` —, présentation présélectionnée, **quantité jamais devinée** ; article
   absent de l'inventaire signalé). Le scan ne contourne aucune validation existante.
8. **Recherche partielle** (« contient ») étendue à tous les codes (principal, supplémentaires,
   conditionnements) via `catalog.api.barcode_search` : catalogue, niveaux de stock, POS,
   candidats et lignes d'inventaire, filtre « référence article » des ventes. Elle ne remplace
   jamais le scan exact.
9. **Interface** : section « Codes-barres » de la fiche article — code principal (modifié avec
   l'article), codes supplémentaires, codes de chaque conditionnement (actifs et inactifs),
   ajout et retrait confirmé.
10. **Retrait** d'un code supplémentaire ou de conditionnement : suppression de la ligne du
    registre (aucune donnée métier ne la référence) ; l'ancienne valeur reste dans l'audit. Le
    code principal ne se retire que sur l'article (`422 barcode_primary`).
11. **Permissions** : aucune nouvelle — consultation `catalog.article.view`, gestion
    `catalog.article.update` (donnée générale du catalogue).
12. **Audit** (journal existant) : `article.barcode_added|barcode_removed`,
    `packaging.barcode_added|barcode_removed` (utilisateur, date, élément, `barcode` avant / après,
    nature) ; le code principal reste audité par `article.updated` (avant / après).

## Hors périmètre

Images, stockage S3, lots, péremption, FIFO / FEFO, promotions, taxes, retours, remboursements,
fournisseurs et commandes, étiquettes et leur impression, codes à poids variable, balances,
génération d'EAN, référentiel global d'unités.

## Conséquences

- Nouvelle table tenant-scoped `catalog_barcodes` (RLS `ENABLE` + `FORCE`, rôle applicatif :
  `SELECT`, `INSERT`, `DELETE`, `UPDATE (is_active, updated_at)`) ; contrainte unique
  `(tenant_id, article_id, id)` ajoutée à `catalog_packagings` (cible de la FK composite).
- Le retour arrière de la migration 0030 est refusé dès qu'un code supplémentaire ou de
  conditionnement existe (l'ancien schéma n'a qu'un code par article).
- **Décisions validées** (TechNova, 2026-10-01) : codes d'un conditionnement libérés par un
  article inactif et revérifiés à sa réactivation (point 4, migration 0031) ; retrait = ligne
  supprimée, ancienne valeur dans l'audit (point 10) ; code préparé sur un élément inactif :
  non reconnu au scan, disponibilité contrôlée à la réactivation, refusée si le code a été repris
  entre-temps ; comparaison **sensible à la casse** (`c-1` ≠ `C-1`), seuls les espaces de bord
  retirés, aucune normalisation ni validation EAN.
