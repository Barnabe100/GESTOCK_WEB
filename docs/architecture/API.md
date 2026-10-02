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
| POST | `/sites` | `organization.site.manage` | Créer un site **et son abonnement** (1 site = 1 abonnement, ADR-0033) : `plan_code` (offre publiée souscriptible en ligne) et `billing_period` obligatoires, sauf pour le premier site d'une inscription (abonnement déjà choisi, rattaché : `422 site_subscription_preselected` si une offre est envoyée) ; `requested_activations` (postes demandés, défaut 1) ; abonnement `pending_activation` (aucun essai) ; `422 site_plan_required` / `plan_not_available` ; réévalue l'onboarding (`first_site`) |
| GET | `/onboarding` | `organization.onboarding.view` | Onboarding persistant (Phase 3.2-B, ADR-0026) : `status` (`NOT_STARTED`/`IN_PROGRESS`/`COMPLETED`), `completed` (étapes obligatoires terminées), `progress` (`completed`, `total`, `percentage`, `required_completed`, `required_total`), `current_step`, `next_action`, `subscription_status`, `steps` (code, ordre, obligatoire, clés i18n, statut, `completed_at`, action : écran, permission, `available`, `blocked_reason`). Crée les étapes manquantes et enregistre les progrès constatés (idempotent) |
| PATCH | `/onboarding/steps/{code}` | `organization.onboarding.manage` | Seule transition manuelle : `{"status": "IN_PROGRESS"}` (démarrer une étape ; sans effet sur une étape en cours ou terminée). `422 onboarding_transition_not_allowed` pour toute autre valeur (une étape n'est jamais déclarée terminée par le client), `404 onboarding_step_not_found`, `validation_error` (champ inconnu) |
| GET | `/sites/{id}` | `organization.site.view` | Détail |
| PATCH | `/sites/{id}` | `organization.site.manage` | Modifier / désactiver |
| GET | `/modules` | `organization.module.view` | Modules du profil : inclus au plan, activés, effectifs |
| PUT | `/modules/{code}` | `organization.module.manage` | Activer / désactiver (dépendances contrôlées) |
| GET | `/members` | `users.member.view` | Appartenances du tenant, **paginées** (Phase 3.2-D) : `search` (nom, e-mail), `status` (`active`/`inactive`/`all`), `role_id`, `site_id` (tous les sites, site attribué ou rôle limité au site) ; tri `full_name` (défaut), `email`, `created_at` (ajout au tenant), `status` |
| POST | `/members` | `users.member.manage` | Ajouter : nouveau compte global (mot de passe provisoire haché, changement imposé) ou compte existant **réutilisé tel quel** (nom et mot de passe ignorés) ; `409 member_exists` ; `409 account_unavailable` (compte TechNova, ADR-0031) ; anti-escalade |
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
| GET | `/subscriptions` | `subscription.subscription.view` | Abonnements de l'entreprise, un par site (sites accessibles au membre) et l'éventuel abonnement d'inscription non rattaché : site, offre, statuts stocké et effectif, période, limites et usage **du site**, fonctionnalités, accès, postes demandés (ADR-0033) ; `effective_plan` (offre de la licence en vigueur), `next_plan` (offre de la prochaine licence si différente), `renewal` (devis de la prochaine période, 3.3-B4) ; `license` : licence en vigueur du site, sinon la plus récente (numéro, version, état calculé, plan, validité, postes autorisés ; lecture seule, ADR-0034). **Aucune route** de création ou de modification de licence côté entreprise |
| GET | `/subscription` | `subscription.subscription.view` | Abonnement du site sélectionné (`X-Site-Id`), sinon abonnement représentatif (même forme que `/subscriptions`) |
| GET | `/license-activations` | `subscription.subscription.view` | Postes des sites visibles (Phase 3.3-B3, ADR-0035) : filtres `site_id`, `status` ; tri `activated_at` décroissant par défaut, `last_seen_at`, `status` ; `stale` = non vu depuis plus que la durée hors ligne tolérée ; `release_source` (`TENANT` / `TECHNOVA`, jamais l'agent) |
| POST | `/license-activations` | `subscription.activation.manage` (nature `billing`) | Activation d'une installation cliente (le Web n'active jamais de navigateur) sur le site sélectionné (`X-Site-Id`, sinon `422 site_required`) : `installation_id` (UUID aléatoire), `label`, `client_version?`, `license_id?` ; `201`, `200` si déjà active sur ce site ; `409` journalisé : `license_missing`, `license_expired`, `license_revoked`, `license_not_yet_valid`, `license_invalid`, `license_wrong_site`, `license_superseded`, `activation_quota_reached` (`max_activations`, `used`), `installation_active_elsewhere` |
| POST | `/license-activations/check-in` | `subscription.activation.check` (nature `read`) | Contrôle d'une installation active sur le site sélectionné : présence mise à jour, `license` en vigueur (nulle : bloquer), `offline_grace_days`, `server_time` ; `404 activation_not_found` |
| POST | `/license-activations/{id}/release` | `subscription.activation.manage` | Libération (`reason`) : une place se libère, licence / période / abonnement inchangés ; `409 activation_already_released` |
| GET | `/subscription/payments` · `/subscription/payments/{id}` | `subscription.subscription.view` | Paiements d'abonnement déclarés à TechNova (Phase 3.3-A, ADR-0032) : paginés (`limit`, `offset`, `status` ; tri `created_at` décroissant par défaut, `amount`, `status`, `period_start`) ; statut, date et motif de la décision (jamais l'agent TechNova) ; `404 subscription_payment_not_found` |
| POST | `/subscription/payments` | `subscription.payment.declare` (nature `billing`) | Déclaration `PENDING` pour l'abonnement d'UN site : `subscription_id`, `payment_method`, `declared_reference`, `idempotency_key`, `requested_activations` facultatif (1 à 10 000 : autre nombre de postes demandé **explicitement**, enregistré seulement s'il diffère des postes actuels), `amount` seulement pour une offre sans tarif (Phase 3.3-B4, ADR-0036). **Période calculée par le serveur** (devis de renouvellement) : `period_start` / `period_end` et tout autre champ (statut, décision, devise…) → `422` ; `422 amount_computed_by_server` (montant envoyé alors qu'un tarif est figé), `422 amount_required` (offre sans tarif) ; `201`, ou `200` si la même clé rejoue la même demande ; `409 idempotency_key_reused`, `404 subscription_not_found`. **Aucune route de décision** côté entreprise |
| GET | `/subscriptions/{id}/renewal-quote` | `subscription.subscription.view` | Devis de la prochaine période d'un site accessible (Phase 3.3-B4) : `kind` (`initial` / `renewal`), `plan` (celui de la prochaine licence), `billing_period`, `valid_from`, `valid_until`, `activations` (postes reconduits, ou `requested_activations` en paramètre), `current_activations`, `activations_explicit`, `amount` (nul : offre sans tarif), `currency`, `coverage_end`, `grace_continuity`, `renewal_due` ; `404 subscription_not_found` |
| GET | `/notifications` | `subscription.subscription.view` (revérifiée par site) | Rappels d'échéance des sites visibles (Phase 3.3-B4) : paginés, filtres `unread`, `site_id` ; `kind`, `step` (jours avant l'échéance, négatif après), `reference_date` (dernier jour couvert), `site`, `data`, `read_at` du membre ; étapes non envoyées (`SKIPPED`) jamais listées |
| GET | `/notifications/unread-count` | `subscription.subscription.view` | `{"unread": n}` pour le membre |
| POST | `/notifications/{id}/read` · `/notifications/read-all` | `subscription.subscription.view` | Marque lu pour le membre (idempotent, `204`) ; `404 notification_not_found` hors des sites visibles. Aucune route ne crée ni ne supprime une notification (job `stockmanager notifications run`) |
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
| GET | `/catalog/articles` | `catalog.article.view` | Liste ; filtres `category_id`, `supplier_id`, `stock_managed` ; tri `reference`, `designation`, `category`, `sale_price`, `purchase_price` (avec `cost_view` seulement), `created_at`. `purchase_price` **absent** sans `catalog.article.cost_view` (Lot 3-A) |
| GET | `/catalog/articles/by-barcode/{code}` | `catalog.article.view` | Article **actif** pour ce code-barres (Lot 3-D : tout code du registre — principal, supplémentaire, conditionnement — désigne son article) |
| GET | `/catalog/barcodes/resolve?code=` | `catalog.article.view` | Scan des écrans opérationnels (Lot 3-D, [ADR-0042](../adr/0042-codes-barres-multiples.md)) : égalité EXACTE parmi les présentations actives → `{article, packaging}` (`packaging` nul : unité de base) ; jamais partielle ; inconnu : `404 barcode_unknown` |
| POST | `/catalog/articles` | `catalog.article.create` | Créer (catégorie / fournisseur actifs) ; `stock_managed` (défaut `true`) ; prix (`sale_price`, `purchase_price`, défaut 0) fixés seulement avec `catalog.article.price_update` (`403 price_update_not_allowed`) |
| GET · PATCH | `/catalog/articles/{id}` | `…view` · `…update` ou `…price_update` | Détail · modifier : champs généraux avec `catalog.article.update`, prix avec `catalog.article.price_update` (valeur inchangée acceptée) ; `stock_managed` : `true → false` seulement à stock nul sur tous les sites (`409 article_has_stock`, `sites`), aucun mouvement créé ; `decimal_quantity_allowed` (Lot 3-B, champ général) : `true → false` refusé si un conditionnement actif a une conversion décimale (`409 article_has_fractional_packagings`) ; Lot 3-G ([ADR-0045](../adr/0045-lots-et-peremption-stock-reception.md)) : `lot_tracked`, `expiry_tracked` (champs généraux, aussi à la création) — **activation du suivi par lot refusée tant que le Lot 3-H n'est pas livré (P1-b, `422 lot_tracking_unavailable`)**, péremption ⇒ lot (`422 expiry_tracking_requires_lots`) ⇒ article géré (`422 lot_tracking_requires_stock`), changement seulement à stock nul sur tous les sites et soldes de lots nuls (`409 article_has_stock`) |
| GET | `/catalog/lot-tracking` | `catalog.article.view` | Lot 3-G : `{available}` — faux tant que la consommation des lots (Lot 3-H) n'est pas livrée (fermeture P1-b, constante du code) |
| GET | `/catalog/articles/{id}/price-history` | `…view` + (`…price_update` ou `audit.log.view`) | Historique des prix lu dans le journal d'audit (création et modifications), paginé, plus récent d'abord ; `sale_price_before/after`, `purchase_price_before/after` (avec `cost_view` seulement) |
| POST | `/catalog/articles/{id}/activate` · `/deactivate` | `catalog.article.status` | Statut (désactivation : ses codes sont libérés ; réactivation refusée si l'un de ses codes — principal ou supplémentaire — est pris : `409 article_barcode_taken`, `codes`) |
| GET | `/catalog/articles/{id}/barcodes` | `catalog.article.view` | Tous les codes de l'article (Lot 3-D) : `kind` `PRIMARY` (miroir du champ `barcode`) / `ADDITIONAL` / `PACKAGING` (`packaging_id`, `packaging_name`), `is_active` (élément porteur actif), paginés |
| POST | `/catalog/articles/{id}/barcodes` · `/catalog/packagings/{id}/barcodes` | `catalog.article.update` | `{code}` (texte libre, 1 à 50 caractères, sans validation EAN) : code supplémentaire de l'article (unité de base) ou code du conditionnement ; unicité commune au tenant parmi les présentations actives (`409 barcode_taken`, `codes`) ; audité |
| DELETE | `/catalog/barcodes/{id}` | `catalog.article.update` | Retire un code supplémentaire ou de conditionnement (`204` ; ancienne valeur dans l'audit) ; code principal : `422 barcode_primary` (se modifie sur l'article) ; `404 barcode_not_found` |
| GET | `/catalog/articles/{id}/packagings` | `catalog.article.view` | Conditionnements de vente de l'article (Lot 3-B, ADR-0040) : paginés, `status` (`active` \| `inactive` \| `all`), tri `conversion` (défaut), `name`, `sale_price`, `created_at` ; `in_use` : figure sur une vente (conversion figée) |
| POST | `/catalog/articles/{id}/packagings` | `catalog.article.update` (+ `price_update` si un prix est fourni, même 0) | `{name, conversion, sale_price?}` : `sale_price` absent = prix **non configuré** (`null`, conditionnement invendable, distinct d'un prix 0) ; conversion `> 0` (3 déc.), **entière** pour un article sans quantités décimales (`422 packaging_conversion_not_whole`) ; nom unique parmi les actifs de l'article (`409 packaging_name_taken`) ; prix sans `price_update` : `403 price_update_not_allowed` ; `in_use`, `sale_price` (`null` : non configuré) en réponse |
| PATCH | `/catalog/packagings/{id}` | `…update` ou `…price_update` | Nom et conversion avec `update`, prix avec `price_update` (valeur inchangée acceptée) ; conversion d'un conditionnement utilisé : `409 packaging_in_use` |
| POST | `/catalog/packagings/{id}/activate` · `/deactivate` | `catalog.article.update` | Statut (aucune suppression) ; réactivation : nom libre parmi les actifs et conversion entière pour un article entier ; Lot 3-D : désactivation = ses codes libérés, réactivation refusée si l'un d'eux est pris (`409 barcode_taken`, `codes`) |
| GET · POST | `/suppliers` | `suppliers.supplier.view` · `.create` | Liste (recherche nom, contact, ville, email, téléphone) · créer |
| GET · PATCH | `/suppliers/{id}` | `…view` · `…update` | Détail · modifier (chaîne vide = champ effacé) |
| POST | `/suppliers/{id}/activate` · `/deactivate` | `suppliers.supplier.status` | Statut |
| GET | `/suppliers/{id}/history` | `audit.log.view` + `suppliers.supplier.view` | Chronologie (Lot 3-E, [ADR-0043](../adr/0043-fiche-fournisseur.md)) : évènements réellement journalisés du fournisseur (`supplier.created`, `updated` avant / après, `activated`, `deactivated`), du plus ancien au plus récent |

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
| GET | `/stock/levels` | `stock.level.view` | Articles × sites : `quantity`, `average_cost` (CMUP, 4 déc.), `stock_value`, seuils effectifs `min_stock`/`max_stock` (surcharge du site sinon article), `min_override`/`max_override`, `state` = `ok` \| `low` \| `out` \| `not_stocked`. Filtres `site_id`, `category_id`, `search`, `state` (`all`, `alerts`, `out`, `low`, `ok`, `not_stocked`), `include_inactive`, `article_id` (répétable : stock disponible des lignes en saisie) ; tri `reference`, `designation`, `category`, `quantity`, `site` ; Lot 3-F : `location_id`, `location_name`, `location_active` (emplacement courant sur le site, nul : non rangé), filtres `location_id`, `unlocated`, recherche aussi sur le nom de l'emplacement, tri `location` |
| PUT | `/stock/levels/{site_id}/{article_id}/thresholds` | `stock.threshold.manage` | Surcharges du site `{min_stock, max_stock}` (`null` = seuil de l'article) ; audité ; ne modifie ni quantité ni CMUP |
| PUT | `/stock/levels/{site_id}/{article_id}/location` | `stock.location.manage` | Lot 3-F ([ADR-0044](../adr/0044-emplacements-par-site.md)) : emplacement COURANT `{location_id}` (`null` : non rangé) ; emplacement ACTIF du MÊME site (`422 stock_location_other_site`, `422 stock_location_inactive`, `404 stock_location_not_found`) ; article géré en stock (`422 article_not_stock_managed`) ; site accessible (`operation_site`) ; audité `stock_location.assigned` (avant / après) ; aucun effet sur le stock ni son état |
| GET · POST | `/stock/locations` | `stock.level.view` · `stock.location.manage` | Emplacements des sites visibles (`site_id`, `status`, `search` ; tri `name`, `site`, `article_count`, `created_at`) : `name`, `is_active`, `article_count` · créer `{site_id?, name}` (nom unique par site, insensible à la casse : `409 stock_location_name_taken`) |
| PATCH | `/stock/locations/{id}` | `stock.location.manage` | Renommer `{name}` ; site non visible : `404 stock_location_not_found` |
| POST | `/stock/locations/{id}/activate` · `/deactivate` | `stock.location.manage` | Statut (jamais de suppression) ; inactif : plus affectable, affectations existantes conservées |
| GET | `/stock/movements` | `stock.movement.view` | Journal : filtres `site_id`, `article_id`, `movement_type` (`ENTRY`, `EXIT`, `SALE`, `CANCELLATION`…), `user_id`, `date_from`, `date_to` (jour du fuseau du tenant), `search` (référence, désignation, numéro de document ou de vente — `source_number`) ; tri `occurred_at` (défaut décroissant) ; `source_type` + `source_id` : mouvements d'un document (ex. `sale`, fiche d'une vente, Lot 2) ; Lot 3-C : `packaging_name`, `packaging_conversion`, `packaging_quantity` (présentation saisie, nuls pour l'unité de base, les mouvements antérieurs et les ajustements) ; Lot 3-G : `lot_id`, `lot_number`, `lot_expiry_date` (réception et annulation de réception), filtre `lot_id`, recherche aussi sur le numéro de lot |
| GET | `/stock/lots` | `stock.level.view` | Lot 3-G : lots ayant un solde (même nul) sur les sites visibles — `number`, article, `expiry_date`, `manufacturing_date`, `state` (`no_expiry`, `ok`, `expiring_soon`, `expired`, calculé avec `tenant_today` et le seuil du tenant), `quantity` (unité de base, sites visibles ou site filtré), `site_count` ; filtres `search` (numéro, article), `article_id`, `site_id`, `state`, `expires_before`, `in_stock` ; tri `expiry_date` (défaut, sans date en fin), `number`, `article`, `quantity`, `created_at` ; **aucun coût** |
| GET | `/stock/available-lots` | `stock.exit.create` | Lot 3-H-A ([ADR-0045](../adr/0045-lots-et-peremption-stock-reception.md)) : lots d'un article (`article_id`) ayant un solde positif sur le site (`site_id`, contrôlé comme une opération) — `{article_id, lot_tracked, expiry_tracked, lots: [{lot_id, number, quantity, expiry_date, manufacturing_date, state, expired, created_at}]}` dans l'ordre de consommation du serveur (lots non périmés en FEFO / FIFO, puis lots périmés) ; **aucun coût** (choix manuel d'une sortie) |
| GET | `/stock/lots/{id}` | `stock.level.view` | Fiche lot + `balances` par site visible ; lot sans solde sur un site visible : `404 stock_lot_not_found` |
| GET · PUT | `/stock/settings` | `stock.level.view` · `stock.threshold.manage` | Seuil « bientôt périmé » `{expiry_warning_days}` (0 à 365, défaut 30) ; modification réservée à un membre ayant accès à tous les sites (`403 tenant_wide_access_required`), auditée `stock_settings.updated` |
| GET | `/stock/exit-reasons` | `stock.reason.view` ou `stock.exit.create` / `.update` | Motifs (filtre `status`, tri `label`) |
| POST · PATCH | `/stock/exit-reasons` · `/{id}` | `stock.reason.manage` | Créer · renommer (motif système : `403 system_exit_reason`) |
| POST | `/stock/exit-reasons/{id}/activate` · `/deactivate` | `stock.reason.manage` | Statut (y compris motifs système) |
| GET · POST | `/stock/entries` | `stock.entry.view` · `.create` | Liste (filtres `status`, `kind`, `supplier_id`, `site_id`, `date_from`, `date_to`, `lot_id` (réceptions d'un lot, Lot 3-G), `search` numéro / référence de pièce / nom du fournisseur (Lot 3-E) ; tri `number`, `operation_date`, `created_at`) · créer un **brouillon** (numéro `ENT-000001` attribué) |
| GET · PUT | `/stock/entries/{id}` | `…view` · `…update` | Détail · remplacer en-tête et lignes d'un brouillon |
| POST | `/stock/entries/{id}/validate` | `stock.entry.validate` | Applique les mouvements `ENTRY` (stock + CMUP du site) |
| — | lignes d'entrée, de sortie, de transfert | — | Lot 3-C ([ADR-0041](../adr/0041-presentations-operations-de-stock.md)) : `packaging_id` facultatif (conditionnement ACTIF de l'article ; nul = unité de base), `quantity` dans cette présentation, `unit_cost` d'entrée par présentation ; réponse : `packaging_name`, `packaging_conversion`, `base_quantity` (calculée par le serveur, seule à mouvementer le stock) ; `422` `packaging_not_found`, `packaging_inactive` (aussi à la validation), `quantity_not_whole` (article entier), `base_quantity_precision`, `duplicate_article_line` (même présentation deux fois) ; `409 packaging_conversion_changed` à la validation |
| — | lignes de réception (Lot 3-G) | — | `lot_number` (50, espaces de bord retirés, comparé sans casse), `lot_expiry_date`, `lot_manufacturing_date` : **obligatoires pour un article suivi par lot** dès le brouillon (`422 lot_number_required`, `lot_expiry_required` si suivi en péremption), interdits sinon (`422 article_not_lot_tracked`) ; fabrication ≤ péremption (`422 lot_dates_invalid`) ; une ligne par présentation ET par lot ; un même lot sur plusieurs lignes : mêmes dates (`422 lot_data_inconsistent`) ; lot connu de l'article : même péremption (`422 lot_expiry_mismatch`), fabrication renseignée identique (`422 lot_manufacturing_mismatch`) ; validation : lot résolu ou créé (`lot_id`), solde du lot par site augmenté ; annulation refusée si un solde de lot devenait négatif (`422 insufficient_lot_stock`, `lots`) ; sortie : `lot_id`, `lot_number`, dates, `lot_state` |
| POST | `/stock/entries/{id}/cancel` | `stock.entry.cancel` | `{reason}` (5–500 car.) : mouvements inverses `CANCELLATION`, CMUP inchangé |
| GET | `/stock/suppliers/{id}/summary` | `stock.entry.view` | Fiche fournisseur (Lot 3-E, [ADR-0043](../adr/0043-fiche-fournisseur.md)) : réceptions `PURCHASE` **VALIDÉES** des sites visibles (`site_id` facultatif) — `validated_count`, `last_received_on`, `received_total` (absent sans `cost_view`) ; brouillons et annulées exclus ; fournisseur inconnu : `404 supplier_not_found` |
| GET | `/stock/suppliers/{id}/articles` | `stock.entry.view` | Articles ayant au moins une réception VALIDÉE du fournisseur (paginés, `search` référence / désignation, `site_id`, tri `last_received_on` (défaut décroissant), `designation`, `reference`, `received_base_quantity`, `receipt_count` ; jamais sur un coût) : `receipt_count`, `received_base_quantity` (unité de base), dernière réception (`last_received_on`, `last_entry_id`, `last_entry_number`), `last_unit_cost` = coût par unité de base de la dernière réception validée (absent sans `cost_view`) |
| GET · POST · GET · PUT | `/stock/exits`, `/stock/exits/{id}` | `stock.exit.*` | Idem (filtre `reason_id`) ; numéro `SOR-000001` |
| POST | `/stock/exits/{id}/validate` · `/cancel` | `stock.exit.validate` · `.cancel` | Mouvements `EXIT` au CMUP du site (coût et montant figés sur les lignes) · annulation |
| — | lignes de sortie (Lot 3-H-A) | — | `lots: [{lot_id, quantity}]` (unité de base) pour un article suivi par lot : choix MANUEL, plusieurs lots ; brouillon : lots de l'article, sans doublon (`422 duplicate_lot_allocation`), somme ≤ quantité de base (`422 lot_allocation_exceeds`), entiers pour un article entier (`422 quantity_not_whole`), **incomplet admis** ; lot interdit pour un article non suivi (`422 article_not_lot_tracked`) ; validation : somme EXACTE (`422 lot_allocation_incomplete`, `requested`, `allocated`), lot présent sur le site (`422 lot_not_available`), soldes sous verrou (`422 insufficient_lot_stock`), lots périmés acceptés (destruction) ; un mouvement `EXIT` par lot ; sortie : `lines[].lots` (choix du brouillon, ou répartition réelle d'une sortie validée / annulée, avec `lot_number`, `expiry_date`, `state`) ; annulation : un inverse par mouvement, sur le même lot |
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
| POST | `/customers` | `customers.customer.create` | Créer (code `CLI-000001` attribué par le serveur, client actif) ; une limite de crédit exige aussi `customers.credit_limit.manage` (`403 credit_limit_not_allowed`, Lot 2) |
| GET | `/customers/{id}` | `customers.customer.view` | Détail (client inactif compris) |
| PATCH | `/customers/{id}` | `customers.customer.update` | Modifier (champ absent : inchangé ; chaîne vide : effacé ; code immuable) ; changer `credit_limit` exige `customers.credit_limit.manage` (valeur inchangée acceptée), audit `customer.credit_limit_changed` avant / après |
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
| GET | `/sales` | `sales.sale.view` | Liste des ventes des sites accessibles (ses propres ventes ; toutes celles du site avec `sales.sale.view_all`, Lot 1) ; `search` (numéro, code / nom / téléphone du client), `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `site_id`, `customer_id`, `date_from`, `date_to`, `payment_status`, `channel`, `seller_id` (auteur = vendeur / opérateur), `mine` (« Mes ventes »), `article_id`, `article_reference` (référence / code-barres d'un article vendu), `payment_reference` (n° de transaction d'un paiement) ; tri `created_at` (**défaut décroissant**, Lot 2), `number`, `sale_date`, `total`, `validated_at` |
| GET | `/sales/sellers` | `sales.sale.view` | Vendeurs / opérateurs proposés au filtre : auteurs des ventes visibles (`[{id, name}]`) |
| GET | `/sales/export` | `sales.sale.export` + `sales.sale.view` | `format` (`xlsx` \| `csv` \| `pdf`) + **mêmes filtres et tri** que `GET /sales` : exactement les ventes de la liste, sans pagination (au plus `SM_EXPORT_MAX_ROWS`, sinon `422 export_too_large`) ; sites où le membre détient l'export ; fichier en pièce jointe ; audité `export.generated` (format, filtres renseignés, nombre de lignes) — ADR-0038 |
| GET | `/sales/{id}/history` | `audit.log.view` + vente visible | Chronologie : évènements réellement journalisés de la vente et de ses paiements (du plus ancien au plus récent) |
| POST | `/sales` | `sales.sale.create` | Créer un **brouillon** (sans numéro : `number` nul ; prix copiés du catalogue, totaux calculés) |
| GET | `/sales/{id}` | `sales.sale.view` | Détail avec lignes |
| PUT | `/sales/{id}` | `sales.sale.update` | Remplacer date, client, observations et lignes d'un brouillon (prix relus) ; site non modifiable |
| POST | `/sales/{id}/validate` | `sales.sale.validate` | Numéro `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` attribué (Lot 1) ; mouvements `SALE` via `StockService` (tout ou rien) ; statut `VALIDATED` ; corps facultatif `{payments: [{payment_method_id, amount?, amount_received?, reference?, cash_register_id?}], credit_override?: {reason}}` : encaissements immédiats (exige aussi `sales.payment.create`) ; reste dû = crédit : client obligatoire (`422 credit_customer_required`), `sales.sale.credit_create` (`403 credit_not_allowed`), limite (`422 credit_limit_exceeded`, `override_allowed`), dépassement avec `sales.sale.credit_override` et justification (`403 credit_override_not_allowed`) ; Lot 3-H-A ([ADR-0045](../adr/0045-lots-et-peremption-stock-reception.md)) : article suivi par lot consommé en **FEFO** (FIFO sans suivi de péremption) sur les lots NON périmés, un mouvement `SALE` par lot ; stock non périmé insuffisant : `422 insufficient_unexpired_stock` (`articles` : `reference`, `missing`, `expired_available`, `expired_lots`) ; dérogation explicite `expired_lot_override?: {reason, lots: [{article_id, lot_id, quantity}]}` (lots PÉRIMÉS désignés, quantités en unité de base ; `sales.sale.expired_lot_override` sur le site : `403 expired_lot_override_not_allowed` ; `422 lot_not_expired`, `lot_not_available`, `lot_allocation_exceeds`, `duplicate_lot_allocation`) — auteur, date et motif sur la vente, audit `sale.expired_lot_overridden` ; sortie : `lines[].lots` (`lot_id`, `lot_number`, `expiry_date`, `quantity`), `expired_lot_override_at`, `_by_name`, `_reason` ; annulation : un inverse par mouvement d'origine, sur le même lot |
| GET | `/sales/articles/{article_id}/lots` | `sales.sale.validate` | Lot 3-H-A : lots disponibles de l'article sur le site de vente (`site_id`) — même réponse que `/stock/available-lots` (ordre FEFO / FIFO, lots périmés signalés `expired`) ; aucun coût |
| POST | `/sales/{id}/cancel` | `sales.sale.cancel` | `{reason}` (5–500 car.) ; brouillon : abandon ; validée : mouvements `CANCELLATION` (remise en stock, CMUP inchangé) |

Corps : `{site_id?, sale_date?, customer_id?, notes?, lines: [{article_id, packaging_id?,
quantity}]}` — **aucun prix ni total** : `unit_price` provient de `catalog_articles.sale_price`
ou, avec `packaging_id` (Lot 3-B, ADR-0040), du prix du conditionnement ; `line_total`,
`subtotal` et `total` sont calculés par le serveur (chaînes décimales en réponse). 1 à 500
lignes, quantité `> 0` (3 déc.) dans la présentation choisie, une ligne par présentation
(article en unité de base, ou conditionnement). Lignes en réponse : `packaging_id`,
`packaging_name`, `packaging_conversion` (instantané figé) et `base_quantity` (quantité ×
conversion, celle qui sort du stock). Lot 3-B : `quantity_not_whole` (422, article sans
quantités décimales), `base_quantity_precision` (422, plus de 3 décimales en unité de base,
jamais arrondie), `packaging_not_found` (422, conditionnement inconnu ou d'un autre article),
`packaging_inactive` et `packaging_price_not_set` (422, prix du conditionnement non configuré —
à l'enregistrement ET à la validation) ; un prix de conditionnement
modifié depuis le brouillon : `sale_prices_changed`.
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
| GET | `/inventories/{id}/lines` | `view` | Lignes paginées : `search`, `state` (`counted`, `uncounted`, `surplus`, `shortage`, `no_variance`), tri `reference`, `designation`, `category`, `variance`, `counted_at` ; Lot 3-D : `article_id` (ligne exacte d'un article identifié par un scan) |
| PATCH | `/inventories/{id}/lines` | `count` | Comptage par lot : `{counts: [{line_id, quantity_physical}]}` (≥ 0, 3 déc. ; `null` efface), 1 à 500 lignes → lignes mises à jour + résumé ; Lot 3-C : ou `{line_id, packaging_id, packaging_quantity, unit_quantity?}` (8 cartons + 5 en vrac → `quantity_physical` 197 calculée par le serveur ; `quantity_physical` avec un conditionnement → `422`) ; lignes : `count_packaging_*`, `count_unit_quantity`, `packagings` (actifs, inventaire ouvert) |
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
Lot 1 ([ADR-0037](../adr/0037-encaissement.md)) : `SaleOut.number` nul pour un brouillon,
`is_credit`, `credit_status` (`OPEN` \| `PARTIAL` \| `PAID` \| `CANCELLED`, calculé),
`credit_override_at`, `credit_override_by_name`, `credit_override_reason`,
`credit_override_amount`. Modifier le code d'un site qui a émis des numéros :
`409 site_code_locked` (`PATCH /sites/{id}`).

### Paiements des ventes (module `sales`) — Phase 2.7

Règles : [`PAYMENTS.md`](PAYMENTS.md) ; décisions : [ADR-0020](../adr/0020-paiements-des-ventes.md).
Mêmes contrôles d'accès que la vente (tenant, site accessible / sélectionné).

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/sales/{sale_id}/payments` | `sales.payment.view` | Historique complet (annulés compris) + `summary` (`total`, `paid_amount`, `remaining_amount`, `payment_status` ; `null` si la vente n'est pas validée) |
| POST | `/sales/{sale_id}/payments` | `sales.payment.create` | `{payment_method_id, amount?, amount_received?, reference?, idempotency_key?, cash_register_id?}` (compatibilité : `method` seul = unique moyen disponible de ce type) → paiement `COMPLETED` `PAY-000001` (201 ; 200 si même clé : réponse rejouée) ; espèces : `amount_received`, monnaie `change_given` calculée par le serveur |
| GET | `/sales/{sale_id}/payments/{payment_id}` | `sales.payment.view` | Détail |
| POST | `/sales/{sale_id}/payments/{payment_id}/cancel` | `sales.payment.cancel` | `{reason}` (5–500 car.) → `CANCELLED` ; vente et stock inchangés |

`method` (type, figé) : `CASH` \| `MOBILE_MONEY` \| `CARD` \| `BANK_TRANSFER` \| `OTHER` ;
`PaymentOut` : `payment_method_id`, `method_label` (instantané), `amount_received`,
`change_given` ; montant > 0, 2 décimales. Codes Lot 1 : `payment_method_not_found`,
`payment_method_unavailable`, `payment_method_required`, `payment_method_mismatch`,
`payment_reference_required`, `change_not_allowed`, `cash_received_insufficient` (422). Codes : `sale_not_payable` (409, `status`), `payment_exceeds_balance` (422,
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

### Moyens de paiement (module `sales`) — Lot 1

Règles : [`PAYMENTS.md`](PAYMENTS.md) ; décisions : [ADR-0037](../adr/0037-encaissement.md).

| Méthode | Chemin | Permission | Rôle |
|---|---|---|---|
| GET | `/payment-methods` (`site_id?`) | `sales.payment.view`, `sales.payment.create` ou `sales.payment_method.manage` | Moyens configurés (`disabled_site_ids` ; avec `site_id` accessible : `available`) |
| POST | `/payment-methods` | `sales.payment_method.manage` | `{label, kind, reference_required?, integration_mode?, sort_order?}` (`API` : `422 payment_integration_unavailable` ; `409 payment_method_label_taken`) |
| PATCH | `/payment-methods/{id}` | `sales.payment_method.manage` | `label`, `reference_required`, `is_active`, `sort_order` (type immuable : `422`) |
| PUT | `/payment-methods/{id}/sites/{site_id}` | `sales.payment_method.manage` sur ce site | `{enabled}` : disponibilité sur un site accessible |

### Caisse (module `cash_register`) — Phase 2.9

Monté sous `/cash`. Règles : [`CASH_REGISTER.md`](CASH_REGISTER.md) ; décisions :
[ADR-0022](../adr/0022-caisse.md). Permissions `cash_register.{register.view, register.manage,
session.view, session.open, session.close, movement.create}`.

| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/cash/sites` | Caisse par site (Lot 1) : sites visibles, `enabled`, `open_sessions` |
| PUT | `/cash/sites/{site_id}` | `{enabled}` — `organization.site.manage` sur ce site ; désactivation refusée si une session est ouverte (`409 cash_sessions_open`) |
| GET / POST | `/cash/registers` | Caisses des sites visibles (`search`, `site_id`, `status`, caisse courante et solde) / création `{site_id?, name, description?}` → `CAI-001` |
| GET / PATCH | `/cash/registers/{id}` | Détail / `name`, `description` (site non modifiable) |
| POST | `/cash/registers/{id}/activate`, `/deactivate` | Désactivation refusée si une session est ouverte |
| GET / POST | `/cash/sessions` | Sessions (`cash_register_id`, `site_id`, `status`, `opened_by`, période) / ouverture `{cash_register_id, opening_float}` → `SES-000001` |
| GET | `/cash/sessions/{id}` | Totaux, solde théorique (figé à la clôture), compté, écart |
| POST | `/cash/sessions/{id}/close` | `{counted_balance, note?}` ; écart calculé par le serveur |
| GET | `/cash/sessions/{id}/movements`, `/cash/movements` | Journal paginé, solde après chaque mouvement ; `movement_type`, `created_by`, `search`, `min_amount`, `max_amount`, période |
| POST | `/cash/sessions/{id}/movements` | Entrée / sortie manuelle `{movement_type, amount, category, reason, reference?, idempotency_key?}` (201 ; 200 si rejouée) |

Paiements : un moyen de type `CASH` exige, **si la caisse du site est activée** (Lot 1), la
session ouverte **de l'utilisateur** sur un poste du site de la vente ; champ facultatif
`cash_register_id` (aussi dans `payments` de la validation et du POS). Codes :
`cash_disabled_for_site`, `cash_sessions_open` (409, Lot 1),
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
| GET | `/pos/articles` | `pos.terminal.use` | Articles du site (`site_id`, `search`, `limit` ≤ 50) : prix du catalogue, stock du site (unité de base), actif, `stock_managed`, `decimal_quantity_allowed`, `packagings` (conditionnements ACTIFS au prix CONFIGURÉ seulement : `id`, `name`, `conversion`, `sale_price` — Lot 3-B) |
| GET | `/pos/articles/by-barcode` | `pos.terminal.use` | Scan (Lot 3-A, Lot 3-D) : `barcode`, `site_id` ; égalité EXACTE sur un code (principal, supplémentaire, conditionnement) d'une présentation ACTIVE, jamais partielle ni sur référence / désignation ; inconnu : `404 barcode_unknown` ; code d'un conditionnement : `scanned_packaging_id` (le panier ajoute 1 conditionnement) ; prix non configuré : `422 packaging_price_not_set` |
| POST | `/pos/checkout` | `pos.terminal.use` + `sales.sale.create` + `sales.sale.validate` (+ `sales.payment.create`) | Création + validation + paiements en une transaction ; `idempotency_key` obligatoire (201 ; 200 `replayed` pour une clé déjà traitée) ; Lot 3-H-A : FEFO automatique (aucun choix de lot par le vendeur), `expired_lot_override?` comme la validation d'une vente ; `sale.lines[].lots` pour le dialogue de confirmation |
| GET | `/pos/articles/{article_id}/lots` | `pos.terminal.use` | Lot 3-H-A (H-D18) : point d'accès DÉDIÉ du POS (jamais `/stock/lots`) — lots ayant un solde positif sur le site de vente (`site_id`), péremption, état, `expired`, ordre du moteur ; aucun coût |

Ventes : `SaleOut.channel` (`BACKOFFICE` \| `POS`), filtre `GET /sales?channel=`.

## Console TechNova (processus distinct, `/platform-api/v1`) — Phase 3.2-F

API séparée de celle des entreprises (`app.console.main`, rôle SQL dédié), réservée aux
administrateurs TechNova ; détail : [`TECHNOVA_CONSOLE.md`](TECHNOVA_CONSOLE.md),
[ADR-0031](../adr/0031-console-technova.md). Session par cookie `HttpOnly`
(`SameSite=Strict`) ; en-tête `X-TechNova-Console: 1` obligatoire sur les requêtes
modifiantes. Aucun jeton de l'API des entreprises n'y est accepté.

| Méthode | Chemin | Rôle |
|---|---|---|
| POST | `/auth/login` · `/auth/logout` | Session de la console (comptes `is_platform_admin` seulement) |
| GET | `/me` · `/dashboard` | Administrateur connecté ; indicateurs des offres et du catalogue |
| GET | `/plans` · `/plans/{code}` | Paramètres commerciaux (+ `self_service`) ; structure technique en lecture seule |
| PATCH | `/plans/{code}/commercial` | Paramètres commerciaux seulement (`extra="forbid"`), `reason` obligatoire ; depuis 3.3-B4 : `monthly_activation_price`, `annual_activation_price` (prix d'un poste supplémentaire ; `monthly_price` / `annual_price` = premier poste ; formule fixe, devise requise) ; `422` : `validation_error`, `unknown_currency`, `price_required`, `currency_required`, `price_display_without_period`, `plan_not_subscribable`, `plan_inactive`, `no_changes` |
| GET | `/catalog` | Catalogue technique (lecture seule) |
| GET | `/audit` | Journal de la plateforme (`limit`, `offset`, `action`, `target_type`, `target_id`, `tenant_id`) |
| GET | `/tenants` · `/tenants/{id}` | Entreprises (Phase 3.2-G ; abonnements par site depuis 3.3-B1) : métadonnées plateforme paginées, résumé des abonnements (`subscription_count`, `plan_codes`, `effective_statuses`, `next_period_end`) ; filtres `search`, `status`, `plan_code` et `subscription_status` (au moins un abonnement) ; tri `name`, `created_at`, `next_period_end`, `status` ; détail : identité, compteurs, **un bloc par abonnement de site** (limites et usage du site, postes demandés, actions possibles) ; `404 tenant_not_found` |
| POST | `/tenants/{id}/suspend` · `/reactivate` | Statut de l'entreprise, `reason` obligatoire ; `409 tenant_already_suspended` / `tenant_not_suspended` |
| POST | `/tenants/{id}/subscriptions/{subscription_id}/activate` · `/extend` · `/change-plan` (abonnement d'un site, ADR-0033) | Activation manuelle transitoire (aucun paiement), prolongation, changement de plan (prix figé ; vaut à la licence suivante si une licence est en vigueur) ; `reason` obligatoire ; double audit (plateforme + entreprise) ; `409 subscription_not_activable` / `subscription_not_extendable` / `subscription_license_controlled` (activation ou prolongation d'un abonnement dont une licence décide la période), `422 invalid_period` / `period_too_long` / `plan_unchanged` / `unknown_plan` |
| GET | `/payments` · `/payments/{id}` | Paiements d'abonnement (Phase 3.3-A, ADR-0032) : paginés (`status`, `tenant_id`, `search` sur la référence ; tri `created_at` décroissant par défaut, `amount`, `status`, `decided_at`) ; entreprise (nom), plan, montant, devise, période, moyen, référence, statut, décision (e-mail de l'administrateur TechNova), motif ; jamais le déclarant ; `404 subscription_payment_not_found` |
| POST | `/payments/{id}/confirm` · `/reject` | Décision définitive d'un paiement `PENDING` sous verrou, `reason` obligatoire (motif du rejet visible par l'entreprise), double audit ; `409 payment_already_decided` ; **aucune activation** |
| GET | `/payments/{id}/license-proposal` | Licence que produirait la génération (Phase 3.3-B2, ADR-0034) : période calculée par le serveur (continuité pendant la grâce : `grace_continuity`, 3.3-B4), postes : `initial_requested_activations` (souscription), `current_activations` (licence de référence), `requested_activations` (demande explicite du paiement, sinon nul), `max_activations` (proposé), période déclarée du paiement, `blocking` (`payment_not_confirmed`, `subscription_site_required`, `tenant_suspended`, `subscription_not_licensable`, `license_already_issued`), `license_id` existante |
| POST | `/payments/{id}/license` | Génération depuis un paiement **confirmé** : `reason`, `max_activations` (1–10 000, figé) ; signature par le Signing Service vérifiée, licence enregistrée, abonnement du site aligné, double audit `license.generated` ; `201` ; `409` (blocages ci-dessus), `503 signing_service_unavailable`, `502 signing_failed` / `license_signature_invalid` / `license_key_unknown` / `license_key_retired` (rien n'est enregistré) |
| GET | `/licenses` · `/licenses/{id}` | Licences (filtres `tenant_id`, `site_id`, `plan_code`, `state` — `NOT_YET_VALID`, `ACTIVE`, `EXPIRED`, `REVOKED` —, `search` sur le numéro ou l'entreprise ; tri `issued_at` décroissant par défaut, `valid_until`, `license_number`) ; fiche : contenu signé, état, révocation, licence remplacée / remplaçante ; `404 license_not_found` |
| GET | `/licenses/{id}/file` | Fichier `.lic` v1 signé (pièce jointe `LIC-….lic`) ; `409 license_revoked` |
| GET | `/activations` | Postes (Phase 3.3-B3) : filtres `tenant_id`, `subscription_id`, `site_id`, `status` ; licence, installation, libellé, version, dates, libération ; jamais l'utilisateur de l'entreprise |
| POST | `/activations/{id}/release` | Libération par TechNova, `reason` obligatoire, double audit ; `409 activation_already_released` |
| POST | `/licenses/{id}/revoke` | Révocation **définitive**, `reason` obligatoire ; abonnement du site suspendu si plus aucune licence ne couvre ce jour ; double audit ; `409 license_already_revoked` |
| POST | `/licenses/{id}/reissue` | Réémission : ancienne licence révoquée, **nouvelle** licence (numéro, version + 1) pour le même paiement et la même période ; `reason`, `max_activations` facultatif (conservé sinon) ; `201` ; `409 license_already_reissued` / `license_already_issued` / `license_expired` / `tenant_suspended` ; mêmes erreurs de signature que la génération |

## Routes des modules métier

Les routeurs des modules métier sont montés sous `/api/v1/<code du module>` (points
remplacés par `/`, ex. `/api/v1/restaurant/tables`) — ou sous le préfixe déclaré par le
manifeste (`route_prefix`, ex. `/api/v1/inventories` pour `inventory_count`) — et **automatiquement protégés** par
`require_module(code)` ; un module peut aussi déclarer des sous-ressources d'un autre module
(`extra_routers`, ex. `/api/v1/customers/{id}/receivables` du module `receivables`), protégées
par
`require_module(code)` : un module non effectif pour le tenant répond
`403 module_unavailable`, quel que soit le client.

### Coûts internes (Lot 3-A, ADR-0039)

Sans `catalog.article.cost_view`, les réponses du catalogue, du stock (niveaux, mouvements,
entrées, sorties, transferts), des inventaires, des alertes et du journal d'audit ne contiennent
**pas** les champs `purchase_price`, `average_cost`, `average_cost_before`, `average_cost_after`,
`stock_value`, `unit_cost`, `amount` / `total_amount` (documents de stock), `adjustment_value`,
`surplus_value`, `shortage_value` (champ absent, jamais remplacé). Articles non gérés en stock
(`stock_managed = false`) : exclus des niveaux, seuils, alertes et candidats d'inventaire ;
entrées, sorties, transferts, inventaires et seuils refusés (`422 article_not_stock_managed`).
