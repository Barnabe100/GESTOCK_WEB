# CLAUDE.md — Consignes pour les assistants IA

Ce fichier guide Claude (et tout assistant) travaillant sur **StockManager Web**
(dépôt `GESTOCK_WEB`, éditeur TechNova). À lire avant toute modification.

## Projet

Plateforme SaaS **multi-tenant, multi-sites** de gestion commerciale.
Architecture : **Core commun + profils d'activité + modules spécialisés**.
Référence complète : [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md)
et [`docs/adr/`](docs/adr/README.md).

**Phase actuelle : 3 — ventes et encaissement.** Phase 3.2 (SaaS) clôturée : 3.2-A livrée —
référentiel des pays (`countries.toml` → `geo_countries`), informations d'entreprise, paramètres
commerciaux des plans (TechNova, jamais écrasés par `catalog sync`), inscription publique
(`POST /public/signup` : compte + entreprise sans site, propriétaire **et** administrateur,
abonnement `trial` ou **`pending_activation`** — accès administratif seulement ; chaîne
plan → abonnement → paiement → licence → activation, ADR-0025 ; limitation de fréquence
persistante, réponse générique si l'e-mail existe) ; 3.2-B livrée — onboarding persistant
(`onboarding_steps`, `GET /onboarding`, page `/onboarding` et bandeau du tableau de bord) :
étapes déclarées par les manifestes des modules, **validation automatique** sur les données
réelles, statuts `NOT_STARTED` → `IN_PROGRESS` → `COMPLETED` (définitif, jamais régressé ; pas
de `SKIPPED`), terminé = étapes obligatoires faites, **onboarding ≠ activation** de
l'abonnement, champs obligatoires marqués `*` en cohérence avec le backend
([ADR-0026](docs/adr/0026-onboarding-persistant.md)) ; 3.2-C livrée — page Entreprise complète
(obligatoires `*` : nom, pays du référentiel, devise figée affichée ; recommandées ;
facultatives), **le `Tenant` est la source unique de l'identité de l'entreprise** : en-tête
documentaire construit par le serveur (`GET /tenant/document-identity`, jamais « N/A »), aperçu
sur la page, futur moteur de reçus ([ADR-0027](docs/adr/0027-identite-documentaire.md)) ;
3.2-D livrée — administration des utilisateurs (`/members` paginé : recherche, filtres statut /
rôle / site ; ajout avec réutilisation du compte global ; accès = rôles, sites, statut ;
`POST /members/{id}/activate|deactivate` ; identité globale en lecture seule, aucune
réinitialisation de mot de passe par le tenant ; audit dédié ; ADR-0029) ; 3.2-E livrée — délégation RBAC **calculée par le serveur**
(`GET /permissions/delegable`, `GET /roles/delegable`, `RoleOut.delegable`) sur la même base
que l'anti-escalade : permissions qu'un rôle accorde réellement dans l'offre du tenant ;
permissions hors offre conservées, jamais ajoutées ; l'interface n'en décide jamais (ADR-0030) ;
3.2-F livrée — **console TechNova** (`backend/app/console/`, `frontend/src/console/`,
[`TECHNOVA_CONSOLE.md`](docs/architecture/TECHNOVA_CONSOLE.md), ADR-0031) : processus distinct
`app.console.main` (`/platform-api/v1`, interface `/tech-admin`), rôle SQL
`stockmanager_platform` aux droits minimaux (**aucune donnée de tenant**), administrateurs
TechNova = comptes dédiés `users.is_platform_admin` attribués **par la CLI seulement**
(`stockmanager platform-admin`) et invisibles pour l'application des tenants (RLS), journal
`platform_audit_logs` append-only, catalogue technique en **lecture seule** (TOML / code),
**paramètres commerciaux** des plans modifiables (raison obligatoire, confirmation, audit
avant / après) et lus par `/public/plans` ; 3.2-G livrée — **tenants et abonnements** dans la
console : métadonnées plateforme (colonnes limitées, compteurs ; jamais de données métier),
statut du tenant ≠ statut de l'abonnement, suspension / réactivation, **activation manuelle
transitoire** (`pending_activation`/`trial` → `active`, **aucun paiement**), prolongation,
changement de plan (prix figé au nouveau tarif, rien de rétroactif), **double audit** dans la
même transaction (plateforme + entrée miroir du tenant) ; 3.2-H — **phase 3.2 clôturée** (revue
RLS / droits SQL / migrations / documentation, E2E rejouée deux fois). **Phase 3.3 en cours :
3.3-A livrée — paiements d'abonnement** (ADR-0032) : `SubscriptionPayment`
(`subscription_payments`, distinct des paiements des ventes) déclaré par l'entreprise
(`POST /subscription/payments`, permission `subscription.payment.declare` de nature `billing`,
idempotent, devise fixée par le serveur, aucun champ de décision accepté du client) ; décision
**définitive** (`PENDING` → `CONFIRMED` | `REJECTED`) par TechNova seule dans la console
(`/payments/{id}/confirm|reject`, verrou, raison obligatoire — motif du rejet visible par
l'entreprise —, double audit ; rôle SQL de la console limité aux colonnes de décision) ;
**Payment CONFIRMED ≠ activation** : rien n'est activé. **3.3-B livrée (licences)** : B1 livrée —
**1 site = 1 abonnement** (ADR-0033) : `subscriptions.site_id` (abonnement d'inscription rattaché
au premier site), nouveau site = abonnement `pending_activation` au plan publié choisi (pas
d'essai), capacités **par site** (sans site : union des abonnements), toute écriture sur un site
revérifiée pour l'abonnement de CE site (`ensure_site_allows`), limites par site (`max_users`),
console par abonnement de site. B2 livrée — **licences** (ADR-0034,
[`LICENSING.md`](docs/architecture/LICENSING.md)) : **Signing Service séparé**
(`signing-service/`, hors Compose de l'application, Ed25519, demandes HMAC + nonce) **seul
détenteur de la clé privée** — jamais dans ce dépôt, l'API, la console, React, PostgreSQL, les
images, la CI ni les tests (clés éphémères) ; `.lic` v1 (forme canonique, `key_id`, trousseau
**public** versionné avec rotation) ; génération dans la console depuis un paiement
**CONFIRMED** seulement (période calculée par le serveur, contiguë au renouvellement ; postes
`requested_activations` confirmés ou ajustés, figés), signature vérifiée avant enregistrement,
abonnement du site aligné ; la licence en vigueur fige modules / fonctionnalités / limites
(`PlanTerms`) ; révocation **définitive** (site suspendu sans autre couverture), réémission =
nouvelle licence ; table `licenses` immuable (déclencheur), l'entreprise ne fait que lire.
B3 livrée — **postes** (ADR-0035) : `license_activations`, activation par l'installation
cliente (`installation_id` aléatoire, jamais MAC / processeur / IP ; le Web n'active jamais de
navigateur) sur le site sélectionné, sous la licence **en vigueur**, quota `max_activations`
**par abonnement de site** sous verrou, idempotente, refus distincts journalisés
(« Le nombre maximal de postes autorisés pour ce site est atteint. ») ; contrôle de présence
(`check-in`, durée hors ligne tolérée) ; libération (entreprise ou TechNova) = une place,
**rien d'autre** (licence et période inchangées). B4 livrée — **renouvellement par site et rappels d'échéance** (ADR-0036) : devis
**calculé par le serveur** (`GET /subscriptions/{id}/renewal-quote` ; la déclaration n'accepte
plus de période, montant seulement pour une offre sans tarif) ; postes reconduits depuis la
licence de référence, autre nombre seulement sur **demande explicite** confirmée par TechNova ;
pendant la grâce, la licence suivante suit la licence échue (aucun jour perdu), au-delà elle
commence le jour même (jamais rétroactive) ; « Offre en vigueur » (licence) ≠ « Au prochain
renouvellement », droits de la licence en vigueur sans job de bascule ; tarif à formule **fixe** premier poste + (postes − 1) × poste
supplémentaire (seuls ces prix, périodes, devise, publication sont paramétrables par TechNova ;
migration 0024) figé sur l'abonnement du site ; postes jamais libérés
par un renouvellement ni l'expiration ; rappels `notifications` (étapes `SM_RENEWAL_NOTICE_DAYS`,
défaut J-30 … J+7, essais J-5/J-1/J0) créés par le job idempotent
`stockmanager notifications run` (cron, rôle SQL de la console, verrou consultatif, job manqué :
seule l'étape la plus récente), lu / non lu par membre, visibles par site avec
`subscription.subscription.view`. Aucun téléchargement `.lic` par l'entreprise dans le Web
(décision finale : le Desktop est traité séparément). **Lot 1 livré — encaissement**
([ADR-0037](docs/adr/0037-encaissement.md)) : moyens de paiement **configurables**
(`payment_methods` par entreprise, `payment_method_sites` par site, **type** `CASH` /
`MOBILE_MONEY` / `CARD` / `BANK_TRANSFER` / `OTHER` = comportement, jamais le libellé ; saisie
manuelle, `API` réservé ; référence obligatoire ou non ; instantané `method_label` sur le
paiement ; `sales.payment_method.manage`) ; **caisse optionnelle par site**
(`cash_site_settings` ; site sans caisse : espèces sans session ; activation par
`organization.site.manage` revérifiée pour le site ; désactivation refusée si une session est
ouverte) ; **poste de caisse = machine** (`CashRegister`), **session = site + poste +
utilisateur** (jamais la session d'autrui) ; espèces : **montant reçu et monnaie calculés par le
serveur** sur la seule partie espèces ; **crédit** = reste dû à la validation : client
obligatoire, `sales.sale.credit_create`, limite dépassable seulement avec
`sales.sale.credit_override` + justification (autorisateur, date, montant, audit), statut
calculé `OPEN`/`PARTIAL`/`PAID`/`CANCELLED` ; numéro **`VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` à la
validation** (brouillon non numéroté, compteur par tenant + site + année, numéro validé figé
par déclencheur, code de site figé) ; portée `sales.sale.view` = ses ventes,
`sales.sale.view_all` = toutes (migration 0025). **Lot 2 livré — historique des ventes**
([ADR-0038](docs/adr/0038-historique-ventes-exports.md), aucune migration) : tri par défaut
`-created_at` (tri par numéro en option) ; filtres serveur (vendeur / opérateur = `created_by`,
« Mes ventes », article, client, canal, **référence article ≠ référence de paiement**) dans UN
objet `SaleFilters` partagé par la liste et l'export ; **UNE action « Exporter »** par
fonctionnalité avec choix du format (`ExportMenu` ; ventes : Excel, CSV `;` UTF-8 BOM, PDF A4)
sur le périmètre EXACT de la liste (architecture commune `app/platform/exports.py`, garde-fou
`SM_EXPORT_MAX_ROWS`) ; permission `sales.sale.export` (nature `export` ; Administrateur et
Gestionnaire) en plus de `sales.sale.view`, sites sans l'export jamais exportés ; chaque export
audité `export.generated` (format, filtres renseignés, nombre de lignes) ; limite de crédit
réservée à `customers.credit_limit.manage` (Administrateur), auditée avant / après ; fiche :
mouvements de stock (`stock.movement.view`, `source_type`/`source_id`) et chronologie
(`GET /sales/{id}/history`, `audit.log.view`, évènements réellement journalisés). **Lot 3-A
livré — catalogue** ([ADR-0039](docs/adr/0039-catalogue-stock-gere-prix-couts.md), migration
0026) : scan POS = égalité **exacte** du code-barres côté serveur (`/pos/articles/by-barcode`,
`barcode_unknown`), jamais le premier résultat affiché ; `catalog_articles.stock_managed`
(défaut `true`) : `false` = vendu sans mouvement ni contrôle de stock, refusé dans toute
opération de stock (garde centrale `StockService`, drapeau sous verrou partagé de l'article),
géré → non géré seulement à stock nul sur tous les sites (port `catalog.stock_port`) ;
historique des prix lu dans l'audit (aucune table) ; `catalog.article.price_update` (prix) ≠
`catalog.article.update` (informations générales) ; `catalog.article.cost_view` : coûts internes
**absents** des réponses sans elle (`app.platform.costs.cost_masking_route` sur les routeurs
catalogue, stock, inventaires, alertes, audit — tout nouveau champ de coût doit y être
déclaré) ; Gestionnaire : coûts visibles (`cost_view`), prix non modifiables (pas de
`price_update`). **Lot 3-B livré — quantités décimales et conditionnements**
([ADR-0040](docs/adr/0040-quantites-decimales-conditionnements.md), migration 0027) : unité de
base = champ libre `unit`, toujours vendable, **stock toujours en unité de base** ;
`catalog_articles.decimal_quantity_allowed` (défaut `false` : quantités vendues entières,
contrôle serveur à l'enregistrement ET à la validation) ; `catalog_packagings` (nom libre,
conversion `> 0` entière pour un article entier, prix propre, jamais supprimés, droits de
l'article : `update` / `price_update` ; prix **non configuré** (`NULL`, création sans
`price_update`) ≠ prix 0 : invendable, `packaging_price_not_set`, migration 0028) ; conversion **figée** dès qu'une vente l'utilise (port
`catalog.usage_port`, verrou exclusif / partagé) ; ligne de vente = présentation (unité de base
ou conditionnement), `base_quantity = quantity × conversion` sans arrondi (plus de 3 décimales
refusé), instantané (nom, conversion, prix) figé ; prix changé : `sale_prices_changed`
existant ; POS : choix de la présentation dans le panier, reçu « 2 Carton 24 × … ». **Lot 3-C livré, validé et clôturé —
conditionnements dans les opérations de stock**
([ADR-0041](docs/adr/0041-presentations-operations-de-stock.md), migration 0029) : entrée,
sortie, transfert et comptage en unité de base **ou** en conditionnement ACTIF (ceux du 3-B, prix
sans effet ; une ligne par présentation) ; `base_quantity` calculée par le serveur (jamais reprise
du client), **stock toujours en unité de base** (`StockService` ne reçoit que des quantités de
base) ; règle `decimal_quantity_allowed` étendue au stock (enregistrement ET validation) ;
instantané sur les lignes (`packaging_*`, `base_quantity`) et les mouvements (`packaging_*`,
`packaging_quantity` ; « -3 Carton 24 → -72 bouteille ») ; comptage conditionnements + vrac
(8 × 24 + 5 = 197, calculé par le serveur) ; revalidation sous verrou partagé à la validation
(`packaging_inactive`, `409 packaging_conversion_changed`) ; conversion figée par tout usage
(port `catalog.usage_port` : ventes, stock, inventaires) ; coût d'entrée saisi par présentation
(**validé** : coût par unité de base calculé automatiquement, seul utilisé pour CMUP et
valorisation ; prix de vente indépendant) ; aucune permission nouvelle ; interface : sélecteur de présentation et
équivalences indicatives (« 48 bouteille = 8 Pack 6 = 2 Carton 24 »). **Lot 3-D livré —
codes-barres** ([ADR-0042](docs/adr/0042-codes-barres-multiples.md), migration 0030) : un code
identifie UNE présentation (article en unité de base ou conditionnement) ; registre
`catalog_barcodes` (code principal = champ `barcode` conservé, miroir par déclencheur ; codes
supplémentaires ; codes des conditionnements), unicité commune au tenant parmi les présentations
ACTIVES (index unique partiel), élément désactivé = codes libérés et revérifiés à la
réactivation — **validé** : un article inactif libère aussi les codes de ses conditionnements
(migration 0031), retrait = ligne supprimée (ancienne valeur dans l'audit), code préparé sur un
élément inactif contrôlé à sa réactivation ; texte libre 50 caractères, **sensible à la casse**,
sans validation EAN ; scan EXACT
(`catalog.api.resolve_barcode`, `/catalog/barcodes/resolve`) : POS et vente = 1 conditionnement
ajouté (prix non configuré refusé), entrées / sorties / transferts / inventaires = présélection
sans quantité devinée ; recherche « contient » étendue à tous les codes
(`catalog.api.barcode_search`) ; aucune permission nouvelle (`catalog.article.update`) ; ajouts
et retraits audités. **Lot 3-D validé.** **Lot 3-E livré, validé et clôturé — fiche fournisseur**
([ADR-0043](docs/adr/0043-fiche-fournisseur.md), migration 0032 : index
`stock_entries (tenant_id, supplier_id)` seulement) : lot de **consultation** (aucune écriture ;
`StockService`, CMUP, ventes, POS, transferts, inventaires inchangés) ; fiche `/suppliers/:id`
(contact unique, aucun champ nouveau) ; réceptions du fournisseur = filtre `supplier_id` existant
(brouillons et annulées affichées) ; agrégats calculés par le module `stock`
(`/stock/suppliers/{id}/summary|articles`, `stock.entry.view`, sites visibles) sur les seules
réceptions `PURCHASE` **VALIDÉES** ; **dernier coût** = coût par unité de base de la dernière
réception validée, information historique — le prix d'achat de référence n'est **jamais** mis
à jour par une réception ; `received_total` / `last_unit_cost` dans `STOCK_COST_FIELDS` ;
fournisseur principal = filtre catalogue existant ; chronologie `GET /suppliers/{id}/history`
(`audit.log.view`, évènements réels) ; recherche des entrées par nom du fournisseur
(`suppliers.api.suppliers_named`) et filtres Entrées / Articles ; aucune permission nouvelle,
aucun export, aucun indicateur au tableau de bord. **Lot 3-E validé.** **Lot 3-F livré, validé et clôturé —
emplacements physiques par site** ([ADR-0044](docs/adr/0044-emplacements-par-site.md), migration
0033) : entité `stock_locations` d'UN site (nom 100 unique par site insensible à la casse, jamais
supprimée, désactivée = plus affectable, affectations existantes conservées) ; emplacement
COURANT facultatif par article et par site (`stock_article_locations`, FK composite
`(tenant_id, site_id, location_id)` : emplacement d'un autre site inaffectable) ; **aucune
quantité par emplacement** (`StockService`, CMUP, ventes, POS inchangés ; affecter ne crée aucun
niveau, état et alertes inchangés) ; article non géré en stock : aucun emplacement ; information
courante seulement (aucun instantané), changements audités `stock_location.*` ; aucune copie
par un transfert ; affichage niveaux (colonne, filtre `location_id` / `unlocated`, tri
`location`), inventaires (tri par emplacement), entrées / sorties (indicatif), fiche article
(vue par site) ; permission `stock.location.manage` (Administrateur, Gestionnaire), consultation
`stock.level.view`, portée des sites (`filter_site_ids` / `operation_site`) ; inventaire sur
mobile : emplacement sous l'article (correctif `366332d`, inclus). **Lot 3-F validé** (état de
référence `366332d`). Ne pas passer au lot
suivant sans validation. Non implémentés (feuille de route §13) :
récupération de mot de passe, communications TechNova, MFA, paramètres SaaS en base.
Phase 3.1 livrée : profils d'activité et
profils UX (secteurs `retail`/`restaurant`/`automobile`/`distribution`, profils
`<secteur>.<activité>`, profils UX : navigation, tableau de bord, terminologie, thème — **données**
du catalogue ; expérience effective limitée aux modules effectifs et implémentés, modules
planifiés « à venir » ; le profil n'accorde aucune permission ;
[`BUSINESS_PROFILES.md`](docs/architecture/BUSINESS_PROFILES.md), ADR-0024). Phase 3.0 livrée : point de vente générique
(module `pos`, aucune logique propre : `POST /pos/checkout` → `SaleService.checkout`, création +
validation + paiements en une transaction, idempotent ; canal `POS` ; [`POS.md`](docs/architecture/POS.md),
ADR-0023). Sous-phases 2 livrées : 2.1 (modules `catalog` et
`suppliers`), 2.2 (modules `stock` — niveaux et CMUP par site, entrées, sorties, motifs,
journal des mouvements, seuils par site — et `alerts`), consolidation du RBAC (rôles de
base Administrateur / Gestionnaire / Vendeur / Consultant, rôles personnalisés, ADR-0015),
2.3 (module `customers` : référentiel clients, [`CLIENTS.md`](docs/architecture/CLIENTS.md)),
2.4 (module `sales` : ventes simples au comptant, validation via `StockService`,
[`SALES.md`](docs/architecture/SALES.md), ADR-0017 ; prix toujours lus dans le catalogue),
2.5 (transferts inter-sites dans `stock`, fonctionnalité de plan `stock.transfers` :
`StockService.transfer`, ADR-0018 ; une permission peut dépendre d'une fonctionnalité ;
sans la fonctionnalité, l'historique reste consultable en lecture seule)
2.5-B (Design System de l'interface, [`DESIGN_SYSTEM.md`](docs/architecture/DESIGN_SYSTEM.md) ;
aucune règle métier modifiée) et 2.6 (module `inventory_count`, API `/inventories` : écart =
physique − stock courant relu à la validation, ajustements `ADJUSTMENT` via `StockService`,
[`INVENTORY.md`](docs/architecture/INVENTORY.md), ADR-0019) et 2.7 (paiements des ventes dans
`sales` : encaissement indépendant de la validation, état d'encaissement **calculé**, aucun
surpaiement sous verrou de la vente, annulation motivée, clé d'idempotence,
[`PAYMENTS.md`](docs/architecture/PAYMENTS.md), ADR-0020) et 2.8 (module `receivables`, lecture seule :
créance = vente validée dont le reste dû — total − paiements `COMPLETED` — est positif,
**calculée sans table** ; limite de crédit contrôlée à la validation de la vente sous verrou du
client, `credit_limit NULL` = non configurée ; encaissement immédiat à la validation,
[`RECEIVABLES.md`](docs/architecture/RECEIVABLES.md), ADR-0021) et 2.9 (module `cash_register`, API `/cash` :
caisse d'un site, sessions `OPEN`/`CLOSED` — une seule ouverte par caisse —, mouvements
append-only, **solde calculé** à partir des mouvements, clôture avec écart calculé par le
serveur ; un paiement `CASH` exige une session ouverte du site de la vente et crée son mouvement
dans la même transaction ; **la vente ne dépend pas de la caisse** : `cash_register` dépend de
`sales` et implémente son port `sales/cash_port.py`,
[`CASH_REGISTER.md`](docs/architecture/CASH_REGISTER.md), ADR-0022).
Autres modules métier (paiements électroniques, restaurant…) :
seulement déclarés `planned`
(`backend/app/modules/planned.py`). Ne pas les
commencer sans validation explicite ; s'arrêter à la fin de chaque sous-phase.

Règles métier de référence (issues du Desktop, identifiants CAT/SUP/ART/STK/ENT/SOR/ALR) :
[`docs/architecture/CATALOGUE_STOCK.md`](docs/architecture/CATALOGUE_STOCK.md).

Documents clés : [`docs/architecture/DATA_MODEL.md`](docs/architecture/DATA_MODEL.md),
[`docs/architecture/API.md`](docs/architecture/API.md).

## Méthode de travail

- **Analyser avant de modifier.** Ne rien supposer : lire le code et la doc concernés.
- **Attendre la validation explicite** de l'utilisateur avant chaque grande phase
  (voir la feuille de route, §13 de l'architecture). Ne pas anticiper une phase.
- Ne rien supprimer sans justification explicite.
- **Tester chaque changement** (commandes ci-dessous) et rapporter les résultats tels quels.
- **Documenter les décisions importantes** dans une nouvelle ADR (`docs/adr/`).
- Le dépôt `GESTOK_ENTREP` (Desktop historique) est une **référence métier en lecture
  seule** : ne jamais le modifier ; ne pas copier son architecture technique.

## Règles d'architecture (non négociables)

1. **Le backend est la seule frontière de sécurité.** Le frontend ne fait que masquer
   pour l'ergonomie.
2. **Isolation des tenants** : `tenant_id` dérivé du jeton uniquement (jamais d'un
   paramètre client), filtre ORM automatique (`TenantFiltered`), RLS PostgreSQL
   (`ENABLE` + `FORCE`). Toute nouvelle table tenant-scoped : `TenantScopedMixin`,
   politique RLS + droits minimaux dans sa migration, FK composites `(tenant_id, …)`, tests
   d'isolation SQL **et** API. Jamais de `BYPASSRLS` pour le rôle applicatif.
3. Chaîne de contrôle : **User → Tenant → Membership → Rôle(s) → Permission → Site →
   Resource**, via les dépendances de `app/platform/context.py` (`TenantContext`,
   `require_permission`, `require_module`). Chaque endpoint tenant-scoped exige une permission.
   **`User` = identité globale** (nom, e-mail, mot de passe) partagée entre tenants ;
   **`TenantMembership` = appartenance** (statut, rôles, sites) : un tenant n'administre que
   l'appartenance, jamais l'identité ; désactiver = suspendre l'appartenance à ce tenant
   seulement ([ADR-0029](docs/adr/0029-identite-globale-et-appartenance.md)).
   **RBAC** ([ADR-0015](docs/adr/0015-rbac-roles-de-base-et-personnalises.md)) : les rôles ne
   sont que des regroupements de permissions ; **jamais** de test sur un nom ou un code de rôle.
   Rôles de base (données : `role_templates.toml`) + rôles personnalisés du tenant ; aucun
   rôle supprimé (désactivation) ; anti-escalade par portée (tenant / site) et par sites ;
   ce qui est délégable est calculé par le serveur ([ADR-0030](docs/adr/0030-delegation-rbac.md)).
   **TechNova ≠ tenant** ([ADR-0031](docs/adr/0031-console-technova.md)) : l'administration de la
   plateforme vit dans la console (processus et rôle SQL distincts) ; jamais de route TechNova
   dans l'API des tenants, jamais de privilège ajouté au rôle applicatif pour la console, jamais
   d'attribution de `is_platform_admin` hors CLI, jamais d'accès de la console aux données
   métier des tenants. Le catalogue technique reste versionné ; seuls les paramètres
   commerciaux sont modifiables en base.
4. **Jamais de `if business_type == "…"`** ni de test d'un code de profil ou de secteur
   (ni backend, ni frontend ; tests statiques). Tester une
   capacité : `require_module(...)`, `require_permission(...)`, `can(...)` côté client.
   Secteurs, profils, profils UX, plans et politiques d'abonnement sont des **données**
   (`backend/app/platform/catalog/data/*.toml`). Toute permission déclare sa nature
   (`read`/`write`/`export`/`admin`/`billing`) : c'est elle que la politique d'abonnement filtre.
5. **Stock** : uniquement via `StockService` ; mouvements append-only ; stock jamais négatif
   (contrainte en base + contrôle service) ; verrouillage de lignes.
6. **Idempotence** des opérations critiques (validation de vente, paiement).
7. **Decimal** pour montants (`NUMERIC(18,2)`) et quantités (`NUMERIC(18,3)`) ;
   jamais `float` ; chaînes dans l'API.
8. **Audit** des actions sensibles.
9. **Pas de logique métier faisant foi dans React.** Le backend recalcule et valide tout.
10. Modules : dépendances déclarées dans le manifeste ; pas d'import des modèles
    internes d'un autre module ; pas de cycle.
11. **Transactions explicites** : SQLAlchemy synchrone ; les services ne valident pas,
    l'endpoint (ou la CLI) appelle `db.commit()`. Écritures d'audit dans la même transaction.
12. **Listes** : pagination, tri (liste blanche, `text_sort` pour les textes) et recherche
    côté serveur (`app/shared/pagination.py`). Montants/quantités : types `Money`/`Quantity`
    (`app/shared/schemas.py`), sérialisés en chaînes JSON.
13. **Textes d'interface** : toujours via i18n (`t(...)`), jamais en dur ; vocabulaire métier
    via l'espace de noms `terminology` (surchargé par le profil). Erreurs API = `code` stable
    traduit dans `errors.json`.
14. **Temps** ([ADR-0028](docs/adr/0028-fuseau-horaire-du-tenant.md)) : horodatages stockés en
    UTC (`timestamptz`) ; toute date **métier** (« aujourd'hui », jour d'une vente, d'une
    opération, d'une session de caisse, filtres par jour, périodes jour / semaine / mois des
    rapports, dates des documents) se calcule dans le fuseau du tenant (`tenants.timezone`,
    IANA, **obligatoire**) — jamais celui du serveur ni du navigateur (`tenant_today`, bornes
    locales converties en UTC).
15. **Interface** : suivre le Design System ([`DESIGN_SYSTEM.md`](docs/architecture/DESIGN_SYSTEM.md)) —
    jetons `--sm-*`, composants de `shared/ui` (`PageHeader`, `FilterBar`, `ServerTable`,
    `RowActions`, `StatusBadge`, `EmptyState`, `confirmAction`…), statuts à tonalité unique,
    confirmation des actions sensibles. PrimeReact uniquement.

## Structure

```text
backend/app/
  core/       config, BD, sécurité, logs (aucune règle métier)
  api/v1/     agrégation des routeurs
  platform/   tenants, sites, users, auth, RBAC, plans, profils, registre, capacités, audit
  console/    console TechNova (processus distinct : app.console.main), CLI platform-admin
  modules/    modules métier : <module>/{manifest,router,schemas,service,models,api}.py
              (api.py = interface publique utilisée par les autres modules)
  shared/     types valeur, identifiants, erreurs
frontend/src/
  app/        providers, routeur
  core/       client API, auth, capacités, registre de modules
  modules/    un dossier par module (même code que le backend)
  shared/     composants UI et utilitaires génériques
  pages/      pages hors module
  console/    console TechNova (/tech-admin), application distincte chargée à la demande
signing-service/  service de signature des licences (Ed25519) : déployé à part, seul détenteur
              de la clé privée (jamais dans ce dépôt)
docker/       Dockerfiles ; docker-compose.yml à la racine
docs/         architecture/ et adr/
```

## Commandes

```bash
# PostgreSQL local : trois rôles (propriétaire, applicatif et console, sans BYPASSRLS)
docker compose up -d db          # crée aussi stockmanager_app et stockmanager_platform

# Backend (depuis backend/)
uv sync
uv run alembic upgrade head                 # rôle propriétaire (SM_MIGRATION_DATABASE_URL)
uv run stockmanager catalog sync            # secteurs, profils, plans, pays, politiques
uv run stockmanager create-tenant --name "…" --slug … --business-profile restaurant.maquis \
    --country BF --plan STANDARD --owner-email … --owner-name "…"
uv run stockmanager change-profile --tenant-id … --profile retail.alimentation  # audité
uv run stockmanager change-plan --tenant-id … --plan ENTREPRISE   # données conservées, audité
uv run uvicorn app.main:app --reload --port 8000
uv run stockmanager platform-admin create --email … --name "…"   # compte TechNova (CLI seule)
uv run uvicorn app.console.main:app --reload --port 8001   # console TechNova (SM_PLATFORM_*)
uv run stockmanager notifications run     # rappels d'échéance (cron quotidien, idempotent)
#   licences : SM_SIGNING_SERVICE_URL, SM_SIGNING_CLIENT_SECRET, SM_LICENSE_PUBLIC_KEYS_FILE

# Signing Service (depuis signing-service/, déployé À PART ; clé privée hors dépôt)
uv sync && uv run pytest && uv run ruff check . && uv run mypy signing_service
uv run pytest        # PostgreSQL requis : SM_TEST_DATABASE_URL / SM_TEST_MIGRATION_DATABASE_URL
                     #   / SM_TEST_PLATFORM_DATABASE_URL
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run alembic check                        # aucune dérive modèles / migrations

# Frontend (depuis frontend/)
npm install
npm run dev          # proxy /api -> :8000, /platform-api -> :8001 (console)
npm run lint && npm run format:check
npm run typecheck
npm test
npm run build
npm run e2e          # Playwright contre la pile démarrée (voir frontend/e2e/README.md)

# Stack complète
docker compose up --build
```

## Conventions

- Code (identifiants, noms de fichiers) en **anglais** ; documentation, messages
  utilisateur et commits descriptifs en **français** acceptés.
- API REST sous `/api/v1`, ressources au pluriel, erreurs au format Problem Details (RFC 9457).
- Permissions nommées `module.ressource.action` (ex. `stock.movement.create`).
- Variables d'environnement : `SM_*` (backend), `VITE_*` (frontend). Ne jamais commiter de `.env`.
- Migrations Alembic : une par changement de schéma, jamais modifiée après fusion ;
  générer avec `alembic revision --autogenerate`, puis ajouter RLS et droits à la main.
- Versions épinglées délibérément : PrimeReact 10 (MIT) et TypeScript 6.0 — voir ADR-0005
  avant toute montée de version.
