# API REST — Phase 1 (socle plateforme)

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
| GET | `/subscription` | `subscription.subscription.view` | Offre, statut effectif, période, limites, utilisation |
| GET | `/audit-logs` | `audit.log.view` | Journal d'audit paginé (`limit`, `offset`, `action`, `user_id`) |

« tenant » = jeton lié à un tenant, appartenance active, tenant actif, mot de passe à jour.

## Routes des modules métier

Les routeurs des modules métier seront montés sous `/api/v1/<code du module>` (points
remplacés par `/`, ex. `/api/v1/restaurant/tables`) et **automatiquement protégés** par
`require_module(code)` : un module non effectif pour le tenant répond
`403 module_unavailable`, quel que soit le client.
