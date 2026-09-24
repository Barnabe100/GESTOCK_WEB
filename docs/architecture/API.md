# API REST — socle plateforme (Phase 1), catalogue (2.1), stock (2.2), clients (2.3), ventes (2.4) et transferts (2.5)

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
| GET | `/me/capabilities` | tenant | **Capacités effectives** : profil, plan, abonnement, sites accessibles, modules, permissions, navigation, terminologie |
| GET | `/tenant` | `organization.tenant.view` | Informations de l'entreprise |
| PATCH | `/tenant` | `organization.tenant.update` | Modifier nom / fuseau horaire |
| GET | `/sites` | `organization.site.view` | Liste des sites |
| POST | `/sites` | `organization.site.manage` | Créer un site (limite `max_sites` du plan) |
| GET | `/sites/{id}` | `organization.site.view` | Détail |
| PATCH | `/sites/{id}` | `organization.site.manage` | Modifier / désactiver |
| GET | `/modules` | `organization.module.view` | Modules du profil : inclus au plan, activés, effectifs |
| PUT | `/modules/{code}` | `organization.module.manage` | Activer / désactiver (dépendances contrôlées) |
| GET | `/members` | `users.member.view` | Membres du tenant |
| POST | `/members` | `users.member.manage` | Ajouter (nouveau compte + mot de passe provisoire, ou compte existant) |
| GET | `/members/{id}` | `users.member.view` | Détail |
| PATCH | `/members/{id}` | `users.member.manage` | Rôles (tenant ou site), sites, statut |
| GET | `/roles` | `users.role.view` | Rôles du tenant ; filtres `kind` (`system` \| `custom`), `status` ; `is_active`, `protected`, `member_count` |
| POST | `/roles` | `users.role.manage` | Créer un rôle personnalisé |
| GET | `/roles/{id}` | `users.role.view` | Détail (rôle de base : nom, description et permissions issus du modèle) |
| PATCH | `/roles/{id}` | `users.role.manage` | Modifier un rôle personnalisé (rôles de base : `403 system_role`) |
| POST | `/roles/{id}/duplicate` | `users.role.manage` | `{name, description?}` : nouveau rôle personnalisé reprenant les permissions (de l'offre) |
| POST | `/roles/{id}/activate` | `users.role.manage` | Réactiver (rétablit les droits des titulaires) |
| POST | `/roles/{id}/deactivate` | `users.role.manage` | `{confirm}` ; rôle attribué sans confirmation : `409 role_in_use` (membres listés) ; rôle protégé : `403 role_protected` |
| GET | `/roles/{id}/members` | `users.role.view` **et** `users.member.view` | Titulaires (membre, portée : tenant ou site) |
| GET | `/permissions` | `users.role.view` | Permissions des modules effectifs (`code`, `module`, `access`, `resource`, `action`) |
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
| POST | `/sales/{id}/validate` | `sales.sale.validate` | Mouvements `SALE` via `StockService` (tout ou rien) ; statut `VALIDATED` |
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

## Routes des modules métier

Les routeurs des modules métier seront montés sous `/api/v1/<code du module>` (points
remplacés par `/`, ex. `/api/v1/restaurant/tables`) et **automatiquement protégés** par
`require_module(code)` : un module non effectif pour le tenant répond
`403 module_unavailable`, quel que soit le client.
