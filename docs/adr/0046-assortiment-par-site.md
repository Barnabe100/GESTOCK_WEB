# ADR-0046 — Assortiment par site

- **Statut** : Acceptée (Recette, étape 1 — paliers 1 à 3 livrés et validés, palier 4 :
  documentation ; E2E et CI au palier 5)
- **Date** : 2026-10-07
- **Prolonge** : [ADR-0033](0033-abonnement-par-site.md) (site = périmètre d'écriture),
  [ADR-0039](0039-catalogue-stock-gere-prix-couts.md) (`stock_managed`, garde centrale),
  [ADR-0044](0044-emplacements-par-site.md) (emplacements par site),
  [ADR-0045](0045-lots-et-peremption-stock-reception.md) (lots, ordre des verrous)

## Contexte

La recette a révélé une confusion entre trois notions : le **catalogue** (articles de
l'entreprise, communs à tous ses sites), ce qu'un **site** vend, reçoit et stocke, et le
**stock** d'un site (`stock_levels`). Les niveaux de stock, le point de vente, les inventaires
complets et les alertes présentaient le **produit cartésien catalogue × sites** : tout article
créé apparaissait sur tous les sites (« non stocké », compté, proposé à la caisse), y compris
sur un dépôt qui ne le vendra jamais. Aucune donnée ne permettait de dire « ce site propose cet
article ».

Règle retenue : **CATALOGUE TENANT ≠ ASSORTIMENT SITE ≠ STOCK SITE.**

## Décision (D1 à D6 validées, puis décisions des paliers 1 à 4)

### D1 — Modèle

- Table `catalog_site_articles` du module **Catalogue** : une ligne par (tenant, site,
  article), `UNIQUE (tenant_id, site_id, article_id)`, clés étrangères **composites**
  `(tenant_id, site_id) → sites` et `(tenant_id, article_id) → catalog_articles` (RESTRICT),
  RLS `ENABLE` + `FORCE`, droits du rôle applicatif `SELECT, INSERT, UPDATE` (jamais
  `DELETE`).
- Colonnes : `is_active`, `added_at`, `added_by`, `removed_at`, `removed_by`. Contraintes :
  `is_active = (removed_at IS NULL)` ; `removed_by` seulement avec `removed_at`.
- **Retrait = désactivation, jamais une suppression** ; réactivation = **la même ligne**.
- Distincte de `stock_levels` : ajouter un article à l'assortiment ne crée **aucun** niveau de
  stock ; un nouveau site a un assortiment vide ; un nouvel article n'est proposé par aucun
  site (sauf choix explicite, D6).

### D2 — Reprise des données (migration 0038)

- Reprise **par usage réel seulement** : niveaux de stock, mouvements, lignes de vente,
  brouillons d'entrées, de sorties et de transferts (source et destination), lignes des
  inventaires non clôturés, affectations d'emplacement. `added_by = NULL` (affiché « reprise
  initiale »), aucun ajout automatique au-delà.
- Le retour arrière (`downgrade`) est refusé dès qu'une ligne a été modifiée par un
  utilisateur (ajout explicite, retrait ou réactivation).

### D3 — Refus explicite, jamais d'ajout automatique

- Toute opération sur un article hors de l'assortiment **actif** du site est refusée :
  `422 article_not_in_site_assortment` (`site_id`, références des articles).
- Contrôle **de saisie** dès l'enregistrement du brouillon (vente, entrée, sortie, transfert,
  inventaire ciblé) et contrôle **faisant foi** à la validation, sous verrou partagé.
- Transferts : **source ET destination**.
- Les **annulations** restent possibles (restauration de l'historique), même hors assortiment.
- Interface : « Ajouter à l'assortiment du site » seulement avec `catalog.assortment.manage`
  (contrôlée par le serveur, auditée) ; sinon « Hors assortiment » et invitation à s'adresser
  à un responsable.

### D4 — Retrait

- **Refusé** si l'article a, **sur ce site**, du stock ou un solde de lot non nul
  (`409 article_has_stock`), ou figure dans un document ouvert du site — brouillon de vente,
  d'entrée, de sortie, de transfert (source ou destination), inventaire non clôturé
  (`409 article_in_open_documents`). Les modules détenteurs de ces données déclarent leurs
  contrôles au port `catalog.assortment_port` (aucune dépendance inverse).
- Retrait multiple **tout ou rien** ; aucun motif obligatoire ; **audit obligatoire**.
- Seuils et emplacements du site **conservés mais inertes**.
- Stock recréé par une annulation après un retrait : visible « Hors assortiment », inutilisable
  sans réactivation.
- **Ordre global des verrous : article → assortiment → niveaux → lots.** Le retrait prend le
  verrou exclusif de la ligne d'assortiment, les opérations un verrou partagé : un retrait et
  une validation concurrents s'exécutent l'un après l'autre (le second voit le résultat du
  premier).

### D5 — Permission

- `catalog.assortment.manage`, nature **`admin`**, contrôlée **par site** (permission et
  abonnement du site ciblé ; un site en attente d'activation se prépare).
- Rôles de base : Administrateur et Gestionnaire oui ; Vendeur et Consultant non.
- Aucune permission de consultation dédiée : `catalog.article.view` suffit.

### D6 — Outils

- **Copie** de l'assortiment d'un autre site (ajout seulement, catégorie facultative, articles
  actifs ; ni stock, ni seuils, ni lots ; `422 assortment_copy_same_site`).
- **`site_ids` à la création d'un article** : facultatif, vide par défaut, permission
  contrôlée sur chaque site avant toute écriture.
- Écartée : règle d'ajout automatique (par catégorie ou par type de site).

### Palier 2 — application aux opérations

- **Garde centrale** dans `StockService._lock` : tout mouvement exige l'article actif dans
  l'assortiment du site (réception, sortie, vente, transfert, ajustement d'inventaire, seuils) ;
  `apply_many` exempte les seuls mouvements `CANCELLATION`. Un test statique vérifie que chaque
  point d'entrée passe par cette garde.
- **Ventes** : contrôle au brouillon, puis sous verrou à la validation pour **toutes** les
  lignes, y compris les articles non gérés en stock (qui ne passent pas par `StockService`).
- **Point de vente** : la recherche ne propose que l'assortiment actif du site (jamais le stock
  restant hors assortiment) ; un scan d'article connu hors assortiment est refusé (422).
- **Niveaux** : assortiment actif ∪ niveaux de stock non nuls, champ `in_assortment` ; le
  produit cartésien est supprimé.
- **Alertes** : limitées à l'assortiment actif ; l'état réel du stock hors assortiment reste
  affiché, mais n'est jamais une alerte.
- **Inventaires** : articles proposés = assortiment actif ; inventaire complet = assortiment
  actif × articles actifs gérés en stock, **y compris jamais reçus** (stock 0).
- **Seuils et affectation d'emplacement** : refusés hors assortiment.

### Décisions ultérieures (paliers 2 à 4)

1. **Désaffecter un emplacement** (`location_id: null`) reste **permis** hors assortiment
   (nettoyage de configuration) ; l'affecter est refusé.
2. **Lots disponibles** d'un article hors assortiment : lecture conservée (consulter n'est pas
   opérer).
3. **Messages génériques** `article_has_stock` / `article_in_open_documents` **inchangés** :
   l'interface du retrait construit des messages contextualisés à partir du détail `blocked`
   (référence, raison `stock` ou `open_document`, documents).
4. **Filtre** `in_site_assortment` (avec `site_id`) sur la liste du catalogue : articles
   proposés ou non par un site (dialogue d'ajout).
5. **Site principal** : calculé par le serveur et exposé dans les capacités (`main_site_id`) —
   le plus ancien site actif du tenant s'il est accessible au membre, sinon le premier site
   accessible. **Aucun indicateur en base** (`sites.is_main` écarté), aucune migration. Site
   par défaut de la page Assortiment (après le site choisi dans l'en-tête).
6. **Menu** : « Assortiment des sites » juste après « Articles ».

### Palier 3 — interface

- Page **Assortiment des sites** (`/catalog/assortment`) : liste par site (Proposés / Retirés /
  Tous), ajout depuis le catalogue (articles pas encore proposés), retrait d'une ligne ou de la
  sélection (confirmé), réactivation, copie depuis un autre site ; consultation seule sans la
  permission.
- **Fiche article** : section « Sites » (état par site visible, ajout, retrait, réactivation).
- **Création d'article** : choix facultatif des sites.
- **Sélecteur d'article** et écrans opérationnels (entrées, sorties, ventes, transferts) :
  « Hors assortiment » signalé, ajout explicite proposé selon la permission.
- **Niveaux** : badge « Hors assortiment », seuils non modifiables, emplacement seulement
  désaffectable ; **POS** : message dédié pour un site sans assortiment.

## Conséquences

- Chaque site présente uniquement ce qu'il propose ; le catalogue reste unique pour
  l'entreprise. Préparer un nouveau site = composer son assortiment (ajout, copie).
- Le serveur reste la seule frontière : l'interface signale et propose, mais chaque écriture
  est revérifiée (garde centrale, verrous, permission par site).
- Les tests et les données de démonstration doivent ajouter explicitement les articles à
  l'assortiment des sites (`site_ids` à la création ou ajout explicite).
- Limites connues : les libellés d'accessibilité des cases à cocher de PrimeReact sont en
  anglais (« Row Selected ») — configuration globale de PrimeReact prévue plus tard ; les tests
  E2E sont adaptés au palier 5.

## Alternatives écartées

- **Déduire l'assortiment du stock** (`stock_levels`) : confond « proposé » et « stocké » ; un
  article jamais reçu ne pourrait pas être inventorié ni commandé, un article retiré resterait
  proposé tant qu'un niveau existe.
- **Ajout automatique** à la première opération : rend le contrôle inopérant et recrée la
  confusion d'origine.
- **Suppression des lignes au retrait** : perte de l'historique (qui, quand) et réactivation
  impossible à l'identique.
- **Règle d'ajout automatique** (catégorie, type de site) : reportée (D6).
- **Indicateur `sites.is_main`** pour le site principal : migration et écran de choix sans
  besoin métier avéré ; une règle de calcul suffit.
