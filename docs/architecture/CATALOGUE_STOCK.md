# Phase 2 — Catalogue et stock : règles métier et adaptation Web

> Source étudiée (lecture seule) : dépôt `GESTOK_ENTREP`, branche
> `claude/stockmanager-requirements-analysis-33706l` (commit `a05ca9d`) — modèles, services,
> migrations, données de référence et cahier des charges §4 à §12, §20.
> **Seules les règles métier sont reprises** : ni classes, ni dépôts, ni vues, ni SQLite, ni
> PySide6. Chaque règle porte un identifiant réutilisé dans les tests et le code.

Légende de la colonne Web : ✅ reprise telle quelle · 🔄 adaptée (voir §2) · ⏭ sous-phase
ultérieure.

## 1. Règles du Desktop

### 1.1 Catégories

| Id | Règle Desktop | Web |
|---|---|---|
| CAT-01 | Nom obligatoire (espaces retirés), 100 caractères max. | ✅ |
| CAT-02 | Nom unique. | 🔄 unique **par tenant**, insensible à la casse |
| CAT-03 | Statut actif/inactif ; **aucune suppression physique**. | ✅ |
| CAT-04 | Une catégorie inactive reste consultable mais n'est plus proposée pour une nouvelle opération. | ✅ |
| CAT-05 | Recherche par nom ; filtre actifs / tous. | ✅ + pagination serveur |
| CAT-06 | Audit : création, modification, activation, désactivation. | ✅ (avec avant/après) |

### 1.2 Fournisseurs

| Id | Règle Desktop | Web |
|---|---|---|
| SUP-01 | Nom / raison sociale obligatoire, 150 max. | ✅ |
| SUP-02 | **Pas d'unicité du nom** (deux fournisseurs peuvent partager une raison sociale). | ✅ |
| SUP-03 | Champs optionnels : contact (150), téléphone (30), email (150, doit être valide), adresse (255), ville (100), pays (100), observations (500) ; une chaîne vide efface le champ. | ✅ (email validé strictement) |
| SUP-04 | Actif/inactif, jamais supprimé ; inactif non proposé pour une nouvelle opération. | ✅ |
| SUP-05 | Recherche : nom, contact, ville, email. | ✅ + téléphone, pagination |
| SUP-06 | Audit des créations, modifications, changements de statut. | ✅ |

### 1.3 Articles

| Id | Règle Desktop | Web |
|---|---|---|
| ART-01 | Référence obligatoire (50 max), **unique**. | 🔄 unique par tenant, insensible à la casse |
| ART-02 | Désignation obligatoire (255 max). | ✅ |
| ART-03 | Catégorie obligatoire. | ✅ |
| ART-04 | Unité obligatoire (20 max, texte libre : pièce, kg, L, m…). | ✅ (table d'unités : V1.5 avec les variantes) |
| ART-05 | Fournisseur principal optionnel. | ✅ (si le module `suppliers` est actif) |
| ART-06 | Prix d'achat par défaut ≥ 0, prix de vente ≥ 0 (montants `Decimal`). | ✅ `NUMERIC(18,2)` |
| ART-07 | Stock minimum ≥ 0 ; stock maximum vide ou ≥ 0 **et ≥ stock minimum** (contraintes en base). | ✅ seuils par défaut de l'article (voir Q2) |
| ART-08 | Emplacement (100), description (1000) optionnels. | 🔄 description ✅ ; emplacement ⏭ **par site** (§2) |
| ART-09 | Code-barres optionnel (50), **unique parmi les articles actifs** (un article désactivé peut conserver un code repris par un article actif). | 🔄 idem, par tenant (index unique partiel) |
| ART-10 | À la création ou au changement : catégorie et fournisseur doivent être **actifs** ; une association existante devenue inactive est conservée tant qu'elle n'est pas changée. | ✅ |
| ART-11 | **Stock actuel et CMUP ne sont jamais modifiables via l'article** : seules les opérations de stock tracées les font évoluer. | ✅ (ils ne sont même plus sur l'article : §2) |
| ART-12 | Stock initial saisi à la création → mouvement **AJUSTEMENT** tracé ; CMUP initial = prix d'achat. | ⏭ 2.2 (un stock appartient à un site) |
| ART-13 | Actif/inactif, jamais supprimé ; un article inactif ne peut pas entrer dans une nouvelle opération. | ✅ |
| ART-14 | Recherche : référence, désignation, catégorie, code-barres ; filtres : catégorie, actifs, stock faible / rupture. | ✅ (stock faible : ⏭ 2.2, par site) |
| ART-15 | Recherche par code-barres : article **actif** uniquement. | ✅ |
| ART-16 | Réactivation : le code-barres ne doit pas entrer en conflit avec un article actif. | ✅ (vérifié explicitement, erreur claire) |

### 1.4 Stock et mouvements (moteur central)

| Id | Règle Desktop | Web |
|---|---|---|
| STK-01 | **Point de passage unique** : toute variation de stock passe par le service de stock. | ✅ `StockService` |
| STK-02 | Le service n'ouvre jamais sa propre transaction : il s'exécute dans celle du document (tout ou rien). | ✅ (ADR-0008) |
| STK-03 | **Stock jamais négatif**, quel que soit le rôle, vérifié avant toute écriture. | ✅ + contrainte `CHECK` + verrou de ligne |
| STK-04 | Quantité **signée** ; `stock_après = stock_avant + quantité`. | ✅ |
| STK-05 | CMUP recalculé **uniquement sur une ENTRÉE** : `((stock_avant × CMUP) + (q × prix)) / (stock_avant + q)`. | ✅ (portée : Q1) |
| STK-06 | Sorties, ventes, ajustements, annulations ne modifient pas le CMUP ; pas de reconstruction rétroactive. | ✅ |
| STK-07 | Mouvements **immuables** (jamais modifiés ni supprimés) ; une correction = un nouveau mouvement. | ✅ (droits SQL : insertion seule) |
| STK-08 | Types : ENTRÉE, SORTIE, VENTE, AJUSTEMENT, ANNULATION. | ✅ (+ TRANSFERT : Q4) |
| STK-09 | Chaque mouvement : date-heure, article, type, quantité, stock avant/après, coût unitaire, ligne d'origine, mouvement d'origine (annulation), utilisateur, commentaire. | ✅ + `site_id` |
| STK-10 | Consultation des mouvements : recherche libre (article, utilisateur, commentaire), filtres date (borne de fin inclusive sur la journée), article, type, utilisateur ; permission dédiée. | ✅ + site, pagination |

### 1.5 Entrées de stock

| Id | Règle Desktop | Web |
|---|---|---|
| ENT-01 | Numéro `ENT-000001` attribué à la création. | 🔄 séquence sûre en concurrence (le Desktop fait `count + 1`) |
| ENT-02 | Date d'opération **jamais postérieure à aujourd'hui**, même en brouillon. | 🔄 « aujourd'hui » dans le fuseau du tenant |
| ENT-03 | Fournisseur obligatoire et actif (à la sélection). | ✅ |
| ENT-04 | Référence document (100), commentaire (500) optionnels. | ✅ |
| ENT-05 | Lignes : article actif, quantité > 0, prix unitaire ≥ 0, montant = q × p arrondi (demi supérieur). | ✅ |
| ENT-06 | Cycle `BROUILLON → VALIDÉE → ANNULÉE` ; le brouillon n'impacte pas le stock et reste modifiable (lignes remplacées). | ✅ |
| ENT-07 | Validation : ≥ 1 ligne ; un mouvement ENTRÉE par ligne ; CMUP recalculé ; l'entrée devient non modifiable. | ✅ + idempotence |
| ENT-08 | Annulation d'une entrée validée : **motif obligatoire (5 à 500 caractères)**, mouvement ANNULATION inverse lié au mouvement d'origine ; **refusée en totalité** si un seul stock devenait négatif ; CMUP inchangé. | ✅ |
| ENT-09 | Audit : création, modification, validation, annulation (motif en détail). | ✅ |

### 1.6 Sorties de stock

| Id | Règle Desktop | Web |
|---|---|---|
| SOR-01 | Motif obligatoire, choisi parmi des **motifs administrables** (libellé unique 150, description 500, actif/inactif, jamais supprimés). | ✅ par tenant |
| SOR-02 | Bénéficiaire/service (150), référence (100), commentaire (500) optionnels. | ✅ |
| SOR-03 | Lignes : article actif, quantité > 0 ; **coût unitaire = CMUP au moment de la validation**, figé sur la ligne. | ✅ |
| SOR-04 | Même cycle, validation et annulation que les entrées ; jamais de stock négatif. | ✅ |
| SOR-05 | Gestion des motifs réservée à l'administrateur. | ✅ (permission dédiée) |

### 1.7 Alertes

| Id | Règle Desktop | Web |
|---|---|---|
| ALR-01 | Stock faible : `stock ≤ stock minimum` (et stock > 0). | 🔄 par site |
| ALR-02 | Rupture : `stock = 0`. | 🔄 par site |
| ALR-03 | Indicateurs : nombre d'articles actifs, en stock faible, en rupture ; valeur du stock (stock × CMUP). | ⏭ 2.2 |

### 1.8 Permissions et rôles du Desktop

Permissions `ARTICLE_*`, `CATEGORY_*`, `SUPPLIER_*` (VIEW, CREATE, UPDATE, ACTIVATE,
DEACTIVATE), `STOCK_REASON_*`, `STOCK_ENTRY_*` / `STOCK_EXIT_*` (VIEW, CREATE, UPDATE,
VALIDATE, CANCEL), `STOCK_MOVEMENT_VIEW`, `INVENTORY_*`.
Rôles : Administrateur (tout), **Gestionnaire de stock** (catalogue + entrées/sorties +
mouvements + inventaires, **sans** motifs de sortie ni annulations), Vendeur, Consultation
(lecture du catalogue et des mouvements).

## 2. Adaptations nécessaires pour le Web

1. **Multi-tenant** : chaque table porte `tenant_id` (RLS, filtre ORM, FK composites) ;
   toutes les unicités sont **par tenant** ; identifiants UUIDv7.
2. **Multi-sites** : le catalogue (catégories, fournisseurs, articles) est **partagé** par
   les sites du tenant ; le **stock** et le CMUP ne sont plus des colonnes de l'article mais
   des niveaux **par (site, article)** ; les documents de stock sont rattachés à un site ;
   l'emplacement physique devient une donnée du site.
3. **Concurrence** : plusieurs utilisateurs et caisses agissent en même temps →
   verrouillage des lignes de stock (`SELECT … FOR UPDATE`, ordre déterministe),
   transition de statut conditionnelle et idempotente, numérotation par séquence en base
   (le `count + 1` du Desktop produit des doublons en concurrence).
4. **Unicités insensibles à la casse** (`lower(...)`) : « Boissons » = « boissons ».
5. **Dates** : « aujourd'hui » est évalué dans le fuseau horaire du tenant, pas celui du
   serveur.
6. **Permissions** : nommage `module.ressource.action` et nature (lecture/écriture…) pour
   la politique d'abonnement ; les rôles système suivent automatiquement les modèles de
   rôles (voir §4).
7. **API** : pagination et tri côté serveur, montants et quantités en chaînes décimales,
   erreurs à code stable.
8. **Audit** : chaque entrée conserve l'état avant/après des champs modifiés.

## 3. Modèle de données — sous-phase 2.1 (catalogue)

Modules : `catalog` (catégories, articles) et `suppliers` (fournisseurs), désormais
**disponibles**. `catalog` peut référencer un fournisseur quand le module `suppliers` est
effectif (lien optionnel, via le service public de `suppliers`).

| Table | Colonnes | Contraintes |
|---|---|---|
| `catalog_categories` | `id`, `tenant_id`, `name` (100), `is_active`, horodatages | unique `(tenant_id, lower(name))` |
| `suppliers` | `id`, `tenant_id`, `name` (150), `contact_name`, `phone`, `email`, `address`, `city`, `country`, `notes`, `is_active`, horodatages | nom non unique (SUP-02) |
| `catalog_articles` | `id`, `tenant_id`, `reference` (50), `designation` (255), `category_id`, `unit` (20), `main_supplier_id`, `purchase_price`, `sale_price` (`NUMERIC(18,2)` ≥ 0), `min_stock`, `max_stock` (`NUMERIC(18,3)`), `description` (1000), `barcode` (50), `is_active`, horodatages | unique `(tenant_id, lower(reference))` ; unique partiel `(tenant_id, barcode) WHERE is_active` ; `CHECK` prix ≥ 0, seuils ≥ 0, max ≥ min ; FK composites `(tenant_id, category_id)`, `(tenant_id, main_supplier_id)` |

RLS `ENABLE` + `FORCE`, droits `SELECT, INSERT, UPDATE` (pas de `DELETE` : jamais de
suppression physique).

## 4. Rôles système dynamiques

Les rôles système (Administrateur, Consultation, et désormais **Gestionnaire de stock**) sont
définis par des **modèles à motifs** (`role_templates.toml`). Leurs permissions sont
désormais **résolues à l'exécution** à partir du modèle : quand un module ajoute des
permissions (ex. `catalog.*`), l'Administrateur les obtient sans migration de données.
Un rôle modèle absent d'un tenant existant peut être ajouté par le tenant
(`POST /roles/from-template`).

## 5. Questions à trancher avant la sous-phase 2.2 (stock)

| # | Question | Recommandation |
|---|---|---|
| Q1 | **CMUP par site ou par tenant ?** | **Par site** (valorisation propre à chaque dépôt/boutique ; un transfert sort au CMUP du site source et entre à ce coût dans le site cible). |
| Q2 | **Seuils min/max par site ?** | Seuils par défaut sur l'article (livré en 2.1) + **surcharge optionnelle par site** (une boutique n'a pas les seuils du dépôt central). |
| Q3 | **Numérotation des documents** : par tenant ou par site ? | Par tenant et par type (`ENT-000001`), avec le code du site affiché à côté ; séquence en base, sûre en concurrence. |
| Q4 | **Transferts inter-sites** en 2.2 ou plus tard ? | Juste après entrées/sorties (2.3), réservés au plan ENTREPRISE via une **fonctionnalité** `stock.transfers` (ADR-0012). |
| Q5 | **Stock initial** (ART-12) : comment le saisir ? | Par une **entrée de stock** « Stock initial » (ou un ajustement tracé) sur le site choisi. |
| Q6 | **Précision du CMUP** : le Desktop l'arrondit à 2 décimales à chaque entrée. | Conserver 4 décimales pour le CMUP (`NUMERIC(18,4)`), montants toujours à 2 : évite la dérive des arrondis successifs. |
| Q7 | **Motifs de sortie par défaut** à la création d'un tenant ? | Oui : consommation interne, dotation, perte, casse, échantillon, autre (modifiables). |
