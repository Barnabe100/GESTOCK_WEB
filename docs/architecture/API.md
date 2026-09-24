# API REST — socle plateforme (Phase 1) et catalogue (Phase 2.1)

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
| PATCH | `/members/{id}` | `users.member.manage` | Rôles, sites, statut |
| GET | `/roles` | `users.role.view` | Rôles du tenant |
| POST | `/roles` | `users.role.manage` | Créer un rôle |
| GET | `/roles/{id}` | `users.role.view` | Détail |
| PATCH | `/roles/{id}` | `users.role.manage` | Modifier (rôles système exclus) |
| DELETE | `/roles/{id}` | `users.role.manage` | Supprimer (si non attribué) |
| GET | `/permissions` | `users.role.view` | Permissions des modules effectifs |
| GET | `/role-templates` | `users.role.view` | Modèles de rôles système (instanciés ou non) |
| POST | `/roles/from-template` | `users.role.manage` | Ajouter au tenant un rôle système manquant |
| GET | `/subscription` | `subscription.subscription.view` | Offre, statut effectif, période, limites, utilisation |
| GET | `/audit-logs` | `audit.log.view` | Journal d'audit paginé (`limit`, `offset`, `action`, `user_id`) |

« tenant » = jeton lié à un tenant, appartenance active, tenant actif, mot de passe à jour.

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

## Routes des modules métier

Les routeurs des modules métier seront montés sous `/api/v1/<code du module>` (points
remplacés par `/`, ex. `/api/v1/restaurant/tables`) et **automatiquement protégés** par
`require_module(code)` : un module non effectif pour le tenant répond
`403 module_unavailable`, quel que soit le client.
