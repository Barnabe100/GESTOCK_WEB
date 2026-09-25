# API REST — socle plateforme (Phase 1), catalogue (2.1), stock (2.2), clients (2.3), ventes (2.4), transferts (2.5) inventaires (2.6), paiements des ventes (2.7), créances (2.8), caisse (2.9) point de vente (3.0) et profils d'activité (3.1)

Base : `/api/v1` · Documentation interactive : `/api/v1/docs` · Schéma : `/api/v1/openapi.json`

## Conventions

- **Authentification** : `Authorization: Bearer <jeton d'accès>` (voir ADR-0010).
- **Tenant actif** : porté par le jeton (`tid`), choisi à la connexion ou au rafraîchissement.
- **Site actif** (optionnel) : en-tête `X-Site-Id`, revalidé à chaque requête.
- **Erreurs** : `application/problem+json` (RFC 9457) avec un champ `code` stable, traduit
  par le client. Ex. :
  ```json
  { "type": "about:blank", "title": "Accès refusé", "status": 403,
    "detail": "Permission insuffisante", "code": "permission_denied" }
  ```
- Une ressource d'un autre tenant répond **404** (son existence n'est pas révélée).
- Une action autorisée par les rôles mais bloquée par l'abonnement répond
  **403 `subscription_restricted`**.

## Endpoints

| Méthode | Chemin | Accès | Rôle |
|---|---|---|---|
| GET | `/health` | public | Vivacité |
| GET | `/health/ready` | public | Disponibilité (base de données) |
| POST | `/auth/login` | public | Connexion ; pose le cookie de rafraîchissement ; `tenant_id` optionnel |
| POST | `/auth/refresh` | cookie | Nouveau jeton (rotation) ; `tenant_id` optionnel = choix/changement de tenant |
| POST | `/auth/logout` | cookie | Révoque la session |
| GET | `/me` | authentifié | Utilisateur et ses appartenances (tous tenants) |
| POST | `/me/password` | authentifié | Changement de mot de passe (y compris obligatoire) |
| GET | `/me/capabilities` | tenant | **Contexte consolidé** : profil (secteur, profil UX), plan, abonnement, sites accessibles, modules, permissions, fonctionnalités, limites, navigation, terminologie, `ux` (rubriques, widgets, raccourcis, thème, modules « à venir ») |
| GET | `/public/geo/countries` | public | Pays actifs (ISO 3166-1) : devise, indicatif, fuseau par défaut |
| GET | `/public/business-profiles` | public | Secteurs et profils actifs (inscription) |
| GET | `/public/plans` | public | Offres publiées par TechNova : périodes ouvertes, prix seulement s'ils sont affichés, offre sur contact, essai, limites ; adresse commerciale |
| POST | `/public/signup` | public, limité par IP | Compte + entreprise (nom, pays et **devise** obligatoires), propriétaire et administrateur, sans site ; étapes d'onboarding créées et évaluées ; abonnement `trial` ou `pending_activation` ; `201` + session. Erreurs : `plan_not_available`, `signup_unavailable` (générique), `unknown_profile`, `unknown_country`, `invalid_currency`, `password_too_short`, `validation_error` (champ inconnu refusé), `429 rate_limited`, `403 signup_closed` |
| GET | `/business-profiles` | `organization.profile.view` | Catalogue : secteurs actifs et profils actifs (classés), modules proposés |
| GET | `/business-profiles/{code}` | `organization.profile.view` | Configuration **par défaut** d'un profil (profil UX + surcharges), modules « à venir » ; `404 unknown_profile` |
| PUT | `/tenant/business-profile` | `organization.profile.manage` | Changer le profil du tenant du jeton : `422 unknown_profile`, `409 profile_change_incompatible` (`modules`) ; données conservées, audité |
| GET | `/tenant` | `organization.tenant.view` | Informations de l'entreprise (source unique de son identité, ADR-0027) |
| GET | `/tenant/document-identity` | `organization.tenant.view` | En-tête documentaire construit depuis le tenant (Phase 3.2-C) : `name`, `trade_name`, `logo_url`, `contact` (`phone`, `email`, `address`, `locality` = ville, région, pays), `identifiers` (`tax_id`, `trade_register`), `missing_recommended` ; une information absente est omise (jamais « N/A ») |
| PATCH | `/tenant` | `organization.tenant.update` | Modifier les informations de l'entreprise : nom (vide ou blanc refusé, `null` ignoré), fuseau, pays (actif dans le référentiel, jamais effacé ; `422 country_required`, `unknown_country`), informations recommandées et facultatives (`null` ou vide : effacées ; e-mail, téléphone, `logo_url`/`website` https sans identifiants validés) ; devise non modifiable (champ ignoré) ; audité ; réévalue l'onboarding (`company`, `configuration`) |
| GET | `/sites` | `organization.site.view` | Liste des sites |
| POST | `/sites` | `organization.site.manage` | Créer un site (limite `max_sites` du plan) ; réévalue l'onboarding (`first_site`) |
| GET | `/onboarding` | `organization.onboarding.view` | Onboarding persistant (Phase 3.2-B, ADR-0026) : `status` (`NOT_STARTED`/`IN_PROGRESS`/`COMPLETED`), `completed` (étapes obligatoires terminées), `progress` (`completed`, `total`, `percentage`, `required_completed`, `required_total`), `current_step`, `next_action`, `subscription_status`, `steps` (code, ordre, obligatoire, clés i18n, statut, `completed_at`, action : écran, permission, `available`, `blocked_reason`). Crée les étapes manquantes et enregistre les progrès constatés (idempotent) |
| PATCH | `/onboarding/steps/{code}` | `organization.onboarding.manage` | Seule transition manuelle : `{"status": "IN_PROGRESS"}` (démarrer une étape ; sans effet sur une étape en cours ou terminée). `422 onboarding_transition_not_allowed` pour toute autre valeur (une étape n'est jamais déclarée terminée par le client), `404 onboarding_step_not_found`, `validation_error` (champ inconnu) |
| GET | `/sites/{id}` | `organization.site.view` | Détail |
| PATCH | `/sites/{id}` | `organization.site.manage` | Modifier / désactiver |
| GET | `/modules` | `organization.module.view` | Modules du profil : inclus au plan, activés, effectifs |
| PUT | `/modules/{code}` | `organization.module.manage` | Activer / désactiver (dépendances contrôlées) |
| GET | `/members` | `users.member.view` | Appartenances du tenant, **paginées** (Phase 3.2-D) : `search` (nom, e-mail), `status` (`active`/`inactive`/`all`), `role_id`, `site_id` (tous les sites, site attribué ou rôle limité au site) ; tri `full_name` (défaut), `email`, `created_at` (ajout au tenant), `status` |
| POST | `/members` | `users.member.manage` | Ajouter : nouveau compte global (mot de passe provisoire haché, changement imposé) ou compte existant **réutilisé tel quel** (nom et mot de passe ignorés) ; `409 member_exists` ; anti-escalade |
| GET | `/members/{id}` | `users.member.view` | Détail (identifiant d'appartenance) |
| PATCH | `/members/{id}` | `users.member.manage` | **Accès seulement** (ADR-0029) : rôles (tenant ou site), sites, statut ; toute clé d'identité (nom, e-mail, mot de passe…) → `422 validation_error` ; `403 owner_protected`, `self_modification`, `permission_escalation`, `site_escalation` |
| POST | `/members/{id}/activate` | `users.member.manage` | Réactiver l'appartenance (limite `max_users` : `422 plan_limit_reached`) |
| POST | `/members/{id}/deactivate` | `users.member.manage` | Désactiver l'appartenance à **ce** tenant seulement (compte global et autres tenants inchangés ; rôles, sites et historique conservés) |
| GET | `/roles` | `users.role.view` | Rôles du tenant ; filtres `kind` (`system` \| `custom`), `status` ; `is_active`, `protected`, `member_count`, `delegable` (l'utilisateur courant peut attribuer ce rôle sur tout le tenant et, s'il est personnalisé, le modifier ; ADR-0030) |
| POST | `/roles` | `users.role.manage` | Créer un rôle personnalisé |
| GET | `/roles/{id}` | `users.role.view` | Détail (rôle de base : nom, description et permissions issus du modèle) |
| PATCH | `/roles/{id}` | `users.role.manage` | Modifier un rôle personnalisé (rôles de base : `403 system_role` ; hors du périmètre : `403 permission_escalation`) ; permissions enregistrées devenues hors offre conservées, jamais ajoutées (`422 unknown_permission`) |
| POST | `/roles/{id}/duplicate` | `users.role.manage` | `{name, description?}` : nouveau rôle personnalisé reprenant les permissions (de l'offre) |
| POST | `/roles/{id}/activate` | `users.role.manage` | Réactiver (rétablit les droits des titulaires) |
| POST | `/roles/{id}/deactivate` | `users.role.manage` | `{confirm}` ; rôle attribué sans confirmation : `409 role_in_use` (membres listés) ; rôle protégé : `403 role_protected` |
| GET | `/roles/{id}/members` | `users.role.view` **et** `users.member.view` | Titulaires (membre, portée : tenant ou site) |
| GET | `/permissions` | `users.role.view` | Permissions des modules effectifs (`code`, `module`, `access`, `resource`, `action`) |
| GET | `/permissions/delegable` | `users.role.manage` **ou** `users.member.manage` | Permissions que l'utilisateur courant peut accorder (Phase 3.2-E, ADR-0030) : tout le tenant, ou `site_id` (site de son périmètre ; sinon liste vide ; `404 site_not_found`) — exactement ce que l'anti-escalade accepte |
| GET | `/roles/delegable` | `users.role.manage` **ou** `users.member.manage` | Rôles actifs attribuables par l'utilisateur courant sur tout le tenant, ou pour `site_id` |
| GET | `/role-templates` | `users.role.view` | Modèles de rôles de base (instanciés ou non) |
| POST | `/roles/from-template` | `users.role.manage` | Ajouter au tenant un rôle de base manquant |
| GET | `/subscription` | `subscription.subscription.view` | Offre, statut effectif, période, limites, utilisation |
| GET | `/audit-logs` | `audit.log.view` | Journal d'audit paginé (`limit`, `offset`, `action`, `user_id`) |

« tenant » = jeton lié à un tenant, appartenance active, tenant actif, mot de passe à jour.

Aucun `DELETE /roles` : un rôle est désactivé, jamais supprimé (ADR-0015). Règles RBAC
(non-propriétaire) : un rôle, son activation ou son attribution **sur tout le tenant** exige de
détenir ses permissions sur tout le tenant ; une attribution **pour un site**, de les détenir
sur ce site (`403 permission_escalation`) ; les sites accordés restent dans ceux de l'acteur
(`403 site_escalation`). Autres codes : `role_name_taken`, `role_name_reserved` (409, nom
d'un rôle de base, casse ignorée), `role_inactive` (422, attribution d'un rôle désactivé),
`unknown_permission` (422, permission hors des modules de l'offre).

### Catalogue (module `catalog`) et fournisseurs (module `suppliers`) — Phase 2.1

Routes montées sous le code du module et refusées (`403 module_unavailable`) si le module
n'est pas effectif pour le tenant. Listes : `limit` (1–200, défaut 25), `offset`,
`sort` (champ de la liste blanche, `-` = décroissant), `search` (contient, insensible à la
casse), `status` = `all` | `active` | `inactive`. Réponse : `{items, total, limit, offset}`.

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/catalog/categories` | `catalog.category.view` | Liste (tri : `name`, `created_at`) |
| POST | `/catalog/categories` | `catalog.category.create` | Créer (nom unique par tenant, casse ignorée) |
| GET · PATCH | `/catalog/categories/{id}` | `…view` · `…update` | Détail · renommer |
| POST | `/catalog/categories/{id}/activate` · `/deactivate` | `catalog.category.status` | Statut (jamais de suppression) |
| GET | `/catalog/articles` | `catalog.article.view` | Liste ; filtres `category_id`, `supplier_id` ; tri `reference`, `designation`, `category`, `sale_price`, `purchase_price`, `created_at` |
| GET | `/catalog/articles/by-barcode/{code}` | `catalog.article.view` | Article **actif** pour ce code-barres |
| POST | `/catalog/articles` | `catalog.article.create` | Créer (catégorie / fournisseur actifs) |
| GET · PATCH | `/catalog/articles/{id}` | `…view` · `…update` | Détail · modifier (aucun champ de stock) |
| POST | `/catalog/articles/{id}/activate` · `/deactivate` | `catalog.article.status` | Statut (réactivation refusée si code-barres pris) |
| GET · POST | `/suppliers` | `suppliers.supplier.view` · `.create` | Liste (recherche nom, contact, ville, email, téléphone) · créer |
| GET · PATCH | `/suppliers/{id}` | `…view` · `…update` | Détail · modifier (chaîne vide = champ effacé) |
| POST | `/suppliers/{id}/activate` · `/deactivate` | `suppliers.supplier.status` | Statut |

Montants (`purchase_price`, `sale_price`) : chaînes décimales à 2 décimales max ; quantités
(`min_stock`, `max_stock`) : 3 décimales max. Codes d'erreur spécifiques :
`category_name_taken`, `article_reference_taken`, `article_barcode_taken` (409),
`category_inactive`, `supplier_inactive`, `category_not_found`, `supplier_not_found`,
`invalid_stock_thresholds`, `module_unavailable` (422), `invalid_sort` (400).

### Stock (module `stock`) et alertes (module `alerts`) — Phase 2.2

Mêmes conventions de liste. **Site** : avec un site sélectionné (`X-Site-Id`), lectures et
opérations portent sur ce site ; sinon sur les sites accessibles au membre (filtre
`site_id` facultatif, restreint à ces sites). Un document d'un site non accessible est
introuvable (`404`) ; d'un autre site que le site sélectionné, refusé (`403 site_mismatch`).

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/stock/levels` | `stock.level.view` | Articles × sites : `quantity`, `average_cost` (CMUP, 4 déc.), `stock_value`, seuils effectifs `min_stock`/`max_stock` (surcharge du site sinon article), `min_override`/`max_override`, `state` = `ok` \| `low` \| `out` \| `not_stocked`. Filtres `site_id`, `category_id`, `search`, `state` (`all`, `alerts`, `out`, `low`, `ok`, `not_stocked`), `include_inactive`, `article_id` (répétable : stock disponible des lignes en saisie) ; tri `reference`, `designation`, `category`, `quantity`, `site` |
| PUT | `/stock/levels/{site_id}/{article_id}/thresholds` | `stock.threshold.manage` | Surcharges du site `{min_stock, max_stock}` (`null` = seuil de l'article) ; audité ; ne modifie ni quantité ni CMUP |
| GET | `/stock/movements` | `stock.movement.view` | Journal : filtres `site_id`, `article_id`, `movement_type` (`ENTRY`, `EXIT`, `SALE`, `CANCELLATION`…), `user_id`, `date_from`, `date_to` (jour du fuseau du tenant), `search` (référence, désignation, numéro de document ou de vente — `source_number`) ; tri `occurred_at` (défaut décroissant) |
| GET | `/stock/exit-reasons` | `stock.reason.view` ou `stock.exit.create` / `.update` | Motifs (filtre `status`, tri `label`) |
| POST · PATCH | `/stock/exit-reasons` · `/{id}` | `stock.reason.manage` | Créer · renommer (motif système : `403 system_exit_reason`) |
| POST | `/stock/exit-reasons/{id}/activate` · `/deactivate` | `stock.reason.manage` | Statut (y compris motifs système) |
| GET · POST | `/stock/entries` | `stock.entry.view` · `.create` | Liste (filtres `status`, `kind`, `supplier_id`, `site_id`, `date_from`, `date_to`, `search` numéro / référence de pièce ; tri `number`, `operation_date`, `created_at`) · créer un **brouillon** (numéro `ENT-000001` attribué) |
| GET · PUT | `/stock/entries/{id}` | `…view` · `…update` | Détail · remplacer en-tête et lignes d'un brouillon |
| POST | `/stock/entries/{id}/validate` | `stock.entry.validate` | Applique les mouvements `ENTRY` (stock + CMUP du site) |
| POST | `/stock/entries/{id}/cancel` | `stock.entry.cancel` | `{reason}` (5–500 car.) : mouvements inverses `CANCELLATION`, CMUP inchangé |
| GET · POST · GET · PUT | `/stock/exits`, `/stock/exits/{id}` | `stock.exit.*` | Idem (filtre `reason_id`) ; numéro `SOR-000001` |
| POST | `/stock/exits/{id}/validate` · `/cancel` | `stock.exit.validate` · `.cancel` | Mouvements `EXIT` au CMUP du site (coût et montant figés sur les lignes) · annulation |
| GET | `/alerts/stock` | `alerts.stock.view` | Articles actifs en rupture (`out` : géré sur le site, stock nul) ou stock faible (`low` : 0 < stock ≤ minimum effectif) ; filtre `state` = `alerts` \| `out` \| `low` |
| GET | `/alerts/stock/summary` | `alerts.stock.view` | `{out, low}` (filtre `site_id`) |

Entrée : `{site_id?, kind: PURCHASE|INITIAL_STOCK, operation_date?, supplier_id, document_reference?,
comment?, lines: [{article_id, quantity, unit_cost}]}` ; sortie : `{site_id?, operation_date?,
reason_id, beneficiary?, reference?, comment?, lines: [{article_id, quantity}]}`. Quantités
`> 0` (3 déc.), coûts d'entrée 2 déc. ; au plus 500 lignes ; un article une seule fois.
Codes d'erreur : `insufficient_stock` (422, `articles: [{article_id, site_id, reference, available}]`),
`document_not_draft`, `document_not_validated` (409), `document_empty`,
`duplicate_article_line`, `article_inactive`, `article_not_found`, `future_operation_date`,
`supplier_required`, `supplier_inactive`, `exit_reason_inactive`, `site_required`,
`invalid_stock_thresholds` (422), `exit_reason_taken` (409), `system_exit_reason`,
`site_access_denied`, `site_mismatch` (403), `stock_entry_not_found`,
`stock_exit_not_found`, `exit_reason_not_found` (404). Abonnement expiré : consultation
possible, opérations refusées (`403 subscription_restricted`).

### Transferts inter-sites (module `stock`, fonctionnalité `stock.transfers`) — Phase 2.5

Création, modification, validation et annulation exigent la fonctionnalité de plan
`stock.transfers` (`403 feature_unavailable` sinon) en plus de leur permission ; la
consultation (liste, détail) n'exige que `stock.transfer.view` : l'historique d'une entreprise
revenue à un plan sans la fonctionnalité reste consultable. Règles : [`CATALOGUE_STOCK.md`
§8](CATALOGUE_STOCK.md#8-transferts-inter-sites-phase-25) ; décisions :
[ADR-0018](../adr/0018-transferts-inter-sites.md).

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/stock/transfers` | `stock.transfer.view` | Transferts dont un site est visible et dont les deux sites sont accessibles ; `search` (numéro), `status`, `source_site_id`, `destination_site_id`, `date_from`, `date_to` ; tri `number` (défaut décroissant), `operation_date`, `created_at` |
| POST | `/stock/transfers` | `stock.transfer.create` | Brouillon `TRF-000001` : `{source_site_id?, destination_site_id, operation_date?, comment?, lines: [{article_id, quantity}]}` (source = site sélectionné par défaut) |
| GET | `/stock/transfers/{id}` | `stock.transfer.view` | Détail avec lignes (coût et valeur après validation) |
| PUT | `/stock/transfers/{id}` | `stock.transfer.update` | Remplacer destination, date, commentaire et lignes d'un brouillon (source fixe) |
| POST | `/stock/transfers/{id}/validate` | `stock.transfer.validate` | Sortie `TRANSFER_OUT` du site source et entrée `TRANSFER_IN` du site destination, en une transaction |
| POST | `/stock/transfers/{id}/cancel` | `stock.transfer.cancel` | `{reason}` (5–500 car.) ; brouillon : abandon ; validé : mouvements inverses sur les deux sites, CMUP inchangés |

La permission est exigée sur les **deux** sites (rôles limités à un site). Codes :
`same_site_transfer`, `site_required`, `future_operation_date`, `duplicate_article_line`,
`article_inactive`, `article_not_found`, `document_empty`, `validation_error` (lignes vides,
quantité ≤ 0), `insufficient_stock` (422) ; `document_not_draft` (modification ou double
validation), `transfer_already_cancelled` (409) ; `site_access_denied`, `site_mismatch`,
`site_permission_denied`, `feature_unavailable` (403) ; `stock_transfer_not_found` (404).

### Clients (module `customers`) — Phase 2.3

Mêmes conventions de liste. Règles métier : [`CLIENTS.md`](CLIENTS.md).

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/customers` | `customers.customer.view` | Liste ; `search` (code, nom, raison sociale, téléphones — séparateurs ignorés —, email), `status` (`all` \| `active` \| `inactive`), `type` (`INDIVIDUAL` \| `BUSINESS`) ; tri `name` (défaut), `code`, `city`, `created_at` |
| POST | `/customers` | `customers.customer.create` | Créer (code `CLI-000001` attribué par le serveur, client actif) |
| GET | `/customers/{id}` | `customers.customer.view` | Détail (client inactif compris) |
| PATCH | `/customers/{id}` | `customers.customer.update` | Modifier (champ absent : inchangé ; chaîne vide : effacé ; code immuable) |
| POST | `/customers/{id}/activate` · `/deactivate` | `customers.customer.status` | Statut (jamais de suppression) |

Corps : `customer_type`, `name` (obligatoires à la création), `legal_name`, `tax_id`,
`phone`, `phone2`, `email`, `address`, `city`, `country`, `notes`, `credit_limit` (chaîne
décimale, 2 décimales, ≥ 0). Codes : `validation_error` (422 ; 400 si un champ obligatoire
est vidé en modification), `customer_not_found` (404, dont un client d'une autre entreprise),
`invalid_sort` (400).

### Ventes (module `sales`) — Phase 2.4

Mêmes conventions de liste et de sites que les documents de stock. Règles métier et cycle de
vie : [`SALES.md`](SALES.md) ; décisions : [ADR-0017](../adr/0017-ventes-prix-validation-annulation.md).

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/sales` | `sales.sale.view` | Liste des ventes des sites accessibles ; `search` (numéro, code / nom / téléphone du client), `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `site_id`, `customer_id`, `date_from`, `date_to` ; tri `number` (défaut décroissant), `sale_date`, `total`, `created_at` |
| POST | `/sales` | `sales.sale.create` | Créer un **brouillon** (numéro `VTE-000001` attribué, prix copiés du catalogue, totaux calculés) |
| GET | `/sales/{id}` | `sales.sale.view` | Détail avec lignes |
| PUT | `/sales/{id}` | `sales.sale.update` | Remplacer date, client, observations et lignes d'un brouillon (prix relus) ; site non modifiable |
| POST | `/sales/{id}/validate` | `sales.sale.validate` | Mouvements `SALE` via `StockService` (tout ou rien) ; statut `VALIDATED` ; corps facultatif `{payments: [{amount, method, …}]}` : encaissements immédiats (exige aussi `sales.payment.create`) ; limite de crédit du client contrôlée (2.8) |
| POST | `/sales/{id}/cancel` | `sales.sale.cancel` | `{reason}` (5–500 car.) ; brouillon : abandon ; validée : mouvements `CANCELLATION` (remise en stock, CMUP inchangé) |

Corps : `{site_id?, sale_date?, customer_id?, notes?, lines: [{article_id, quantity}]}` —
**aucun prix ni total** : `unit_price` provient de `catalog_articles.sale_price`,
`line_total`, `subtotal` et `total` sont calculés par le serveur (chaînes décimales en
réponse). 1 à 500 lignes, quantité `> 0` (3 déc.), un article une seule fois.
Codes : `insufficient_stock` (422, détail par article), `sale_not_draft` (409, modification
ou validation d'une vente non brouillon — double validation comprise),
`sale_prices_changed` (409, `articles` : références dont le prix catalogue a changé depuis
l'enregistrement), `sale_already_cancelled` (409), `sale_empty`, `duplicate_article_line`,
`article_inactive`, `article_not_found`, `customer_inactive` (`customer_code`),
`customer_not_found`, `future_operation_date`, `site_required` (422), `site_access_denied`,
`site_mismatch` (403), `sale_not_found` (404, dont une vente d'un autre site ou d'une autre
entreprise). Abonnement expiré : consultation seule (`403 subscription_restricted`).

### Inventaires (module `inventory_count`) — Phase 2.6

Monté sous `/inventories` (préfixe d'URL du module, ADR-0019). Règles et cycle de vie :
[`INVENTORY.md`](INVENTORY.md) ; décisions : [ADR-0019](../adr/0019-inventaires.md).
Permissions : `inventory_count.inventory.{view,create,update,count,validate,cancel}`.

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/inventories` | `view` | Inventaires des sites visibles ; `search` (numéro), `status`, `inventory_type`, `site_id`, `date_from`, `date_to` (création, fuseau du tenant) ; tri `number` (défaut décroissant), `created_at`, `status` ; `line_count`, `counted_count`, `variance_count` |
| GET | `/inventories/candidates` | `create` ou `update` | Articles actifs proposables pour un site (`site_id`, `search`, `stocked_only`) avec leur stock courant |
| POST | `/inventories` | `create` | `{site_id?, inventory_type: FULL\|TARGETED, article_ids?, comment?}` → brouillon `INV-000001` ; complet : articles actifs gérés sur le site (liste fixée par le serveur) |
| GET | `/inventories/{id}` | `view` | Détail + `summary` (avant validation : sur le stock courant ; après : figé) |
| PUT | `/inventories/{id}` | `update` | Brouillon : `{comment?, add_article_ids?, remove_article_ids?}` (ciblé seulement) |
| GET | `/inventories/{id}/lines` | `view` | Lignes paginées : `search`, `state` (`counted`, `uncounted`, `surplus`, `shortage`, `no_variance`), tri `reference`, `designation`, `category`, `variance`, `counted_at` |
| PATCH | `/inventories/{id}/lines` | `count` | Comptage par lot : `{counts: [{line_id, quantity_physical}]}` (≥ 0, 3 déc. ; `null` efface), 1 à 500 lignes → lignes mises à jour + résumé |
| POST | `/inventories/{id}/start` | `count` | `DRAFT → COUNTING` (liste recalée pour un complet, stock théorique initial relevé) |
| POST | `/inventories/{id}/complete-counting` | `count` | `COUNTING → READY_TO_VALIDATE` (toutes les lignes comptées) |
| POST | `/inventories/{id}/reopen-counting` | `count` | `READY_TO_VALIDATE → COUNTING` |
| POST | `/inventories/{id}/validate` | `validate` | Écarts sur le stock courant, mouvements `ADJUSTMENT` via `StockService` (tout ou rien) → `VALIDATED` |
| POST | `/inventories/{id}/cancel` | `cancel` | `{reason}` (5–500 car.), avant validation seulement, sans effet sur le stock |

Codes : `inventory_invalid_transition` (409, `status`, `action` — dont double validation et
toute action sur un inventaire validé ou annulé), `article_in_open_inventory` (409, `articles`,
`inventories`), `inventory_empty`, `inventory_not_fully_counted` (`remaining`),
`inventory_full_articles_fixed`, `inventory_line_not_found`, `duplicate_count_line`,
`duplicate_article_line`, `article_inactive`, `article_not_found`, `site_required` (422),
`site_access_denied`, `site_mismatch` (403), `inventory_not_found` (404, dont un autre site ou
une autre entreprise). Abonnement expiré : consultation seule (`403 subscription_restricted`).
Paiements (2.7) : `SaleOut` expose `paid_amount`, `remaining_amount`, `payment_status`
(`UNPAID` \| `PARTIALLY_PAID` \| `PAID`, vente validée seulement, calculés) ; `GET /sales` accepte
le filtre `payment_status` ; annuler une vente encaissée → `409 sale_has_payments`.

### Paiements des ventes (module `sales`) — Phase 2.7

Règles : [`PAYMENTS.md`](PAYMENTS.md) ; décisions : [ADR-0020](../adr/0020-paiements-des-ventes.md).
Mêmes contrôles d'accès que la vente (tenant, site accessible / sélectionné).

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/sales/{sale_id}/payments` | `sales.payment.view` | Historique complet (annulés compris) + `summary` (`total`, `paid_amount`, `remaining_amount`, `payment_status` ; `null` si la vente n'est pas validée) |
| POST | `/sales/{sale_id}/payments` | `sales.payment.create` | `{amount, method, provider?, reference?, idempotency_key?}` → paiement `COMPLETED` `PAY-000001` (201 ; 200 si même clé : réponse rejouée) |
| GET | `/sales/{sale_id}/payments/{payment_id}` | `sales.payment.view` | Détail |
| POST | `/sales/{sale_id}/payments/{payment_id}/cancel` | `sales.payment.cancel` | `{reason}` (5–500 car.) → `CANCELLED` ; vente et stock inchangés |

`method` : `CASH` \| `MOBILE_MONEY` \| `CARD` \| `BANK_TRANSFER` \| `OTHER` ; montant > 0,
2 décimales. Codes : `sale_not_payable` (409, `status`), `payment_exceeds_balance` (422,
`remaining`), `sale_already_paid` (422), `payment_already_cancelled` (409),
`idempotency_key_reused` (409), `payment_not_found`, `sale_not_found` (404), `site_mismatch`
(403). Aucune route de modification ni de suppression. Abonnement expiré : consultation seule.

### Créances / comptes clients (module `receivables`) — Phase 2.8

Règles : [`RECEIVABLES.md`](RECEIVABLES.md) ; décisions :
[ADR-0021](../adr/0021-creances-comptes-clients.md). Lecture seule, permission
`receivables.receivable.view` ; créances calculées (vente validée, reste dû > 0), sites
visibles du membre.

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/receivables` | Créances ouvertes : `search`, `customer_id`, `site_id`, `date_from`, `date_to`, `min_amount`, `max_amount`, `status` (`UNPAID` \| `PARTIALLY_PAID`) ; tri `sale_date` (défaut), `sale_number`, `total`, `paid_amount`, `remaining_amount`, `customer_name` |
| GET | `/receivables/summary` | `total_receivables`, `receivables_count`, `debtor_customers_count` (mêmes filtres) |
| GET | `/receivables/{sale_id}` | Solde d'une vente validée et historique de ses paiements (`is_open`) |
| GET | `/customers/{id}/receivables` | Créances ouvertes du client |
| GET | `/customers/{id}/credit-exposure` | `credit_limit` (nul : non configurée), `limit_configured`, `current_exposure`, `available_credit`, `over_limit`, `open_receivables_count`, `consolidated` |

Codes : `receivable_not_found`, `customer_not_found` (404), `site_mismatch` (403). À la
validation d'une vente : `credit_limit_exceeded` (422 ; `credit_limit`, `sale_exposure`, et
`current_exposure` / `available_credit` pour un membre voyant tous les sites). Aucune route
d'écriture (405). Abonnement expiré : consultation normale.

### Caisse (module `cash_register`) — Phase 2.9

Monté sous `/cash`. Règles : [`CASH_REGISTER.md`](CASH_REGISTER.md) ; décisions :
[ADR-0022](../adr/0022-caisse.md). Permissions `cash_register.{register.view, register.manage,
session.view, session.open, session.close, movement.create}`.

| Méthode | Chemin | Rôle |
|---|---|---|
| GET / POST | `/cash/registers` | Caisses des sites visibles (`search`, `site_id`, `status`, caisse courante et solde) / création `{site_id?, name, description?}` → `CAI-001` |
| GET / PATCH | `/cash/registers/{id}` | Détail / `name`, `description` (site non modifiable) |
| POST | `/cash/registers/{id}/activate`, `/deactivate` | Désactivation refusée si une session est ouverte |
| GET / POST | `/cash/sessions` | Sessions (`cash_register_id`, `site_id`, `status`, `opened_by`, période) / ouverture `{cash_register_id, opening_float}` → `SES-000001` |
| GET | `/cash/sessions/{id}` | Totaux, solde théorique (figé à la clôture), compté, écart |
| POST | `/cash/sessions/{id}/close` | `{counted_balance, note?}` ; écart calculé par le serveur |
| GET | `/cash/sessions/{id}/movements`, `/cash/movements` | Journal paginé, solde après chaque mouvement ; `movement_type`, `created_by`, `search`, `min_amount`, `max_amount`, période |
| POST | `/cash/sessions/{id}/movements` | Entrée / sortie manuelle `{movement_type, amount, category, reason, reference?, idempotency_key?}` (201 ; 200 si rejouée) |

Paiements (2.7) : `method = CASH` exige une session ouverte d'une caisse du site de la vente ;
champ facultatif `cash_register_id` (aussi dans `payments` de la validation). Codes :
`cash_session_required`, `cash_register_required`, `cash_insufficient_balance`,
`cash_movement_type_invalid`, `cash_movement_category_invalid` (422), `cash_session_closed`,
`cash_session_already_open`, `cash_register_inactive`, `cash_register_has_open_session`,
`idempotency_key_reused` (409), `cash_register_not_found`, `cash_session_not_found` (404).
Aucune suppression. Abonnement expiré : consultation seule.

### Point de vente (module `pos`) — Phase 3.0

Règles : [`POS.md`](POS.md) ; décisions : [ADR-0023](../adr/0023-point-de-vente.md). Aucune
logique propre : orchestration de `SaleService` (et, par lui, `StockService`,
`PaymentService`, caisse pour les espèces, limite de crédit).

| Méthode | Chemin | Permissions | Rôle |
|---|---|---|---|
| GET | `/pos/articles` | `pos.terminal.use` | Articles du site (`site_id`, `search`, `limit` ≤ 50) : prix du catalogue, stock du site, actif |
| POST | `/pos/checkout` | `pos.terminal.use` + `sales.sale.create` + `sales.sale.validate` (+ `sales.payment.create`) | Création + validation + paiements en une transaction ; `idempotency_key` obligatoire (201 ; 200 `replayed` pour une clé déjà traitée) |

Ventes : `SaleOut.channel` (`BACKOFFICE` \| `POS`), filtre `GET /sales?channel=`.

## Routes des modules métier

Les routeurs des modules métier sont montés sous `/api/v1/<code du module>` (points
remplacés par `/`, ex. `/api/v1/restaurant/tables`) — ou sous le préfixe déclaré par le
manifeste (`route_prefix`, ex. `/api/v1/inventories` pour `inventory_count`) — et **automatiquement protégés** par
`require_module(code)` ; un module peut aussi déclarer des sous-ressources d'un autre module
(`extra_routers`, ex. `/api/v1/customers/{id}/receivables` du module `receivables`), protégées
par
`require_module(code)` : un module non effectif pour le tenant répond
`403 module_unavailable`, quel que soit le client.
