# ADR-0053 — Recettes : fiches techniques, coût de revient, déduction des ingrédients (palier R8)

- **Statut** : Proposée — livraison complète demandée par l'utilisateur le 2026-11-02 (règles
  « à formaliser » : décisions ci-dessous) ; soumise à validation fonctionnelle sur Ubuntu
- **Date** : 2026-11-02
- **Prolonge** : [ADR-0049](0049-restauration-commandes.md) (restauration, O10),
  ADR-0039 (articles non gérés en stock), ADR-0040 (conditionnements), ADR-0045 (lots, FEFO),
  ADR-0046 (assortiment par site), ADR-0050 (cuisine)

## Contexte

Le module `restaurant.recipes` était déclaré « Bientôt disponible » (`planned.py`), inclus dans
les plans STANDARD et ENTREPRISE et proposé par les profils de restauration. `RESTAURANT.md` en
fixait le cadre : fiches versionnées, ingrédients en unité de base, consommation par
`StockService.consume`, CMUP et lots inchangés, et un point ouvert **O10** : la garde
d'unicité des mouvements (`tenant, source_line_id, movement_type, site, lot`) n'admet qu'UN
mouvement d'un type par ligne source et par lot — une ligne de vente qui consomme trois
ingrédients ne peut pas produire trois mouvements sous la même `source_line_id`.

Existant pertinent :

- un plat est un **article du catalogue** ; un article « non géré en stock » (ADR-0039) se vend
  sans mouvement ni contrôle de stock ;
- le menu, les commandes (personnel et QR), le POS et le règlement des commandes aboutissent
  tous à `SaleService.validate`, seul point de sortie de stock des ventes ;
- `StockService.consume` est le moteur unique des sorties (verrous article → assortiment →
  niveaux → lots, FEFO sur les lots non périmés, tout ou rien, CMUP inchangé par une sortie).

## Décisions

- **D1 — Objet.** Une recette est la fiche technique d'UNE présentation vendue : article du
  catalogue en unité de base, ou un conditionnement de cet article (« variante »). Elle est
  propre à l'entreprise (comme le catalogue) et s'applique sur les sites où le module
  `restaurant.recipes` est **effectif** (profil ∩ abonnement ∩ activation du site). Une
  présentation a au plus une recette (unicité en base, `NULLS NOT DISTINCT`). Jamais supprimée :
  désactivée, elle ne déduit plus rien et n'influe plus sur la disponibilité.
- **D2 — Plat non géré en stock (aucune double déduction).** L'article d'une recette doit être
  **non géré en stock** : il ne produit jamais de mouvement propre ; seuls ses ingrédients sortent.
  Garde du catalogue (port `catalog.recipe_port`) : un article ayant une recette ACTIVE ne peut
  pas devenir géré en stock (`409 article_has_recipe`).
- **D3 — Ingrédients.** Articles du catalogue **gérés en stock**, actifs, quantité en **unité de
  base** (> 0, 3 décimales au plus), un ingrédient au plus une fois par version, 1 à 50 lignes,
  jamais le plat lui-même. Un article non géré ne peut pas être ingrédient (donc aucune
  sous-recette en V1). Un ingrédient d'une recette active ne peut pas devenir non géré en stock
  (`409 article_used_in_recipes`).
- **D4 — Rendement et portions.** Une version déclare son **rendement** : nombre d'unités de la
  présentation (portions) que la composition produit (> 0). Consommation d'un ingrédient pour une
  ligne vendue = quantité × unités vendues ÷ rendement, arrondie au millième (demi supérieur).
  Ingrédient en quantités entières (ADR-0040) : la quantité par portion (quantité ÷ rendement)
  doit être entière — refus à l'enregistrement (`422 recipe_whole_quantity_required`), jamais
  une surprise à la vente.
- **D5 — Variantes.** Ligne vendue dans un conditionnement : recette propre de ce
  conditionnement si elle existe (unités vendues = nombre de conditionnements), sinon recette de
  l'unité de base appliquée à la quantité de base de la ligne (2 « Plat familial » de 4 portions
  = 8 portions).
- **D6 — Versions immuables.** Toute modification de la composition (ingrédients, quantités,
  rendement, instructions) crée la version n + 1 ; versions et lignes ne sont jamais modifiées
  (rôle SQL : `SELECT, INSERT`). La recette pointe sa version courante ; l'historique reste
  consultable ; une consommation référence la version appliquée.
- **D7 — Moment de la déduction.** À la **validation de la vente** (back-office, POS, règlement
  d'une commande personnel ou QR : même `SaleService.validate`), dans la MÊME transaction, par UN
  SEUL appel à `StockService.consume` regroupant les lignes gérées et les ingrédients (ordre
  global des verrous, aucun interblocage, FEFO / FIFO automatique, CMUP inchangé, mouvements
  `SALE` au CMUP courant). Le module Ventes ne dépend pas des Recettes : port
  `sales.components_port` implémenté par le module Recettes. Rien n'est déduit à la prise de
  commande ni en cuisine (la commande ne réserve rien, comme pour tout article géré).
- **D8 — O10 résolu.** Chaque couple (ligne de vente, ingrédient) est **matérialisé** dans
  `restaurant_recipe_consumptions` ; son identifiant est la `source_line_id` du mouvement (source
  = la vente). La garde d'unicité des mouvements reste inchangée et protège chaque ingrédient ;
  unicité supplémentaire (ligne de vente, ingrédient) en base. Une vente déjà validée ne se
  revalide pas (statut sous verrou) : aucune double déduction.
- **D9 — Refus.** Ingrédient insuffisant, seulement périmé, hors assortiment du site ou devenu
  non géré : la vente n'est pas validée (tout ou rien) et le refus nomme le plat et l'ingrédient
  (`422 recipe_ingredients_unavailable`, motif d'origine dans `reason`). Aucune dérogation de lot
  périmé pour un ingrédient en V1 (la dérogation existante porte sur une ligne de vente).
- **D10 — Annulation de la vente.** Un inverse `CANCELLATION` par mouvement d'ingrédient
  d'origine : même quantité, même coût, même lot (comme les lignes, H-D10) ; double annulation
  impossible (statut verrouillé + unicité).
- **D11 — Coût de revient (par site).** Σ quantité × CMUP courant de l'ingrédient sur le site ÷
  rendement = coût par portion ; marge = prix de vente de la présentation − coût. « Incomplet »
  si un ingrédient n'a aucun coût sur le site (jamais reçu). Champs de coût et de marge réservés
  à `catalog.article.cost_view` (masquage `RECIPE_COST_FIELDS`). Le coût réel d'une vente est
  celui de ses mouvements.
- **D12 — Disponibilité des plats.** Portions réalisables sur un site = minimum, sur les
  ingrédients, de ⌊stock du site × rendement ÷ quantité⌋ (indicatif : le refus de la vente fait
  foi). Menu : motif calculé `ingredients_unavailable` quand aucune portion n'est réalisable —
  l'élément n'est plus commandable (prise de commande du personnel et QR comprises), comme pour
  les autres motifs ; l'« épuisé » manuel reste inchangé. Page publique QR : l'élément est
  montré « épuisé » (motif ajouté aux motifs publics, aucun détail interne) et la commande
  refusée. Port `restaurant_menu.availability_port` implémenté par les Recettes (le menu n'en
  dépend pas) ; en conséquence, le module dépend de `restaurant.menu` (comme la cuisine dépend
  des commandes dont elle implémente le port) : dépendances `catalog`, `stock`, `sales`,
  `restaurant.menu`.
- **D13 — Cuisine.** Fiche technique d'une ligne (ingrédients mis à l'échelle de la quantité
  commandée, instructions) consultable depuis l'écran cuisine avec la permission de
  consultation des recettes ; sans elle, rien n'est affiché. Aucun coût.
- **D14 — Permissions.** `restaurant.recipes.recipe.view` (read) et
  `restaurant.recipes.recipe.manage` (admin) — matrice de `RESTAURANT.md` §9 : Administrateur
  (`*`), Gestionnaire (`restaurant.*`), Consultant (`*.view`, consultation). Aucun modèle de rôle
  modifié. Coûts : `catalog.article.cost_view` en plus.
- **D15 — Livraison.** Module disponible ; offres inchangées (STANDARD et ENTREPRISE
  l'incluaient déjà) ; activations héritées remises à `false` sur les sites existants
  (activation explicite par site, comme R1, R5, R6, R7). Sans module effectif sur le site, une
  vente se comporte exactement comme avant.

## Modèle de données (migrations 0059, 0060)

| Table | Contenu | Droits du rôle applicatif |
|---|---|---|
| `restaurant_recipes` | présentation (article, conditionnement), version courante, actif, notes | `SELECT, INSERT, UPDATE (current_version_id, is_active, notes, updated_at)` |
| `restaurant_recipe_versions` | n°, rendement, instructions, auteur | `SELECT, INSERT` |
| `restaurant_recipe_lines` | ingrédient, quantité (unité de base), note | `SELECT, INSERT` |
| `restaurant_recipe_consumptions` | site, vente, ligne de vente, version, ingrédient, quantité | `SELECT, INSERT` |

RLS `ENABLE` + `FORCE` (isolation par `tenant_id`), FK composites `(tenant_id, …)` vers les
articles, conditionnements, ventes et lignes de vente ; aucun droit pour la console. 0060 :
livraison (activations remises à `false`) ; descente de 0059 refusée dès qu'une recette ou une
consommation existe.

## Conséquences

- Les mouvements d'une vente de plat portent la vente pour source : fiche de la vente, journal
  des mouvements, valorisation et annulation fonctionnent sans nouvelle notion.
- Limites V1 : aucune sous-recette ; aucune dérogation de lot périmé pour un ingrédient ;
  aucune réservation à la commande (une commande « réglée à la fin » peut voir son règlement
  refusé si les ingrédients ont été consommés entre-temps, comme un article géré) ; perte en
  cuisine non modélisée (procédure de perte, à concevoir séparément) ; recettes communes à tous
  les sites (coûts et disponibilité calculés par site).
