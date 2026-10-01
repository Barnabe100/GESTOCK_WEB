# ADR-0041 — Conditionnements dans les opérations de stock

- **Statut** : Acceptée (Lot 3-C — entrées, sorties, transferts, inventaires)
- **Date** : 2026-10-01
- **Prolonge** : [ADR-0040](0040-quantites-decimales-conditionnements.md) (conditionnements de
  vente, quantités décimales)

## Contexte

Le Lot 3-B a introduit les conditionnements (`catalog_packagings` : Pack 6, Carton 24, Sac
25,5 kg) et la règle `decimal_quantity_allowed`, limitées aux ventes et au point de vente. Les
opérations de stock (entrées, sorties, transferts, inventaires) restaient saisies dans l'unité de
base seulement, sans contrôle des quantités entières. En réception, en magasin ou au comptage, on
manipule pourtant des cartons : « 10 cartons reçus », « 3 cartons cassés », « 8 cartons et 5
bouteilles comptés ».

## Décision

1. **Aucun second système** : les opérations de stock réutilisent les conditionnements du 3-B
   (même table, mêmes droits). Aucun référentiel global d'unités, aucune permission nouvelle :
   les permissions existantes de chaque opération (`stock.entry.*`, `stock.exit.*`,
   `stock.transfer.*`, `inventory_count.inventory.*`) s'appliquent telles quelles.
2. **Présentation par ligne** : une ligne d'entrée, de sortie ou de transfert porte sa
   présentation — unité de base (`packaging_id` nul) ou conditionnement **actif** de l'article
   (son prix n'intervient pas : un conditionnement au prix non configuré est utilisable en stock)
   — et la quantité dans cette présentation. Une ligne par présentation : le même article peut
   figurer en cartons et en unités sur un document.
3. **Le stock reste en unité de base.** Le serveur calcule `base_quantity = quantité ×
   conversion` (sans arrondi, refus au-delà de 3 décimales : `422 base_quantity_precision`) ;
   **aucune quantité de base envoyée par le client n'est reprise** (champ ignoré sur les lignes
   de document ; au comptage, `quantity_physical` combinée à un conditionnement → `422`). `StockService` ne reçoit que des quantités de base ; le contrôle « stock jamais
   négatif » s'applique sur elles.
4. **Règle décimale étendue au stock** (validation du Lot 3-B : « étendue au 3-C ») : un article
   sans `decimal_quantity_allowed` n'accepte que des quantités entières (dans la présentation
   saisie) en entrée, sortie, transfert et comptage (`422 quantity_not_whole`), contrôlées à
   l'enregistrement **et revérifiées à la validation**. Les inventaires restent tolérants pour
   la quantité **calculée** (8 × 24 + 5) mais pas pour les quantités saisies.
5. **Instantané figé** : chaque ligne conserve `packaging_id`, `packaging_name`,
   `packaging_conversion` et `base_quantity` (contrainte `base_quantity = quantity ×
   COALESCE(packaging_conversion, 1)`) ; chaque mouvement de stock conserve `packaging_id`,
   `packaging_name`, `packaging_conversion` et `packaging_quantity` (contrainte `|quantity| =
   packaging_quantity × packaging_conversion`). Le journal affiche « -3 Carton 24 → -72
   bouteille ». Les mouvements des ventes portent aussi la présentation de la ligne vendue ; les
   ajustements d'inventaire (écart calculé) n'en portent aucune.
6. **Revalidation à la validation** (sous verrou partagé `FOR SHARE` du conditionnement,
   mécanisme commun `catalog.api.check_packagings`) : conditionnement toujours actif
   (`422 packaging_inactive`), conversion identique à l'instantané (`409
   packaging_conversion_changed` — défense en profondeur, la conversion étant figée par
   l'usage), règle décimale. Un brouillon dont le conditionnement a été désactivé ne peut être
   validé qu'après correction de sa présentation.
7. **Conversion figée par tout usage** : le port `catalog.usage_port` (anciennement
   `sales_port`) agrège les fournisseurs d'usage déclarés par les modules (`sales`, `stock`,
   `inventory_count`) : dès qu'un brouillon de stock ou un comptage utilise un conditionnement,
   sa conversion est figée (`409 packaging_in_use`), comme pour une vente. Le catalogue ne
   dépend d'aucun de ces modules.
8. **Coûts** : en entrée, le coût unitaire est saisi **par présentation** (12 000 le carton) ;
   le mouvement porte le coût par unité de base (`unit_cost / conversion`, 4 décimales, comme le
   CMUP) et le montant de ligne = quantité saisie × coût saisi. Sorties et transferts : CMUP du
   site source (par unité de base) × quantité de base, inchangé.
9. **Inventaire** : un comptage se saisit soit en unité de base (`quantity_physical`), soit en
   conditionnement + vrac (`packaging_id`, `packaging_quantity`, `unit_quantity` : 8 cartons +
   5 bouteilles) ; le serveur calcule la quantité physique (197) — l'écart reste « physique −
   stock courant relu à la validation » (ADR-0019). Les lignes exposent les conditionnements
   actifs de l'article tant que l'inventaire est ouvert.
10. **Interface** : sélecteur de présentation par ligne (unité de base toujours proposée,
    conditionnements actifs) et **équivalences** calculées en décimal exact, indicatives :
    « 2 Carton 24 = 48 bouteille » pour une saisie en conditionnement, « 48 bouteille = 8 Pack 6
    = 2 Carton 24 » en unité de base (au plus 3 conditionnements, conversion croissante,
    seulement ceux dont le quotient est ≥ 1, reste éventuel en unités de base). Le serveur
    reste seul à calculer et enregistrer la quantité de base.
11. **Compatibilité** : migration 0029 ; les lignes existantes reçoivent `base_quantity =
    quantity` et aucune présentation ; les mouvements antérieurs restent affichés comme avant.
    La contrainte d'unicité `(document, article)` devient deux index partiels (unité de base /
    conditionnement). Le retour arrière est refusé dès qu'une opération de stock utilise un
    conditionnement (il perdrait l'information de présentation).

## Hors périmètre

Référentiel global d'unités, lots et péremption, FIFO / FEFO, règles de prix, achats et
commandes fournisseurs, codes-barres par conditionnement.

## Conséquences

- Les tables `stock_entry_lines`, `stock_exit_lines`, `stock_transfer_lines`,
  `stock_movements` et `inventory_lines` gagnent des colonnes nullables (RLS et droits
  inchangés : tables existantes).
- Les tests qui saisissaient des quantités décimales pour des articles entiers déclarent
  désormais `decimal_quantity_allowed`.
- Points soumis à validation : coût d'entrée saisi par présentation ; blocage de la validation
  d'un document dont le conditionnement a été désactivé ; conversion figée dès un brouillon de
  stock ou un comptage ; présentation des ventes reportée sur leurs mouvements.
