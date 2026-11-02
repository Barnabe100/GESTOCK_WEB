# ADR-0055 — Rapports et tableau de bord commercial (palier R10)

- **Statut** : Proposée — livrée (migration 0062), soumise à validation fonctionnelle
- **Date** : 2026-11-02
- **Prolonge** : ADR-0017 (ventes), ADR-0020 (paiements), ADR-0024 (profils UX), ADR-0028
  (fuseau du tenant), ADR-0038 (exports), ADR-0039 (coûts masqués), ADR-0053 (recettes),
  ADR-0054 (remises)

## Contexte

Le module `reports` était déclaré « Bientôt disponible », inclus dans STANDARD et ENTREPRISE et
proposé par les profils. La mission demande des indicateurs (KPI), des analyses commerciales,
la rentabilité des recettes et des exports Excel / CSV / PDF.

## Décisions

1. **Aucune table, aucune seconde source de vérité.** Le module Ventes expose ses requêtes de
   rapport (`sales.api.report_sales_query`, `report_lines_query`, `report_payments_query`) ; le
   Stock expose le coût des sorties (`stock.api.outflow_costs_query`). `reports` ne lit aucun
   modèle d'un autre module (règle 10) ; il dépend de `catalog`, `customers`, `stock`, `sales`.
2. **Ventes VALIDÉES seulement**, période = date MÉTIER des ventes (`sale_date`, fuseau du
   tenant) ; par défaut le mois en cours ; deux ans au plus (`report_period_too_long`).
3. **Formules** (détail dans [`REPORTS.md`](../architecture/REPORTS.md)) : CA = Σ `total`
   (remise globale déduite) ; coût = coût RÉEL des mouvements `SALE` de ces ventes (CMUP du site
   au moment de la sortie, ingrédients des recettes compris) ; marge = CA − coût ; taux =
   marge / CA × 100 ; panier moyen = CA / nombre de ventes ; par article / catégorie : montants
   des lignes avant remise globale et coût des mouvements directs de la ligne.
4. **Rentabilité des recettes** dans le module Recettes (`GET /restaurant/recipes/profitability`,
   `restaurant.recipes.recipe.view`) : portions vendues, CA des lignes, coût réel des
   ingrédients consommés (consommations matérialisées, O10), coût par portion, marge.
5. **Coûts et marges** : retirés de toute réponse sans `catalog.article.cost_view`
   (`REPORT_COST_FIELDS`, `RECIPE_COST_FIELDS` + `ingredient_cost`), aucun tri sur eux sans la
   permission, colonnes ABSENTES des exports.
6. **Permissions** `reports.report.view` (read) et `reports.report.export` (export), par site
   (sites visibles où elles sont détenues, module effectif). Rôles de base : Administrateur
   (`*`), Gestionnaire (`reports.*`), Consultant (consultation par `*.view`) ; Vendeur : aucune.
   *À valider* : ce choix de rôles (donnée de `role_templates.toml`, sans migration).
7. **Exports** par l'architecture commune (`app/platform/exports.py`) : synthèse, répartition,
   paiements ; périmètre exact du rapport affiché, `SM_EXPORT_MAX_ROWS`, audit
   `export.generated` (`feature = reports.<rapport>`).
8. **Tableau de bord** : indicateur `reports:month_revenue` (CA du mois) ajouté aux profils UX
   (données), affiché avec la permission et le module effectif.
9. **Livraison** (0062) : activations héritées remises à `false` sur les sites existants ;
   activation explicite par site ; nouveaux sites : défauts de leur profil.

## Hors périmètre

Écarts de caisse (déjà dans les sessions de caisse : écart calculé à la clôture, liste
filtrable), valorisation du stock (niveaux de stock), rapports planifiés envoyés par e-mail,
graphiques avancés, comparaison de périodes.
