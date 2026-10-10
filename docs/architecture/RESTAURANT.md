# Restauration / Maquis — conception

> **Conception validée.** Décisions : [ADR-0049](../adr/0049-restauration-commandes.md)
> (arbitrages A1–A7, B1–B6, Z1–Z3, W1 ; décisions du palier R2 : D14). **Palier R1 (menu)
> livré** (migration 0041). **Palier R2 en cours** (commandes et règlement, absorbe l'ancien
> R3 ; R2-A livré : socle de plateforme ; R2-B livré : migration 0042 et moteur T1 ; R2-C livré : prise en charge, réattribution, modèles Serveur et Préparateur ; R2-D livré : migration 0043, règlement T2, Z3 ; R2-E livré : interface — suivi, saisie, fiche, règlement, réglages, ticket 80 mm sans prix) ; `restaurant.orders` reste `planned` jusqu'à la
> validation de R2-E (décision de l'utilisateur). Les autres modules `restaurant.*` restent `planned` (palier E.1) jusqu'à la livraison
> de leur palier.
> **Aucun palier ne commence sans validation explicite.** Les numéros de migration des paliers
> suivants sont indicatifs.

## 1. Vocabulaire

| Terme | Sens |
|---|---|
| Commande | Document du moteur unique (canaux personnel, POS, QR), numéro court par site et par jour |
| Ligne | Une présentation commandée (article en unité de base ou conditionnement), prix figé |
| Fonction dans la commande | Responsable, préparateur d'une ligne, personne qui sert / remet — **≠ rôle RBAC** |
| Réglée | La vente issue de la commande est **validée** (payée ou à crédit) |
| Close | Réglée ET toutes les lignes non annulées servies / remises — définitif |
| À régler | Ouverte et non réglée (affichage) |
| Poste | Poste de préparation du site (Cuisine, Bar…), donnée du site (module `restaurant.kitchen`) |
| Point QR | Point d'accès public (table ou comptoir) portant un jeton opaque |

## 2. Modules et activations

| Module | Dépendances obligatoires | Enrichissements facultatifs (testés par capacité) |
|---|---|---|
| `restaurant.menu` | `catalog` | `restaurant.kitchen` (poste par défaut d'un élément) |
| `restaurant.orders` | `restaurant.menu`, `sales` | `restaurant.tables`, `restaurant.kitchen`, `cash_register` (espèces), `customers` (crédit), `pos` (canal POS) |
| `restaurant.tables` | `restaurant.orders` | `restaurant.qr` (QR par table) |
| `restaurant.kitchen` | `restaurant.orders` | — |
| `restaurant.qr` | `restaurant.orders` | `restaurant.tables` |
| `restaurant.recipes` | `catalog`, `stock`, `sales` | — |

États d'un module sur un site : proposé par le profil, activé par défaut, facultatif, dans
l'offre, activé sur le site, **effectif** (seul état qui ouvre l'accès) — ADR-0049 D11.

- Livraison d'un module : sa migration remet à `false` ses activations existantes (sites
  existants désactivés jusqu'à activation explicite, D10) ; nouveaux sites : défauts du profil
  dans l'offre.
- `restaurant.kitchen` (« Postes de préparation ») : activé / désactivé par
  `organization.module.manage` ; jamais requis par les commandes ; désactivation refusée tant que
  des lignes routées vers un poste ne sont pas finales (`module_has_open_operations`).
- Désactiver `restaurant.orders` : refusé avec des dépendants activés (`module_has_dependents`) ou
  des commandes non finales (`module_has_open_operations`).
- Activations inertes des modules encore planifiés (défauts du profil) : ni lues ni modifiées ;
  elles ne bloquent jamais la désactivation d'un module livré (seuls les dépendants DISPONIBLES
  comptent dans `module_has_dependents`, R1). Ex. : le menu se désactive même si les commandes
  (planifiées) sont « activées » sur le site ; la migration de livraison des commandes (R2) les
  remettra à `false`.
- Lecture du menu (R1) : site sélectionné sans menu effectif → `403 module_unavailable`
  (`require_module` du routeur) ; sans site sélectionné, le service ne lit que les sites où le
  menu est effectif pour le membre (`has_site_permission(site, "restaurant.menu.view")`) ; les
  écritures sont revérifiées pour le site visé (`ensure_site_allows`).
- Réglages par site (mode de paiement, protection, délai entre prises, acceptation automatique
  QR) : créés à l'activation par le hook `site_setup` depuis `module_settings` du profil, jamais
  écrasés par un changement de profil.
- `site_setup` (socle R2-A) : appelé pour un module **disponible** quand il devient activé sur
  un site (création du premier site ou d'un nouveau site, activation manuelle, changement de
  profil du site), dans la même transaction ; crée seulement ce qui manque ; jamais à la
  désactivation, jamais pour un module planifié ; un échec annule l'activation. Désactiver ne
  supprime rien ; réactiver retrouve les réglages conservés (D14, N5).

## 3. Parcours par canal et par mode de paiement

T1 = création de la commande (numéro, prix figés ; ni vente, ni paiement, ni stock). T2 =
règlement (vente → validation = déduction du stock → paiements ; tout ou rien).

| Canal | « À la fin » | « À la commande » | Échec du règlement (T2) |
|---|---|---|---|
| Personnel | T1 → préparation → service ; T2 à tout moment | T1 puis T2 enchaînées par l'écran ; préparation après T2 | Commande « à régler », correction (annulation de ligne non réglée, entrée de stock…) puis nouvelle tentative, ou annulation motivée |
| POS | idem (canal `POS`) | idem | idem |
| QR | T1 (à confirmer, ou ouverte si acceptation automatique) → confirmation → préparation → T2 au comptoir | T1 → au comptoir : confirmation **puis** T2 → préparation | idem ; suivi public « à régler », sans aucune donnée de stock |

- Mode recopié sur la commande à sa création ; aucune dérogation par commande.
- T2 accepte la dérogation existante `expired_lot_override` (`sales.sale.expired_lot_override`,
  motif, audit), comme `POST /pos/checkout`.
- `POST /pos/checkout` et `SaleService.checkout` (POS ordinaire) sont inchangés.
- **Palier R2** (D14, Q2) : seul le canal **Personnel** (`STAFF`) est livré ; l'intégration à
  l'écran du POS est reportée. Le moteur est unique : un canal ultérieur (POS, QR) sera une
  autre route du même module, appelant le même service.

## 4. Modèle de données

Règles communes : `TenantScopedMixin`, RLS `ENABLE` + `FORCE` (`tenant_isolation`), clés
étrangères composites `(tenant_id, …)`, `UNIQUE (tenant_id, id)`, aucune suppression
(désactivation), `NUMERIC(18,2)` / `NUMERIC(18,3)`, droits minimaux dans chaque migration.

### R1 — Menu (livré, migration 0041)

- `restaurant_menu_sections` : `site_id`, `name` (100), `sort_order`, `is_active` ; nom unique
  par site, insensible à la casse (`uq_restaurant_menu_sections_site_name` ; création
  concurrente : `409 menu_section_name_taken`).
- `restaurant_menu_items` : `site_id`, `section_id`, `article_id`, `packaging_id` (NULL = unité
  de base), `display_name` (150), `description` (500), `sort_order`, `is_active`, `available`
  (épuisé manuel), `unavailable_reason` (200) ; `station_id` ajouté en R6. Aucun prix stocké
  (prix du catalogue courant) ; la présentation (article, conditionnement) ne change jamais.

```sql
CONSTRAINT uq_restaurant_menu_items_site_presentation
  UNIQUE NULLS NOT DISTINCT (tenant_id, site_id, article_id, packaging_id),
CONSTRAINT fk_restaurant_menu_items_packaging
  FOREIGN KEY (tenant_id, article_id, packaging_id)
  REFERENCES catalog_packagings (tenant_id, article_id, id) ON DELETE RESTRICT
```

PostgreSQL ≥ 15 (projet : 16) ; SQLAlchemy `postgresql_nulls_not_distinct=True`, comme
`uq_stock_movements_line_type_site_lot`. Contrainte totale (pas partielle) : une présentation
désactivée se réactive (même ligne, contrôles refaits) ; un nouvel ajout est refusé
`409 menu_item_exists` avec l'identifiant existant. Élément non commandable (calculé à chaque
lecture, motifs `blockers`) : élément ou section désactivé, épuisé, article inactif ou hors
assortiment actif du site, conditionnement désactivé ou sans prix configuré. Le menu ne bloque
jamais le catalogue (aucun port ; R2 enregistrera les commandes ouvertes).

### R2 — Commandes (migration 0042, sous-étape R2-B)

Précisions du palier R2 (D14) : `call_name` ≤ 40 caractères (Q4) ; `restaurant_order_events`
porte une `idempotency_key` facultative, unique par commande (ajout de lignes rejouable,
P-8) ; `restaurant_menu_items` reçoit `UNIQUE (tenant_id, site_id, id)` (cible de la clé
étrangère des lignes, seule retouche du schéma R1) ; `table_id` sans clé étrangère jusqu'à R5,
aucun `station_id` avant R6 ; `version` informatif (P-7) ; retour arrière refusé dès qu'une
commande ou une ligne de réglages existe (Q7, N5). Clé du compteur (décision R2-B) :
`ro:{site_id}:{AAAAMMJJ}`, 48 caractères (limite de 50, colonne inchangée), identifiant du
site (stable après un changement de code ou de nom), jamais préfixée par `{site_id}:`.


- `restaurant_site_settings` (une ligne par site) : `payment_timing` (`AT_END` | `AT_ORDER`),
  `claim_protection_minutes` (défaut 5), `claim_cooldown_minutes` (défaut 0), `qr_auto_accept`
  (défaut faux).
- `restaurant_orders` : `site_id`, `business_date`, `daily_number` (unique par entreprise, site,
  jour), `channel` (`STAFF` | `POS` | `QR`), `service_mode` (`ON_SITE` | `COUNTER` |
  `TAKEAWAY`), `table_id` (R5), `customer_id`, `call_name` (O2), `status`
  (`PENDING_CONFIRMATION` | `OPEN` | `CLOSED` | `CANCELLED` | `REJECTED`), `prep_status`
  (dérivé des lignes), `settlement_status` (`UNSETTLED` | `SETTLED`), `payment_timing`
  (recopié), `assigned_user_id` / `assigned_at` / `assigned_by`, `sale_id` (vente **active**,
  unique), `idempotency_key` (unique par site), `created_by`, `confirmed_*`, `closed_at`,
  `cancelled_*`, `cancel_reason` (CHECK : non vide si annulée), `version`. Index
  `(tenant_id, site_id, settlement_status, status, created_at)` pour l'ancienneté (§6).
  **Aucune copie de l'état financier** : payé / partiel / dû est lu sur la vente.
- `restaurant_order_lines` : `line_no`, `menu_item_id`, instantané (article, conditionnement,
  libellé, conversion, `unit_price` figé, `quantity`, `base_quantity`, `line_total`), `note`,
  `status` (`RECEIVED` | `IN_PREPARATION` | `READY` | `SERVED` | `CANCELLED`), `station_id` (R6),
  `prepared_by`, `ready_at`, `served_by`, `served_at`, `cancelled_*`, `cancel_reason` (CHECK).
- `restaurant_order_events` (ajout seul : `SELECT` + `INSERT`) : `CREATED`, `CONFIRMED`,
  `REJECTED`, `CLAIMED`, `REASSIGNED`, `LINES_ADDED`, `PREP_STARTED`, `READY`, `READY_REVERTED`,
  `SERVED`, `LINE_CANCELLED`, `SETTLED`, `SALE_CANCELLED`, `CLOSED`, `CANCELLED` ; acteur
  (`STAFF` | `PUBLIC` | `SYSTEM`), motif, lignes, poste ; index
  `(tenant_id, site_id, actor_user_id, event_type, occurred_at DESC)` (dernière prise, D7).
- Déclencheur `trg_restaurant_final_state` : refuse toute modification de l'état d'une ligne
  `SERVED` / `CANCELLED` et d'une commande `CLOSED` / `CANCELLED` / `REJECTED`, ainsi que toute
  modification de l'instantané d'une ligne.
- Catalogue : `business_profiles.module_settings` (JSONB, section générique des profils).
- Numéro : compteur `ro:{site_id}:{AAAAMMJJ}` (jour de l'entreprise ; D14, R2-B).

### R2-D — Ventes (module `sales`, migration 0043, ancien palier R3)

- `origin_type` / `origin_id` facultatifs, posés par le serveur seulement, **immuables**
  (déclencheur), jamais acceptés en entrée de l'API.
- ```sql
  CREATE UNIQUE INDEX uq_sales_active_origin ON sales (tenant_id, origin_type, origin_id)
    WHERE origin_id IS NOT NULL AND status <> 'CANCELLED';
  ```
- Canal `RESTAURANT` ajouté à la contrainte CHECK de `channel`.
- CHECK `origin_complete` (origine complète ou absente) et `restaurant_has_origin` ; déclencheur
  `sales_origin_immutable` ; FK composite `restaurant_orders (tenant_id, sale_id, site_id)` →
  `sales (tenant_id, id, site_id)` (livré, R2-D ; décisions : ADR-0049, « palier R2-D »).

### R4 à R8

- R4 `member_notifications` (plateforme) : `site_id`, `recipient_user_id`, `kind`,
  `source_type` / `source_id` (évènement), entité, `payload` sans donnée sensible, `read_at` ;
  unique (entreprise, destinataire, type de source, source) ; politique restrictive : lecture et
  mise à jour par le seul destinataire (`app.user_id`) ; purge à 30 jours par un rôle dédié.
- R5 `restaurant_zones`, `restaurant_tables` (libellé unique par site, places, actif).
- R6 `restaurant_prep_stations`, `restaurant_station_members` (affectation = filtre d'écran,
  jamais un droit) ; `station_id` sur éléments, sections (poste par défaut) et lignes ; routage :
  élément, puis section, puis flux général.
- R7 `restaurant_qr_points` (`token_hash` unique, rotation) ; politique `qr_token_lookup`
  (`current_setting('app.qr_token_hash')`) ; référence publique et secret de suivi haché sur la
  commande.
- R8 recettes (fiches versionnées, ingrédients en unité de base) ; source des mouvements à
  définir pour respecter la garde d'unicité des mouvements (O10).

## 5. Machines à états et refus

| Ligne : de → vers | Permission | Condition |
|---|---|---|
| RECEIVED → IN_PREPARATION | `restaurant.orders.order.prepare` | commande ouverte ; « à la commande » : réglée |
| IN_PREPARATION → READY | `prepare` | — |
| READY → IN_PREPARATION | `prepare` | correction (`READY_REVERTED`) |
| READY → SERVED | `restaurant.orders.order.serve` | — |
| RECEIVED → CANCELLED | `restaurant.orders.order.cancel` | commande **non réglée** ; motif |
| IN_PREPARATION, READY → CANCELLED | `restaurant.orders.order.cancel_prepared` | commande **non réglée** ; motif |
| SERVED, CANCELLED → * | — | interdit (A7), déclencheur |

Sans postes : une action porte sur toutes les lignes ouvertes de la commande (un évènement) ;
avec postes : sur les lignes d'un poste (un évènement par poste). Le palier R2 permet aussi
d'agir **ligne par ligne** (P-2 : une boisson servie avant le plat), un évènement par requête.
Le passage à « close » est calculé sous le verrou de la commande. Une commande dont toutes les
lignes sont annulées n'est pas réglable (`409 order_empty`) ; elle s'annule avec
`restaurant.orders.order.cancel` et un motif (N4).

Refus : `order_not_settled`, `order_settled` (ajout ET annulation de lignes, annulation de la
commande), `order_empty` (règlement d'une commande sans ligne), `order_has_served_lines`,
`order_closed`, `sale_origin_unavailable`, `sale_origin_active`, `idempotency_key_reused`,
`assignee_not_eligible`, `order_claim_protected`
(prise pendant la protection), `claim_cooldown_active` (délai entre prises), et les refus existants des
ventes, paiements, caisse et stock (`insufficient_stock`, `insufficient_unexpired_stock`,
`article_inactive`, `article_not_in_site_assortment`, `cash_session_closed`,
`sale_has_payments`…).

### Annulation de la vente d'une commande (Z3, B4)

1. Commande close : refus `order_closed`.
2. Commande réglée non close : annulation de chaque paiement (`sales.payment.cancel` ; espèces :
   sortie inverse dans la session d'origine ouverte), puis de la vente (`sales.sale.cancel`) par
   une personne habilitée ; le port d'origine verrouille la commande **avant** la vente, la
   repasse « à régler » et écrit `SALE_CANCELLED` dans la même transaction.
3. Annulation de la ligne voulue, avec motif.
4. Nouveau règlement aux prix figés : nouvelle vente, nouveau numéro à la validation.

Cohérence vérifiée :

- **Stock** : un `CANCELLATION` par mouvement `SALE` d'origine (même quantité, même coût, même
  lot, y compris périmé) ; la nouvelle vente a de nouvelles lignes, donc de nouveaux mouvements
  `SALE` (aucun conflit avec la garde d'unicité) consommés par `StockService.consume` (FEFO).
- **Autres lots** : si la nouvelle vente consomme d'autres lots, le stock total reste exact et
  Σ lots = stock est maintenu ; la répartition par lot peut différer du physique — correction
  par l'inventaire par lot existant. Lot d'origine périmé et aucun autre lot :
  `insufficient_unexpired_stock`, commande conservée, dérogation `expired_lot_override` possible.
- **CMUP** : aucun coût par lot ; annulation au coût de la sortie, CMUP inchangé (STK-06) ;
  nouvelle sortie au CMUP courant du site, quel que soit le lot.
- **Vente active unique** : `uq_sales_active_origin` ; règlements sérialisés par le verrou de la
  commande.
- **Caisse et paiements** : paiements annulés conservés ; espèces rendues dans la session
  d'origine ; nouveaux paiements dans la session ouverte de l'encaisseur.

### Commande close et paiements

Annuler un paiement laisse la vente validée (règle existante) : la commande reste close, le
reste dû est une créance calculée selon les règles existantes (aucun nouveau contrôle de limite),
un nouveau paiement s'enregistre sur la même vente (sans surpaiement). Les routes de paiement
n'appellent jamais la commande. Aucune voie de réouverture : ni changement d'état, ni ajout ou
annulation de ligne, ni annulation de la vente, ni écriture SQL directe (déclencheur), ni
changement de profil ou de module, ni console / CLI.

## 6. Limites connues de V1

| Cas | Comportement V1 |
|---|---|
| Commande servie non réglée sans client à créditer (Z1) | Reste ouverte, aucune action automatique ; la perte n'existe pas en V1 (conçue séparément) |
| Remboursement d'espèces après clôture de la session d'origine (W1) | Règle de caisse conservée (`cash_session_closed`) ; aucun remboursement ; commande inchangée |

Ces commandes restent **identifiables** (numéro, jour, table ou mode, responsable, lignes
servies, montant à régler aux prix figés) et leur **ancienneté est visible** (depuis la création
et depuis le dernier service, dans le fuseau de l'entreprise ; filtre « servies non réglées »
trié par ancienneté sur l'écran « À régler »). Effets connus : elles bloquent le changement de
profil du site (commandes nommées dans le détail), la désactivation de `restaurant.orders` et les
opérations du catalogue concernées (ports).

## 7. Ordre global des verrous

1. Verrou consultatif de l'employé (site + employé), pour toute action « Prendre » (R2-C : le
   réglage du délai n'est lu qu'ensuite, sous le verrou des réglages).
2. Réglages du site, en partage (le changement de profil les prend en exclusif).
3. Point QR, en partage (R7).
4. Commande (exclusif).
5. Lignes (exclusif, par numéro).
6. Vente, paiements, caisse (ordre interne de `SaleService`).
7. Stock (article → assortiment → niveaux → lots, `StockService`).
8. Insertion des notifications (aucun verrou).

Annulation d'une vente d'origine Restauration : le port verrouille la commande avant la vente.
Les routes de paiement ne verrouillent que la vente. Le verrou exclusif du site (changement de
profil, ADR-0048) n'est jamais pris par une commande.

## 8. Permissions et rôles

| Permission | Nature | Admin. | Gestionnaire | Vendeur | Serveur | Préparateur | Consultant |
|---|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `restaurant.menu.view`, `restaurant.orders.order.view` | read | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `restaurant.menu.availability` | write | ✓ | ✓ | ✓ | ✓ | ✓ | |
| `restaurant.orders.order.create`, `.claim`, `.serve`, `.confirm` | write | ✓ | ✓ | ✓ | ✓ | | |
| `restaurant.orders.order.prepare` | write | ✓ | ✓ | ✓ | | ✓ | |
| `restaurant.orders.order.cancel` (lignes reçues, commandes) | write | ✓ | ✓ | ✓ | | | |
| `restaurant.orders.order.cancel_prepared`, `.reassign` | admin | ✓ | ✓ | | | | |
| `restaurant.menu.manage`, `restaurant.orders.settings.manage` ; gestion des tables (R5), des postes (R6), des points QR (R7) | admin | ✓ | ✓ | | | | |
| Recettes (R8) : consultation / gestion | read / admin | ✓ | ✓ / ✓ | | | | ✓ / |
| Encaisser : `sales.sale.create`, `.validate`, `sales.payment.create` (existantes) | write | ✓ | ✓ | ✓ | **jamais** | | |
| Annuler paiement / vente : `sales.payment.cancel`, `sales.sale.cancel` (existantes) | write | ✓ | rôle perso. | rôle perso. | rôle perso. | | |
| Activer un module : `organization.module.manage` (existante) | admin | ✓ | | | | | |
| `organization.notification.view` | read | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

- **Nommage (D14, R2-B)** : toute permission est préfixée par le code EXACT de son module
  (`restaurant.orders.order.*`, `restaurant.orders.settings.manage`) ; les modules suivants
  suivront `restaurant.tables.<ressource>.<action>`, `restaurant.kitchen.…`,
  `restaurant.qr.…`, `restaurant.recipes.…`, noms fixés à la livraison de leur palier.
- Un module planifié n'accorde aucune permission, même déclarée (registre).
- Union des rôles de l'utilisateur pour le site ; jamais de test d'un nom de rôle.
- Modèles de rôles = données complétées à chaque livraison : Gestionnaire `restaurant.*` ;
  Vendeur liste explicite (« sans annulation » vise les ventes et documents validés, pas les
  lignes non réglées) ; Consultant `*.view` automatiquement.
- Serveur et Préparateur : `auto_provision = false`, créés à la demande
  (`POST /roles/from-template`) ; un rôle personnalisé du même nom fait refuser la création
  du modèle (`409 role_name_taken`), sans renommage automatique (D14, Q5).
- `restaurant.orders.order.confirm` est déclarée au palier R7, à son premier usage (P-12).
- État financier d'une commande (numéro de vente, payé / partiel / dû) visible avec
  `restaurant.orders.order.view` ; détail des paiements réservé à `sales.payment.view` (P-11).
- Réattribution : nouveau responsable détenteur de `restaurant.orders.order.claim` effectif sur le site
  (appartenance active, compte actif, site accessible, capacités résolues pour ce site), motif
  obligatoire, immédiate ; « Prendre » respecte la protection et le délai du site (ADR-0049,
  décisions du palier R2-C).
- Codes des modèles facultatifs : `waiter` (Serveur), `preparer` (Préparateur) ; Vendeur :
  `restaurant.orders.order.claim` en plus des permissions de R2-B.

## 9. Notifications

Voir ADR-0049 D8. Destinataires calculés par `CapabilityService.resolve` pour chaque membre
candidat ayant accès au site (borné par l'équipe du site). Granularité sans / avec postes,
clé anti-doublon, résolution calculée à la lecture, interrogation 10–15 s, purge à 30 jours
(rôle de purge : `SELECT (id, created_at)` + `DELETE` des lignes de plus de 30 jours sous une
politique restrictive). Une annulation de vente ou de paiement ne notifie personne.

## 10. QR

Voir ADR-0049 D9. Espace `/api/v1/public/…` ; entreprise et site déduits du jeton haché
(politique `qr_token_lookup`) ; limitation de fréquence persistante (`RateLimiter`) ; idempotence
de la création ; plafonds (O9) ; réponses sans coût, stock, caisse ni donnée interne ; module
effectif et site actif revérifiés à chaque appel. Un site STANDARD déjà licencié n'obtient le QR
qu'à sa prochaine licence.

## 11. Plan de migrations

| Migration | Palier | Contenu |
|---|---|---|
| 0041 | R1 (livrée) | menu (contrainte `NULLS NOT DISTINCT`, FK composite), RLS, droits ; remise à `false` de `restaurant.menu` ; descente refusée si un menu a été saisi |
| 0042 | R2 (R2-B, livrée) | réglages, commandes, lignes, évènements, CHECK des motifs, déclencheur d'états finaux, index d'ancienneté, `business_profiles.module_settings`, `UNIQUE (tenant_id, site_id, id)` des éléments de menu ; remise à `false` de `restaurant.orders` ; descente refusée si une commande ou des réglages existent |
| 0043 | R2 (R2-D, livrée) | origine des ventes, index partiel, déclencheur d'origine, canal `RESTAURANT`, FK `restaurant_orders.sale_id` ; descente refusée si une vente issue d'une commande existe |
| 0044 | R4 | `member_notifications`, politiques RLS, droits du rôle de purge |
| 0045 | R5 | zones, tables, FK `restaurant_orders.table_id` ; remise à `false` de `restaurant.tables` |
| 0046 | R6 | postes, affectations, `station_id` ; remise à `false` de `restaurant.kitchen` |
| 0047 | R7 | points QR, `qr_token_lookup`, champs publics ; remise à `false` de `restaurant.qr` |
| 0048 | R8 | recettes et source des mouvements de consommation |

Hors migrations (données versionnées, à la livraison de chaque palier) : passage de `planned` à
un manifeste réel, dépendances (D1), modèles de rôles (`auto_provision`), profils (D11,
`module_settings`), plan STANDARD (`restaurant.qr` en R7). Chaque migration : montée, descente,
remontée, `alembic check` ; tests de migration sur le modèle de `test_site_modules.py`.

### Retour arrière de la migration 0041 (R1)

- **Condition de refus** : au moins une ligne dans `restaurant_menu_sections` ou
  `restaurant_menu_items`, **toutes entreprises confondues**. Le comptage se fait hors RLS le
  temps du contrôle (`NO FORCE ROW LEVEL SECURITY`, rôle propriétaire de la migration), puis
  `FORCE ROW LEVEL SECURITY` est rétablie.
- **Refus** : erreur « Retour arrière refusé : N section(s) ou élément(s) de menu saisis par les
  utilisateurs seraient perdus. » La transaction de la migration est annulée : rien n'est
  supprimé ni modifié.
- **Descente possible** lorsqu'aucune section ni aucun élément de menu n'existe : les tables,
  index et droits du menu sont alors supprimés.
- **Non restauré** : les activations `restaurant.menu` remises à `false` à la montée (lignes
  inertes de la période « Bientôt disponible », D10) ; ce ne sont pas des données métier.
- **Aucune procédure de suppression n'est fournie** (ni par l'application, ni par la console, ni
  par la CLI) : un retour arrière ne supprime jamais silencieusement des données métier. La
  descente n'est envisageable que sur une base sans menu saisi (par exemple un environnement de
  test).
- **Tests de référence** (`backend/tests/test_restaurant_menu_migration.py`) :
  `test_migration_0041_downgrade_refuses_to_lose_a_menu` (refus, section conservée, RLS
  rétablie) et `test_migration_0041_disables_the_menu_on_existing_sites_only` (descente vers
  0040 sans menu saisi, puis remontée).

## 12. Plan de tests

- **Concurrence** : deux « Prendre » simultanés (un seul succès) ; protection et délai entre
  prises ; transitions croisées ; double règlement ; règlement concurrent d'une modification de
  stock ; changement de profil concurrent d'une création ; annulation de vente concurrente du
  dernier « servi » (soit close et refus, soit annulation et non close ; aucun interblocage).
- **Idempotence** : création par canal, règlement rejoué (une vente), notification rejouée.
- **RLS et isolation** : SQL et API pour chaque table ; site A ↔ site B ; entreprise A ↔ B ;
  notifications d'autrui illisibles ; jeton QR limité à son site.
- **Menu (migration 0041)** : doublon unité de base (NULL) refusé avec le nom de contrainte ;
  doublon conditionnement refusé ; unité de base + conditionnement acceptés ; autre site / autre
  entreprise acceptés ; conditionnement d'un autre article refusé (FK) ;
  `pg_index.indnullsnotdistinct = true` ; descente vers 0040 puis remontée ; deux ajouts
  simultanés → 201 + `409 menu_item_exists`.
- **Paiement (A1)** : par canal et par mode, échec de T2 (stock, lot, session fermée, moyen de
  paiement, limite de crédit) → commande conservée, ni vente, ni paiement, ni mouvement ; nouvelle
  tentative réussie ; « à la fin » : règlement avant service accepté ; « à la commande » :
  `order_not_settled` ; réglage modifié sans effet sur les commandes ouvertes ; suivi QR sans
  stock ; `expired_lot_override` accepté avec permission et motif.
- **Prix et non-régression POS** : prix modifié après commande → vente au prix figé ; vente sans
  origine toujours soumise à `sale_prices_changed` ; origine envoyée par le client ignorée ; suite
  POS / ventes / caisse / stock existante verte.
- **Annulations (A2, A3, Z3)** : motif vide → 422 ; permission selon l'état ; `order_settled`,
  `order_has_served_lines` ; aucune tâche d'expiration ; procédure Z3 complète (paiements annulés
  avec sortie de caisse inverse, `CANCELLATION` sur les mêmes lots, CMUP inchangé, commande « à
  régler », ligne annulée, nouveau règlement aux prix figés, une seule vente active sous
  concurrence) ; nouvelle vente consommant un autre lot (lot d'origine périmé) : stock total
  exact, coût = CMUP, refus `insufficient_unexpired_stock` puis dérogation.
- **Commande close** : transition depuis `SERVED` → 409 ; réouverture, ajout et annulation de
  ligne refusés ; annulation de la vente → `order_closed` ; paiement annulé → commande close,
  créance visible, nouveau paiement sans surpaiement, aucun évènement de commande ; `UPDATE` SQL
  direct refusé par le déclencheur ; vente d'origine sans port → `sale_origin_unavailable`.
- **Limites V1 (Z1, W1)** : commande servie non réglée jamais fermée automatiquement, ancienneté
  et filtre exacts, nommée dans le détail d'un changement de profil refusé ; annulation d'espèces
  après clôture de session → `cash_session_closed`, commande inchangée.
- **Notifications (A4)** : responsable + titulaires effectifs de `serve` − auteur ; responsable
  auteur exclu, autres notifiés ; membre sans `order.view` écarté ; rôle limité à un autre site
  écarté ; rôle personnalisé avec `serve` inclus ; aucun doublon ; granularité avec / sans postes ;
  résolution calculée ; purge.
- **Modules et rôles (A5, A6, Z2)** : Postes activés / désactivés par l'Administrateur,
  Gestionnaire 403, `module_not_offered`, `module_not_implemented` (avant R6),
  `module_dependency_missing`, `module_has_dependents`, `module_has_open_operations` ; commandes
  sans Postes ; sites existants désactivés à chaque livraison ; Serveur qui encaisse → 403 ;
  Serveur + Vendeur sur A encaisse sur A, pas sur B ; nouvelle entreprise (commerce et
  restauration) sans rôles Serveur / Préparateur ; création à la demande.
- **E2E** (bureau et mobile, rejoués deux fois) : maquis sans table (client qui revient),
  restaurant avec postes, POS, QR avec confirmation.

## 13. Décisions ouvertes

| Id | Sujet | Palier |
|---|---|---|
| O1 | ~~Communication du numéro au client~~ — tranché (D14, Q3) : numéro affiché, ticket de retrait 80 mm sans prix ; page QR en R7 | R2 |
| O2 | ~~Nom d'appel facultatif~~ — tranché (D14, Q4) : 40 caractères, jamais à la place du numéro | R2 |
| — | ~~Conflits de noms des modèles Serveur / Préparateur~~ — tranché (D14, Q5) : `409 role_name_taken` | R2 |
| — | Indicateur de stock à la prise de commande (reporté, D14, Q6) | après R2 |
| — | Intégration du canal POS à l'écran du point de vente (reportée, D14, Q2) | après R2 |
| O7 | Perte de stock d'une ligne préparée puis annulée | R6 |
| O8 | Déplacement d'une commande entre tables | R5 |
| O9 | Plafonds QR (lignes, quantités, commandes ouvertes par point) | R7 |
| O10 | Source des mouvements des recettes (garde d'unicité) | R8 |
| — | Procédure de perte (Z1) et remboursement d'espèces après clôture (W1) | à concevoir séparément |
| — | Addition scindée, remises, options payantes | après V1 |

## 14. Critères d'acceptation

- **R1 Menu** : menu par site limité à l'assortiment actif ; conditionnement sans prix refusé ;
  épuisé visible en 15 s au plus ; unicité de présentation garantie en base ; isolation SQL et
  API ; audit ; module désactivé sur les sites existants ; dépendance orders → tables retirée.
- **R2 Commandes** : six cas (table, comptoir, à emporter, sans responsable, sans client, client
  qui revient) de bout en bout ; numéro unique par site et par jour sans figer le code du site ;
  100 « Prendre » simultanés → exactement 1 succès ; protection et délai respectés ; historique
  en ajout seul ; écran en trois colonnes avec recherche par numéro ; commandes non finales =
  travail en cours ; aucun contournement de l'état close ; limites V1 identifiables avec leur
  ancienneté.
- **R2 Règlement (ancien R3)** : une vente active par commande, même rejouée ; prix figés ; POS
  ordinaire identique ; échec du règlement sans perte de commande pour chaque canal et chaque
  mode ; procédure Z3 cohérente (caisse, stock, paiements, vente active unique) ; état
  financier lu sur la vente.
- **R2, compléments (D14)** : ticket de retrait 80 mm sans prix, sans dépendance au POS ; un
  seul moteur, canal posé par la route ; retours arrière de 0042 et 0043 protégés et testés ;
  réglages conservés par une désactivation ; `restaurant.orders` jamais utilisable dans un
  commit où le règlement ou l'interface n'existe pas.
- **R4 Notifications** : avis en 15 s au plus ; destinataires exacts ; jamais l'auteur ; aucun
  doublon ; résolution calculée ; purge à 30 jours ; aucune lecture croisée.
- **R5 Tables** : occupation exacte ; un site sans tables n'est pas affecté.
- **R6 Postes** : routage et un avis par poste ; un site sans poste inchangé ; activation par
  l'Administrateur.
- **R7 QR** : batterie de sécurité verte ; confirmation par défaut ; QR dans STANDARD.
- **R8 Recettes** : consommation par `StockService.consume` ; CMUP et lots inchangés ; garde
  d'unicité résolue.
- **R9 Consolidation** : E2E des trois canaux rejoués deux fois ; isolation, concurrence, CI
  verte ; documentation alignée ; aucun module disponible non livré.
