# ADR-0040 — Quantités décimales et conditionnements de vente

- **Statut** : Acceptée (Lot 3-B — catalogue, vente, point de vente)
- **Date** : 2026-10-01

## Contexte

Avant ce lot, toute quantité vendue pouvait être décimale (aucune règle par article) et un article
ne se vendait que dans son unité (texte libre `unit`). Un commerce vend pourtant :

- des articles à la **pièce** (jamais 2,5 bouteilles) et d'autres au **poids / volume / longueur**
  (2,5 kg, 3,75 m, 0,5 L) ;
- le même article sous plusieurs **présentations** : la pièce, le pack de 6, le carton de 24, le
  sac de 25,5 kg — chacune à son prix, le stock restant suivi dans l'unité de base.

## Décision

1. **Unité de base** : le champ libre `unit` de l'article (aucun référentiel global d'unités).
   Elle est **toujours vendable** et le **stock est toujours tenu dans cette unité**.
2. **`catalog_articles.decimal_quantity_allowed`** (`BOOLEAN NOT NULL DEFAULT false`, articles
   existants compris) — champ général (`catalog.article.update`), audité :
   - `false` : quantités vendues **entières** (1, 2, 10 ; 2,5 et 0,5 refusés,
     `422 quantity_not_whole`) ;
   - `true` : quantités décimales (3 décimales au plus, comme toutes les quantités).
   - Contrôle **serveur** à l'enregistrement d'une vente (back-office, point de vente) et
     **revérifié à la validation** ; l'interface ne fait que guider.
   - Passage à `false` refusé tant qu'un conditionnement ACTIF de l'article a une conversion
     décimale (`409 article_has_fractional_packagings`).
   - Périmètre : les ventes. Entrées, sorties, transferts et inventaires conservent leurs règles
     (quantités à 3 décimales) — point soumis à validation ; **étendu au stock par le Lot 3-C**
     ([ADR-0041](0041-presentations-operations-de-stock.md)).
3. **Conditionnements** (`catalog_packagings`, par article, aucune liste globale) : nom libre
   (unique parmi les conditionnements ACTIFS de l'article, insensible à la casse), **conversion**
   vers l'unité de base `NUMERIC(18,3) > 0` (décimale possible : 1 sac = 25,5 kg ; **entière**
   pour un article sans quantités décimales, `422 packaging_conversion_not_whole`), **prix de
   vente propre** (aucune cohérence imposée avec prix de base × conversion), actif / inactif.
   - **Jamais supprimé** (aucune route ni droit SQL `DELETE`) : désactivé.
   - **Conversion figée** dès qu'une vente (brouillon compris) utilise le conditionnement
     (`409 packaging_in_use`) : désactiver puis créer un nouveau conditionnement. Nom, prix et
     état restent modifiables ; le prix d'une vente passée reste celui figé sur sa ligne.
   - **Droits** (aucune permission nouvelle, mêmes règles que l'article, ADR-0039) : consultation
     `catalog.article.view` ; création, nom, conversion, activation / désactivation
     `catalog.article.update` ; prix `catalog.article.price_update`. Valeur inchangée renvoyée
     par le formulaire acceptée.
   - **Prix non configuré ≠ prix 0** (décision de validation) : `sale_price` nul = prix NON
     CONFIGURÉ (création sans `price_update`, ou sans prix saisi) ; `0` = prix réellement
     configuré à zéro (fixé par un habilité). Fixer un prix, même 0, exige `price_update`
     (`403 price_update_not_allowed`) ; un prix ne redevient jamais « non configuré ». Un
     conditionnement au prix non configuré est **invendable** : absent du point de vente,
     signalé « Prix non configuré » (fiche article, back-office : non sélectionnable), et refusé
     par le serveur à l'enregistrement comme à la validation (`422 packaging_price_not_set`) —
     migration 0028.
   - Le catalogue ne dépend pas des ventes : l'usage d'un conditionnement est lu par un **port**
     (`catalog.sales_port`, devenu `catalog.usage_port` au Lot 3-C, ADR-0041) que le module Ventes implémente (même principe que
     `catalog.stock_port`). Audit : `packaging.created|updated|activated|deactivated`.
4. **Vente** : chaque ligne porte sa **présentation** — unité de base (`packaging_id` nul) ou
   conditionnement — et la quantité dans cette présentation. Le serveur :
   - relit l'article (existant, actif, `stock_managed`, règle décimale) et le conditionnement
     (du tenant, **de cet article**, actif) sous **verrou partagé** (`FOR SHARE`) ;
   - calcule la **quantité de base = quantité × conversion, sans arrondi** ; au-delà de 3
     décimales (précision du stock) la ligne est refusée (`422 base_quantity_precision`), jamais
     arrondie ; article entier : quantité vendue ET quantité de base entières ;
   - prend le prix du conditionnement (ou de l'article), calcule le montant ;
   - **fige** sur la ligne : `packaging_id`, `packaging_name`, `packaging_conversion`,
     `unit_price`, `base_quantity` (contrainte `base_quantity = quantity × COALESCE(conversion,
     1)` en base) — la vente historique reste fidèle (désactivation, nouveau prix, renommage ou
     nouveau conditionnement sans effet) ;
   - une ligne par présentation : le même article peut figurer en unité de base ET en
     conditionnement(s) (unicité `(vente, article)` sans conditionnement, `(vente,
     conditionnement)` sinon ; doublon : `422 duplicate_article_line`).
5. **Validation** : tout est relu (article, conditionnement, état, conversion, prix, règle
   décimale, stock). Conditionnement désactivé : `422 packaging_inactive`. Prix (article ou
   conditionnement) modifié depuis le brouillon : le mécanisme existant `409
   sale_prices_changed` (aucun second mécanisme). Le stock sort **en unité de base** (2 cartons
   de 24 → −48) via `StockService` ; l'annulation y remet la quantité de base. Article non géré
   en stock : aucun contrôle ni mouvement, conditionnements utilisables.
6. **Point de vente** : `GET /pos/articles` renvoie la règle décimale et les **seuls**
   conditionnements actifs ; le panier garde article, conditionnement, quantité, prix appliqué
   et quantité de base (indicatifs, décimaux exacts, aucune réservation de stock) ; changer de
   présentation recalcule prix, quantité de base, total et disponibilité. Le reçu (80 mm)
   affiche la présentation vendue : « Coca-Cola — 2 Carton 24 × 10 500 F … 21 000 F ».
7. **Concurrence** : modifier la conversion prend le verrou **exclusif** du conditionnement puis
   vérifie l'usage ; une vente prend le verrou **partagé** — l'un attend l'autre : jamais de
   vente à une conversion différente de celle du conditionnement ; plusieurs ventes simultanées
   ne s'attendent pas entre elles.
8. **Multi-tenant** : `catalog_packagings` RLS `ENABLE` + `FORCE`, FK composites `(tenant_id,
   article_id)` et `(tenant_id, packaging_id)` depuis `sale_lines` ; rôle applicatif : `SELECT`,
   `INSERT`, `UPDATE (name, conversion, sale_price, is_active, updated_at)` seulement.

## Conséquences

- Validation du lot : règle décimale limitée aux ventes / POS dans le 3-B (étendue aux entrées,
  sorties, transferts et inventaires au 3-C) ; conversion figée dès qu'un brouillon utilise le
  conditionnement ; nom unique parmi les conditionnements actifs de l'article ; prix non
  configuré distinct de 0 et invendable (migration 0028 : `sale_price` nullable, retour arrière
  refusé s'il existe des prix non configurés).
- Migration 0027 : aucun conditionnement inventé pour les articles existants (vendus comme
  avant, en unité de base, prix inchangé) ; lignes de vente existantes : `base_quantity =
  quantity`. Retour arrière refusé s'il existe des lignes vendues en conditionnement.
- Quantités entières par défaut : un article vendu au poids doit être marqué « quantités
  décimales autorisées ».
- Hors périmètre (lots suivants) : codes-barres multiples ou par conditionnement, images, lots /
  péremption, entrées / sorties / transferts / inventaires en conditionnement, référentiel global
  d'unités, tarifs (listes de prix, prix par client, par quantité, promotions),
  `sales.sale.price_override`.
