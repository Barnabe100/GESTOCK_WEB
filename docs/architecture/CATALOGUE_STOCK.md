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
| STK-05 | CMUP recalculé **uniquement sur une ENTRÉE** : `((stock_avant × CMUP) + (q × prix)) / (stock_avant + q)`. | ✅ (portée : Q1 ; un transfert entrant est une entrée pour le site destination, §8) |
| STK-06 | Sorties, ventes, ajustements, annulations ne modifient pas le CMUP ; pas de reconstruction rétroactive. | ✅ |
| STK-07 | Mouvements **immuables** (jamais modifiés ni supprimés) ; une correction = un nouveau mouvement. | ✅ (droits SQL : insertion seule) |
| STK-08 | Types : ENTRÉE, SORTIE, VENTE, AJUSTEMENT, ANNULATION. | ✅ (+ TRANSFERT SORTANT / ENTRANT, Phase 2.5) |
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

## 4. Rôles de base et rôles personnalisés

Les rôles de base (**Administrateur**, **Gestionnaire**, **Vendeur**, **Consultant**) sont
définis par des **modèles à motifs** (`role_templates.toml`) dont les permissions sont
**résolues à l'exécution** (ADR-0013) : quand un module ajoute des permissions, les rôles
concernés les obtiennent sans migration de données. Le Gestionnaire reprend la matrice du
Desktop pour le stock (ni annulation, ni gestion des motifs de sortie) ; le Vendeur ne reçoit
que des consultations tant que le module Ventes n'existe pas. Chaque entreprise crée ses
propres rôles personnalisés (ADR-0015). Un rôle de base absent d'un tenant peut être ajouté
par le tenant (`POST /roles/from-template`).

## 5. Décisions validées pour la sous-phase 2.2 (TechNova, 2026-09-24)

| # | Décision |
|---|---|
| Q1 | **CMUP par site.** Une sortie utilise le CMUP du site au moment de la sortie et ne le recalcule jamais. Le futur transfert sortira au CMUP du site source. |
| Q2 | Seuils min/max de l'article = valeurs par défaut ; **surcharge par site** prioritaire si elle existe. |
| Q3 | Numérotation **par entreprise** (`ENT-000001`, `SOR-000001`), sûre en concurrence. |
| Q4 | Transferts préparés en 2.2 (fonctionnalité de plan `stock.transfers`, ENTREPRISE), sans condition commerciale dans le code ; **réalisés en Phase 2.5** (§8). |
| Q5 | Stock initial = **entrée de stock normale** de type `INITIAL_STOCK` ; pas de champ sur l'article. |
| Q6 | CMUP calculé et stocké avec **4 décimales** ; montants à 2 décimales ; `Decimal` uniquement. |
| Q7 | Motifs système créés pour chaque entreprise : `CONSOMMATION_INTERNE`, `DOTATION`, `PERTE`, `CASSE`, `ECHANTILLON`, `AUTRE` (`is_system`, `is_active`), protégés. |

## 6. Plan de la sous-phase 2.2 — stock par site, entrées, sorties, mouvements, alertes

### 6.1 Modules

- `stock` devient **disponible** (dépend de `catalog`) : niveaux par site, seuils par site,
  motifs de sortie, entrées, sorties, journal des mouvements. Déclare la fonctionnalité
  `stock.transfers` (incluse dans le plan ENTREPRISE ; transferts réalisés en Phase 2.5, §8).
- `alerts` devient **disponible** (dépend de `stock`) : alertes de stock faible / rupture.
- Le stock accède au catalogue et aux fournisseurs uniquement par leurs API publiques
  (`catalog/api.py`, `suppliers/api.py`).

### 6.2 Tables (toutes tenant-scoped : RLS `ENABLE` + `FORCE`, FK composites `(tenant_id, …)`)

| Table | Rôle | Colonnes / contraintes clés |
|---|---|---|
| `document_sequences` | Compteurs de numérotation par entreprise | PK `(tenant_id, sequence_key)`, `next_value` ; plateforme (réutilisable : ventes…) |
| `stock_levels` | Stock et CMUP par (site, article) + surcharges de seuils | unique `(tenant_id, site_id, article_id)` ; `quantity NUMERIC(18,3) CHECK ≥ 0` ; `average_cost NUMERIC(18,4) CHECK ≥ 0` ; `min_stock`/`max_stock` surcharges nullables, `CHECK max ≥ min` |
| `stock_movements` | Journal **append-only** | type (`ENTRY`, `EXIT`, `CANCELLATION`, `SALE` — ventes, Phase 2.4, [`SALES.md`](SALES.md) ; `TRANSFER_OUT`, `TRANSFER_IN` — transferts, Phase 2.5 ; `ADJUSTMENT` — ajustements d'inventaire, Phase 2.6, [`INVENTORY.md`](INVENTORY.md)) ; `quantity` signée ≠ 0 ; `quantity_before/after` avec `CHECK after = before + quantity AND after ≥ 0` ; `unit_cost`, `average_cost_before/after` (4 déc.) ; `source_type`, `source_id`, `source_line_id`, `source_number` (numéro lisible, 2.4) ; `origin_movement_id` (annulation) ; utilisateur ; **unique `(tenant_id, source_line_id, movement_type, site_id)`** (par site depuis la 2.5) = garde anti double application |
| `stock_exit_reasons` | Motifs de sortie | `code` (système), `label` unique par tenant (casse ignorée), `is_system`, `is_active` |
| `stock_entries` / `stock_entry_lines` | Entrées | numéro unique par tenant ; `kind` `PURCHASE` \| `INITIAL_STOCK` ; `status` `DRAFT` → `VALIDATED` → `CANCELLED` ; site ; date ; fournisseur (obligatoire pour `PURCHASE`, `CHECK`) ; motif d'annulation obligatoire si annulée (`CHECK`) ; lignes : article unique par document, `quantity > 0`, `unit_cost ≥ 0` (2 déc.), `amount` |
| `stock_exits` / `stock_exit_lines` | Sorties | idem ; motif ; bénéficiaire ; lignes : `unit_cost` (4 déc.) et `amount` **figés à la validation** (CMUP du site) |

Droits du rôle applicatif : `stock_movements` en `SELECT, INSERT` seulement (immuable) ;
autres tables `SELECT, INSERT, UPDATE` (lignes de brouillon : `DELETE` autorisé pour le
remplacement des lignes d'un document **en brouillon**).

### 6.3 Stratégie transactionnelle et concurrence

1. Une requête = une transaction ; le service ne valide pas, l'endpoint appelle `commit()`.
2. Validation / annulation : **verrou du document** (`SELECT … FOR UPDATE`) puis contrôle du
   statut → une double validation concurrente échoue proprement (`409`).
3. `StockService.apply()` : crée au besoin les lignes `stock_levels` (`INSERT … ON CONFLICT DO
   NOTHING`), puis les **verrouille** (`SELECT … FOR UPDATE`) **dans l'ordre des identifiants
   d'article** (pas d'interblocage), contrôle la non-négativité, met à jour stock et CMUP,
   insère les mouvements — tout ou rien.
4. Garde en base : `CHECK quantity ≥ 0`, `CHECK after = before + quantity`, unicité
   `(source_line_id, movement_type)`.
5. Toute erreur annule tout : ni stock, ni mouvement, ni audit.

### 6.4 CMUP (par site, 4 décimales)

`CMUP = ((stock_avant × CMUP_avant) + (q × coût)) / (stock_avant + q)`, arrondi au
dix-millième (demi supérieur), **uniquement sur une ENTRÉE**. Sortie : coût unitaire = CMUP
du site, figé sur la ligne ; montant = `q × coût` arrondi à 2 décimales. Annulations :
mouvement inverse, CMUP inchangé (STK-06, pas de reconstruction rétroactive).

### 6.5 Numérotation

`document_sequences` : `INSERT … ON CONFLICT DO UPDATE SET next_value = next_value + 1
RETURNING` — atomique, sérialisé par verrou de ligne, annulé avec la transaction (pas de
numéro perdu sur échec). Format `ENT-000001`, `SOR-000001`, attribué à la création du
brouillon.

### 6.6 Site de l'opération

Si un site actif est sélectionné (`X-Site-Id`), l'opération porte sur ce site ; sinon le
`site_id` fourni doit être actif et accessible au membre. Un document d'un site non
accessible est introuvable (`404`) ; un document d'un autre site que le site sélectionné est
refusé (`403 site_mismatch`) — les rôles limités à un site ne s'appliquent que sur ce site.

### 6.7 Permissions (nature)

`stock.level.view` (R), `stock.threshold.manage` (W), `stock.movement.view` (R),
`stock.entry.view` (R) · `.create` · `.update` · `.validate` · `.cancel` (W),
`stock.exit.view` (R) · `.create` · `.update` · `.validate` · `.cancel` (W),
`stock.reason.view` (R), `stock.reason.manage` (A — administrateur), `alerts.stock.view` (R).
Gestionnaire de stock : tout sauf annulations et motifs (matrice Desktop).

### 6.8 Audit

`stock_entry.created|updated|validated|cancelled`, `stock_exit.*` (avec numéro, site,
totaux, motif d'annulation), `stock_threshold.updated`, `exit_reason.*` — dans la transaction
de l'opération (jamais d'audit d'une opération annulée par erreur).

### 6.9 Migrations et tests

Migration `0004` (tables, contraintes, RLS, droits) ; motifs système créés au provisioning et
ajoutés aux tenants existants par la migration (dans le contexte RLS de chaque tenant).
Tests : formule CMUP ; concurrence (validations simultanées sur un même article, numérotation
parallèle) ; idempotence ; rollback complet ; immutabilité SQL des mouvements ; règles
ENT/SOR ; stock initial ; seuils ; alertes ; permissions par rôle ; abonnement expiré ;
isolation SQL et API ; restriction par site.

## 7. Réalisation de la sous-phase 2.2 (2026-09-24)

Plan du §6 réalisé en cinq étapes (A : plan ; B : séquences, niveaux, `StockService` ;
C : motifs, entrées, sorties ; D : niveaux, seuils par site, journal, alertes ; E : écrans).
Choix de conception : [ADR-0014](../adr/0014-documents-et-mouvements-de-stock.md).

| Règle | Réalisation |
|---|---|
| STK-01 / Q1 | Stock et CMUP par (site, article) dans `stock_levels` ; `CHECK quantity ≥ 0` + contrôle du service (`insufficient_stock` détaillant les articles et le stock disponible) |
| STK-05 / Q6 | CMUP recalculé uniquement sur une entrée, 4 décimales (demi supérieur) ; sortie au CMUP du site, figé sur la ligne |
| STK-06 | Annulation par mouvements inverses, CMUP inchangé ; refusée si le stock deviendrait négatif |
| STK-07 | Journal append-only (droits `SELECT, INSERT` seulement), consultable et filtrable |
| ENT-* / Q5 | Entrées `PURCHASE` (fournisseur obligatoire) et `INITIAL_STOCK` ; brouillon → validée → annulée (motif obligatoire) |
| SOR-* / Q7 | Sorties avec motif actif ; six motifs système protégés (activables / désactivables, non renommables) ; motifs du tenant gérés par l'administration (`stock.reason.manage`) |
| Q2 | Surcharges par site (`PUT /stock/levels/{site}/{article}/thresholds`), prioritaires sur l'article |
| Q3 | `ENT-000001`, `SOR-000001` par entreprise (`document_sequences`, atomique, sans trou sur échec) |
| Q4 | Aucun transfert en 2.2 ; fonctionnalité `stock.transfers` déclarée (plan ENTREPRISE), types de mouvement réservés — transferts réalisés en 2.5 (§8) |
| ALR-01 / ALR-02 | Module `alerts` : rupture (`out`) et stock faible (`low`) des articles actifs, par site, compteurs |

Rôle de base **Gestionnaire** (ex-Gestionnaire de stock) : consultation, saisie et validation
des entrées et sorties, seuils par site, alertes ; **ni annulation ni gestion des motifs**
(administrateur).

### Points soumis à validation

1. Motifs système : désactivables mais non renommables (Q7 dit « protégés »).
2. Alertes : seulement pour les couples (site, article) déjà gérés (niveau existant) ;
   définir un seuil crée le niveau (rupture tant que non approvisionné) — ADR-0014 §5.
3. Un article au plus une fois par document.
4. Article inactif : refusé à la saisie et à la validation ; motif désactivé après la saisie
   d'un brouillon : validation acceptée.
5. `stock.threshold.manage` accordé au Gestionnaire.
6. Source polymorphe des mouvements sans clé étrangère (ADR-0014, Proposée). ADR-0013
   (rôles système dynamiques) : Acceptée le 2026-09-24 avec l'ADR-0015.

## 8. Transferts inter-sites (Phase 2.5)

Décisions : [ADR-0018](../adr/0018-transferts-inter-sites.md). Fonctionnalité de plan
`stock.transfers` (ENTREPRISE) : sans elle, aucune opération (création, modification,
validation, annulation : `403 feature_unavailable`, permissions correspondantes ni accordées ni
attribuables) ; la **consultation** de l'historique reste possible (entreprise rétrogradée
d'ENTREPRISE à STANDARD : transferts conservés, lecture seule, bandeau d'information).

| Id | Règle | Réalisation |
|---|---|---|
| TRF-01 | Un transfert appartient à l'entreprise ; site source et site destination du **même** tenant, **distincts**, actifs. | FK composites `(tenant_id, site)` ; `CHECK source_site_id <> destination_site_id` ; `same_site_transfer`, `site_access_denied` |
| TRF-02 | Cycle **brouillon → validé → annulé** ; numéro `TRF-000001` par entreprise (`document_sequences`). | Brouillon modifiable (destination, date, lignes ; source fixe) ; validé immuable |
| TRF-03 | Lignes : article existant, actif, du tenant, une fois par transfert ; quantité > 0 (3 déc.) ; au moins une ligne. | `duplicate_article_line`, `article_inactive`, `article_not_found`, `UNIQUE (transfer_id, article_id)`, `CHECK quantity > 0` |
| TRF-04 | Validation **atomique** : sortie du site source et entrée du site destination dans **une** transaction ; tout le stock source contrôlé avant la moindre écriture. | `StockService.transfer` ; `insufficient_stock` (avec `site_id`) → rien ne change, transfert toujours brouillon |
| TRF-05 | Mouvements `TRANSFER_OUT` (−q, source) et `TRANSFER_IN` (+q, destination), liés au transfert (`source_number` = `TRF-…`). | Journal filtrable par type et par numéro |
| TRF-06 | **CMUP** : sortie au CMUP du site source (inchangé) ; entrée au même coût, qui recalcule le CMUP destination (STK-05). | Coût (4 déc.) et valeur (2 déc.) figés sur les lignes |
| TRF-07 | **Annulation** : brouillon = abandon ; validé = mouvements inverses `CANCELLATION` (retrait destination, remise source) au coût du transfert, CMUP inchangés (STK-06) ; refus total si le stock destination ne suffit plus. | `transfer_already_cancelled` ; mouvements d'origine jamais modifiés |
| TRF-08 | **Concurrence** : double validation refusée (`409`) ; transfert et vente simultanés ne consomment jamais deux fois le même stock ; transferts croisés sans interblocage. | Verrou du transfert + verrous des niveaux dans un ordre global (site, article) |
| TRF-09 | **Sites du membre** : accès aux deux sites ; site sélectionné = l'un des deux ; permission détenue sur les deux sites. | `site_access_denied`, `site_mismatch`, `site_permission_denied` ; transfert touchant un site inaccessible : `404` |

Permissions (nature) : `stock.transfer.view` (R), `.create`, `.update`, `.validate`,
`.cancel` (W) — ces quatre dernières liées à la fonctionnalité `stock.transfers`. Rôles de base : Administrateur
tout ; Gestionnaire tout sauf l'annulation ; Consultant consultation ; Vendeur aucun accès.
Audit : `stock_transfer.created`, `.updated` (avant / après), `.validated` (statut précédent
/ nouveau, lignes, valeur), `.cancelled` (motif, `stock_restored`) — numéro, sites, lignes et
quantités, utilisateur, dans la transaction de l'opération.

Exemple : site A 10 u au CMUP 100, site B 5 u au CMUP 200, transfert de 3 u →
A = 7 u (CMUP 100), B = 8 u (CMUP (5 × 200 + 3 × 100) / 8 = 162,5) ; valeur transférée 300.
Annulation → A = 10 u (CMUP 100), B = 5 u (CMUP 162,5, inchangé : pas de reconstruction).

Hors périmètre : état « en transit » (expédition puis réception), inventaires, lots.

## 9. Lot 3-A — articles gérés ou non en stock, prix et coûts (ADR-0039)

| Règle | Web |
|---|---|
| `stock_managed` | Booléen de l'article, `true` par défaut (articles existants : `true`). `false` = article / service vendu **sans mouvement ni contrôle de stock**. |
| Article non géré | Absent des niveaux, seuils, alertes, candidats d'inventaire ; entrées, sorties, transferts, inventaires, seuils refusés (`article_not_stock_managed`). Garde centrale dans `StockService` (aucun niveau ni mouvement), drapeau lu sous verrou partagé de l'article. |
| Géré → non géré | Stock nul sur **tous** les sites du tenant (`article_has_stock`) ; verrou exclusif de l'article avant la vérification ; aucun mouvement ni ajustement automatique. |
| Non géré → géré | Autorisé ; aucun mouvement ; stock initial nul, alimenté par les opérations normales. |
| Actif / inactif | Indépendant de `stock_managed` ; un stock nul ne désactive jamais l'article (rupture). |
| Prix catalogue | `sale_price` / `purchase_price` modifiés (et fixés à la création) avec `catalog.article.price_update` seulement ; `catalog.article.update` = informations générales. |
| Historique des prix | Journal d'audit existant (`article.created` avec prix initiaux, `article.updated` avant / après) ; `GET /catalog/articles/{id}/price-history` ; aucune table dédiée. |
| Coûts internes | `catalog.article.cost_view` : sans elle, prix d'achat, CMUP, coûts unitaires, valorisations absents des réponses (catalogue, stock, inventaires, alertes, audit). |
| Code-barres au POS | Scan = égalité exacte, article actif (`GET /pos/articles/by-barcode`) ; inconnu : `barcode_unknown`. |

## 10. Lot 3-B — quantités décimales et conditionnements (ADR-0040)

| Règle | Web |
|---|---|
| Unité de base | Champ libre `unit` de l'article (aucun référentiel global) ; toujours vendable ; **le stock est toujours tenu dans cette unité**. |
| Quantités décimales | `decimal_quantity_allowed`, `false` par défaut (articles existants compris) : quantités vendues entières ; `true` : décimales (3 au plus). Contrôle serveur à l'enregistrement et à la validation d'une vente. Étendu aux entrées, sorties, transferts et inventaires au Lot 3-C (§11). |
| Conditionnement | Par article : nom libre (unique parmi les actifs), conversion vers l'unité de base `> 0` (décimale possible, entière pour un article entier), prix propre (aucune cohérence imposée avec prix × conversion), actif / inactif ; **jamais supprimé**. |
| Modification | Jamais utilisé : nom, conversion, prix, état. Utilisé par une vente (brouillon compris) : conversion **figée** (`packaging_in_use`) — désactiver et en créer un nouveau ; prix modifiable (les ventes passées gardent leur prix figé). |
| Droits | Aucune permission nouvelle : consultation `catalog.article.view` ; création, nom, conversion, état `catalog.article.update` ; prix `catalog.article.price_update`. |
| Prix non configuré | Conditionnement créé sans `price_update` (ou sans prix saisi) : prix **non configuré** (`NULL`), distinct d'un prix configuré à 0 ; invendable (absent du POS, non sélectionnable au back-office, `packaging_price_not_set` côté serveur) jusqu'à ce qu'un habilité fixe un prix. |
| Quantité de base | Quantité × conversion, **sans arrondi** ; au-delà de 3 décimales : refus (`base_quantity_precision`). Exemples : 2 × Carton 24 = 48 ; 1,5 × Sac 25,5 kg = 38,25 kg. |
| Stock | Contrôlé et mouvementé en unité de base (stock 50 : 2 cartons de 24 → reste 2 ; stock 40 : 2 cartons → `insufficient_stock`). Article non géré : ni contrôle ni mouvement, conditionnements utilisables. |
| Hors périmètre | Codes-barres multiples ou par conditionnement, images, lots / péremption, unités globales, tarifs avancés (conditionnements en entrée / sortie / transfert / inventaire : Lot 3-C, §11). |

## 11. Lot 3-C — conditionnements dans les opérations de stock (ADR-0041)

| Règle | Web |
|---|---|
| Présentation | Entrée, sortie, transfert et comptage d'inventaire : en **unité de base** ou dans un conditionnement **actif** de l'article (les conditionnements du 3-B, aucun second système ; prix sans effet). Une ligne par présentation (le même article peut figurer en cartons et en unités). |
| Quantité de base | Calculée par le serveur : quantité × conversion, sans arrondi (`base_quantity_precision` au-delà de 3 décimales) ; aucune quantité de base du client n'est reprise. Le **stock reste en unité de base** : `StockService` ne reçoit que des quantités de base. |
| Quantités entières | `decimal_quantity_allowed = false` : quantités saisies entières en entrée, sortie, transfert et comptage (`quantity_not_whole`), à l'enregistrement et à la validation. |
| Inventaire | Comptage en unité de base, ou conditionnements + vrac : 8 cartons de 24 + 5 bouteilles = **197** (calculé par le serveur) ; écart = physique − stock courant relu à la validation. |
| Coûts | **Validé** : en entrée, seul le coût de la présentation est saisi (12 000 le carton) ; le serveur calcule automatiquement le coût par unité de base (500 la bouteille, 4 décimales) qui alimente seul CMUP et valorisation. Sortie / transfert : CMUP par unité de base × quantité de base. Prix de vente du conditionnement indépendant du coût. |
| Historique | Lignes : présentation, conversion et quantité de base figées. Mouvements : présentation saisie (« -3 Carton 24 → -72 bouteille ») ; anciens mouvements et ajustements d'inventaire : sans présentation, affichés comme avant. |
| Désactivation | Conditionnement désactivé : plus proposé ; un brouillon ou un comptage qui l'utilise est refusé à la validation (`packaging_inactive`) jusqu'à correction. |
| Conversion | Figée dès qu'une vente, un document de stock (brouillon compris) ou un comptage l'utilise (`packaging_in_use`, port `catalog.usage_port`) ; revérifiée à la validation (`409 packaging_conversion_changed`). |
| Interface | Sélecteur de présentation par ligne et équivalences indicatives : « 2 Carton 24 = 48 bouteille », « 48 bouteille = 8 Pack 6 = 2 Carton 24 » (au plus 3 conditionnements, reste en unités de base). |
| Permissions | Aucune nouvelle : celles de chaque opération (`stock.entry.*`, `stock.exit.*`, `stock.transfer.*`, `inventory_count.inventory.*`). |
| Hors périmètre | Référentiel d'unités, lots / péremption, FIFO / FEFO, règles de prix, achats. |

## 12. Lot 3-D — codes-barres multiples et codes des conditionnements (ADR-0042)

| Règle | Web |
|---|---|
| Principe | Un code-barres identifie **une présentation** : l'article en unité de base, ou l'article + un conditionnement. |
| Codes | Code **principal** = champ `barcode` de l'article (inchangé, miroir dans le registre) ; codes **supplémentaires** de l'article ; codes de chaque **conditionnement** (plusieurs). Registre relationnel `catalog_barcodes`. |
| Unicité | Commune au tenant parmi les présentations **actives** (articles et conditionnements), contrôlée par le serveur et garantie en base ; deux tenants peuvent partager un code. |
| Inactifs | Élément désactivé : codes libérés (conservés) ; réactivation refusée si l'un d'eux a été repris (règle ART-16 étendue). |
| Format | Texte libre, 50 caractères ; aucune validation EAN imposée, aucune génération, pas de poids variable. |
| Scan | Égalité exacte, présentation active ; POS et vente : ajout direct (1 carton) ; entrées, sorties, transferts : article + présentation présélectionnés ; inventaire : ligne et présentation présélectionnées, quantité jamais devinée ; prix non configuré : refus à la vente seulement. |
| Recherche | « Contient » étendue à tous les codes (catalogue, stock, POS, inventaires, ventes) ; ne remplace jamais le scan. |
| Droits | Aucune permission nouvelle : `catalog.article.view` (consultation, scan), `catalog.article.update` (ajout, retrait). Audit `article.barcode_added/removed`, `packaging.barcode_added/removed`. |
| Hors périmètre | Images, lots, péremption, étiquettes, balances, génération d'EAN. |

