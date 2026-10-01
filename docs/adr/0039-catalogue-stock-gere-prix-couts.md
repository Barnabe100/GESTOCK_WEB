# ADR-0039 — Catalogue : scan exact, articles gérés ou non en stock, historique des prix, droits sur les prix et les coûts

- **Statut** : Acceptée (Lot 3-A — catalogue / articles)
- **Date** : 2026-10-01

## Contexte

L'audit du catalogue (Lot 3) a établi :

- un **défaut du point de vente** : Entrée ajoutait le premier article *affiché*, alors que la
  recherche (« contient », relancée 250 ms après la frappe) pouvait encore afficher les résultats
  précédents — une douchette (code + Entrée en quelques millisecondes) ajoutait donc un mauvais
  article ; aucune correspondance exacte n'était exigée ;
- **tout article était stocké** : un service ne pouvait pas être vendu (`insufficient_stock`) ;
- l'historique des prix n'existait que dans le journal d'audit, sans vue sur la fiche ;
- `catalog.article.update` couvrait aussi les prix, et les **coûts internes** (prix d'achat,
  CMUP, valorisations) étaient visibles par toute personne qui consultait les articles ou le
  stock (Vendeur compris).

## Décision

1. **Scan exact** : Entrée dans la recherche du point de vente = scan. L'interface envoie la
   valeur saisie À CET INSTANT à `GET /pos/articles/by-barcode?site_id=&barcode=`
   (`pos.terminal.use`) : égalité EXACTE sur le code-barres (espaces retirés), article ACTIF du
   tenant, jamais de recherche partielle, ni de repli sur la référence ou la désignation, ni
   d'utilisation d'un résultat affiché. Code inconnu : `404 barcode_unknown`, message
   « Code-barres inconnu », rien n'est ajouté au panier.
2. **`catalog_articles.stock_managed`** (`BOOLEAN NOT NULL DEFAULT true`, migration 0026 :
   tous les articles existants restent gérés) :
   - `true` : comportement inchangé (stock, contrôles, mouvements, entrées, sorties, transferts,
     inventaires, seuils, alertes) ;
   - `false` : vendu (back-office et point de vente) **sans mouvement ni contrôle de stock** —
     jamais `insufficient_stock` ; absent des niveaux, seuils, alertes, candidats d'inventaire ;
     entrées, sorties, transferts, inventaires et seuils refusés (`422
     article_not_stock_managed`).
   - **Garde centrale** dans `StockService` : aucun niveau ni mouvement pour un article non
     géré, quel que soit l'appelant. Le drapeau est lu sous **verrou partagé** de l'article
     (`FOR SHARE`) par toute opération de stock et par la validation d'une vente.
   - **géré → non géré** : seulement si le stock est nul sur TOUS les sites du tenant
     (`409 article_has_stock`, sites concernés) ; verrou EXCLUSIF de l'article avant la
     vérification (une opération de stock en cours se termine d'abord ; aucune ne peut
     commencer avant la fin de la vérification). Aucun ajustement ni mouvement automatique.
     Le catalogue ne dépend pas du stock : il interroge un **port** (`catalog.stock_port`)
     que le module Stock implémente (même principe que `sales.cash_port`).
   - **non géré → géré** : autorisé, aucun mouvement ; le stock part de zéro.
   - Actif / inactif reste indépendant ; un stock nul ne désactive jamais un article.
   - Annulation d'une vente : seules les lignes réellement sorties du stock (mouvement
     `SALE`) y reviennent ; un article devenu non géré entre-temps (stock nul exigé) ne
     reçoit aucun stock (audit : `not_restored_unmanaged`).
3. **Historique des prix** : aucune table ; lu dans le journal d'audit existant
   (`article.created` — prix initiaux désormais journalisés — et `article.updated`, avant /
   après). `GET /catalog/articles/{id}/price-history` (paginé, plus récent d'abord) : exige
   `catalog.article.view` ET (`catalog.article.price_update` OU `audit.log.view`) ; prix
   d'achat seulement avec `cost_view`. Section « Historique des prix » sur la nouvelle fiche
   article (`/catalog/articles/{id}`).
4. **Permissions** (convention `module.ressource.action`) :
   - `catalog.article.update` (écriture) : informations générales seulement ;
   - `catalog.article.price_update` (écriture, nouvelle) : prix de vente et prix d'achat, à la
     modification ET à la création (sans elle, l'article est créé aux prix par défaut 0) ;
     aucune des deux n'accorde l'autre ; valeur inchangée renvoyée par le formulaire acceptée ;
   - `catalog.article.cost_view` (lecture, nouvelle) : coûts internes.
   - Rôles de base : **Administrateur** les trois ; **Gestionnaire** `update` mais ni
     `price_update` ni `cost_view` (exclus de son motif `catalog.*`) ; Vendeur et Consultant
     aucun des deux. Un rôle personnalisé peut les accorder.
5. **Coûts absents sans `cost_view`, contrôlés par le serveur** : une classe de route
   (`app.platform.costs.cost_masking_route`) retire les champs de coût de TOUTE réponse JSON
   des routeurs concernés (catalogue, stock, transferts, inventaires, alertes, journal
   d'audit), à toute profondeur, d'après les permissions effectives de la requête : le champ
   est absent (jamais remplacé par une valeur fictive). Champs : `purchase_price`,
   `average_cost`, `average_cost_before`, `average_cost_after`, `stock_value`, `unit_cost`,
   `amount` et `total_amount` (documents de stock), `adjustment_value`, `surplus_value`,
   `shortage_value`. Tri par prix d'achat refusé sans `cost_view`.

## Conséquences

- Une nouvelle route d'un de ces routeurs est protégée sans code supplémentaire ; un nouveau
  champ de coût doit être ajouté à la liste de son routeur.
- Le Gestionnaire ne voit plus les coûts (CMUP, valorisations, coûts unitaires des entrées) ni
  ne modifie les prix par défaut ; il saisit toujours le coût unitaire d'une entrée, sans le
  revoir ensuite (point à valider : lui accorder `cost_view` ou un rôle personnalisé).
- Hors périmètre (lots suivants) : conditionnements, codes-barres multiples, images, lots /
  péremption, tarification avancée, promotions, `sales.sale.price_override`, unités globales,
  intégration matérielle.
