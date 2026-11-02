# Rapports (palier R10)

Référence : [ADR-0055](../adr/0055-rapports.md). Consultation seule, aucune table.

## Routes (`/api/v1`)

| Route | Permission | Contenu |
|---|---|---|
| `GET /reports/sales/summary` | `reports.report.view` | Indicateurs de la période et série par `granularity` = `day` / `week` (lundi) / `month` |
| `GET /reports/sales/breakdown?dimension=…` | `reports.report.view` | Répartition paginée : `article`, `category`, `site`, `seller`, `channel`, `customer` ; tri `revenue` (défaut décroissant), `sales_count`, `label`, `quantity` (article), `cost`, `margin` (avec `cost_view`) |
| `GET /reports/payments` | `reports.report.view` | Paiements effectués par moyen (type, libellé figé) |
| `GET /reports/export?report=summary\|breakdown\|payments&format=xlsx\|csv\|pdf` | `reports.report.export` + `view` | Fichier du rapport affiché, audité |
| `GET /restaurant/recipes/profitability` | `restaurant.recipes.recipe.view` | Rentabilité des plats |

Filtres communs : `date_from`, `date_to` (dates métier, défaut : mois en cours), `site_id`,
`channel`. Refus : `422 report_period_invalid`, `422 report_period_too_long` (> 732 jours),
`400 invalid_sort`.

## Formules

| Indicateur | Formule |
|---|---|
| Ventes | nombre de ventes `VALIDATED` de la période (brouillons et ventes annulées exclus) |
| Chiffre d'affaires (CA) | Σ `total` (sous-total − remise globale) |
| Remises | Σ `discount_amount` |
| Panier moyen | CA / nombre de ventes (arrondi au centime) |
| Encaissé | Σ paiements `COMPLETED` de ces ventes ; reste dû = CA − encaissé |
| Coût | Σ (−quantité × `unit_cost`) des mouvements `SALE` de ces ventes (CMUP du site au moment de la sortie ; ingrédients des recettes compris ; article non géré sans recette : coût nul) |
| Marge | CA − coût ; taux = marge / CA × 100 (nul si CA nul) |
| Part | CA de la ligne / CA du périmètre × 100 |
| Article / catégorie | montants des LIGNES (avant remise globale) ; coût des mouvements directs de la ligne |
| Rentabilité d'un plat | portions = Σ quantités de base des lignes ayant déduit des ingrédients ; coût = Σ coûts des mouvements des consommations ; coût par portion = coût / portions |

## Périmètre et sécurité

- Sites visibles du membre (site sélectionné, sinon ses sites) où il détient la permission,
  module effectif sur le site ; un rôle limité à un site ne vaut qu'avec ce site sélectionné.
- Coûts, marges, taux et coût des ingrédients **absents** sans `catalog.article.cost_view`
  (réponses JSON, tris et exports).
- Isolation : RLS et condition `tenant_id` explicite dans chaque requête.
