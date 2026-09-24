# Modèle de données

Migrations : `0001` socle plateforme · `0002` fonctionnalités de plan · `0003` catalogue et
fournisseurs (`backend/migrations/versions/`).
Identifiants : UUIDv7 générés par l'application. Horodatages : `timestamptz` (UTC).

## Vue d'ensemble

```text
                         ┌──────────────────────┐
                         │ users (global)       │◄──────────── auth_sessions (global)
                         └──────────┬───────────┘
                                    │ 1..n
┌────────────────────┐   ┌──────────▼───────────┐   ┌───────────────────┐
│ business_profiles  │◄──┤ tenants              ├──►│ subscriptions     │──► plans
│ + profile_modules  │   └──┬──────┬──────┬─────┘   └───────────────────┘   + plan_modules
└────────────────────┘      │      │      │
                   ┌────────▼┐  ┌──▼────┐ │   ┌──────────────────────┐
                   │ sites   │  │ roles │ └──►│ tenant_memberships   │
                   └────┬────┘  └──┬────┘     └──┬────────────────┬──┘
                        │          │ role_permissions│             │
                        │          └──────────┐   │             │
                        └──────── membership_roles (site_id nul = tout le tenant)
                        └──────── membership_sites
   tenant_modules (activations)        audit_logs (append-only)     subscription_access_policies
```

## Tables

### Catalogue plateforme (global, lecture seule pour le rôle applicatif)

| Table | Clé | Contenu |
|---|---|---|
| `business_profiles` | `code` | Nom, navigation (JSONB), terminologie par langue (JSONB), réglages |
| `business_profile_modules` | `profile_code, module_code` | Modules proposés ; `default_enabled` |
| `plans` | `code` | Nom, limites (JSONB, codes déclarés par les modules), fonctionnalités (JSONB), `grace_days` |
| `plan_modules` | `plan_code, module_code` | Modules inclus |
| `subscription_access_policies` | `status` | Natures d'accès autorisées (`text[]`) |

Source : `backend/app/platform/catalog/data/*.toml`, synchronisés par `stockmanager catalog sync`.

### Identité (global)

| Table | Colonnes principales |
|---|---|
| `users` | `email` (unique, minuscules — contrainte `CHECK`), `full_name`, `password_hash` (Argon2id), `is_active`, `must_change_password`, `failed_login_count`, `locked_until`, `last_login_at`, `locale` |
| `auth_sessions` | `user_id`, `refresh_token_hash`, `previous_token_hash`, `rotated_at`, `expires_at`, `revoked_at`, `user_agent`, `ip_address` |

### Tenant (isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `tenants` | `name`, `slug` (unique), `status`, `business_profile_code`, `currency` (XOF), `locale` (fr), `timezone` | RLS sur `id` |
| `sites` | `tenant_id`, `name`, `code`, `kind` (store/warehouse/restaurant/other), `address`, `phone`, `is_active` | `UNIQUE(tenant_id, code)`, `UNIQUE(tenant_id, id)` |
| `tenant_modules` | `tenant_id`, `module_code`, `enabled` | PK `(tenant_id, module_code)` |
| `subscriptions` | `tenant_id` (unique), `plan_code`, `billing_period`, `status`, `started_at`, `current_period_start/end`, `cancelled_at` | |
| `tenant_memberships` | `tenant_id`, `user_id`, `status`, `is_owner`, `all_sites` | `UNIQUE(tenant_id, user_id)` |
| `roles` | `tenant_id`, `name`, `description`, `template_code`, `is_system` | `UNIQUE(tenant_id, name)` ; rôle système : permissions résolues depuis son modèle (ADR-0013) |
| `role_permissions` | `tenant_id`, `role_id`, `permission_code` | FK `(tenant_id, role_id)` |
| `membership_roles` | `tenant_id`, `membership_id`, `role_id`, `site_id` (nullable) | FK composites vers membre, rôle et site du **même tenant** ; unicité `NULLS NOT DISTINCT` |
| `membership_sites` | `tenant_id`, `membership_id`, `site_id` | FK composites |
| `audit_logs` | `tenant_id` (nullable), `site_id`, `user_id`, `action`, `entity_type`, `entity_id`, `data` (JSONB), `ip_address`, `user_agent`, `occurred_at` | Index `(tenant_id, occurred_at)` |

### Catalogue et fournisseurs (Phase 2.1, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `catalog_categories` | `tenant_id`, `name` (100), `is_active` | unique `(tenant_id, lower(name))` |
| `suppliers` | `tenant_id`, `name` (150), `contact_name`, `phone`, `email`, `address`, `city`, `country`, `notes`, `is_active` | nom non unique (règle SUP-02) |
| `catalog_articles` | `tenant_id`, `reference` (50), `designation` (255), `category_id`, `unit` (20), `main_supplier_id`, `purchase_price`, `sale_price` (`NUMERIC(18,2)`), `min_stock`, `max_stock` (`NUMERIC(18,3)`), `description`, `barcode`, `is_active` | unique `(tenant_id, lower(reference))` ; unique partiel `(tenant_id, barcode) WHERE is_active` ; `CHECK` prix ≥ 0, `min_stock` ≥ 0, `max_stock` ≥ `min_stock` ; FK composites vers catégorie et fournisseur du même tenant |

Pas de colonne de stock sur l'article : le stock est tenu par site (Phase 2.2). Droits du rôle
applicatif : `SELECT, INSERT, UPDATE` (jamais de suppression physique).

Les énumérations sont stockées en texte avec contrainte `CHECK` (évolution plus simple qu'un
type PostgreSQL natif).

## Isolation (ADR-0002)

1. **Contexte** : chaque transaction exécute
   `set_config('app.tenant_id', …, true)` et `set_config('app.user_id', …, true)`.
2. **ORM** : toute entité `TenantFiltered` reçoit automatiquement `WHERE tenant_id = <tenant actif>`.
3. **RLS** (`ENABLE` + `FORCE`) :

| Table | Politiques |
|---|---|
| sites, tenant_modules, tenant_memberships, membership_sites, membership_roles, roles, role_permissions, subscriptions, catalog_categories, suppliers, catalog_articles | `tenant_isolation` : `tenant_id = app_current_tenant_id()` (lecture et écriture) |
| tenant_memberships | + `own_memberships_read` : **sans tenant actif**, l'utilisateur lit ses propres appartenances |
| tenants | `tenant_isolation` sur `id` + `member_tenants_read` (**sans tenant actif**) |
| audit_logs | lecture : tenant actif ; insertion : tenant actif ou `tenant_id` nul |

4. **Droits du rôle applicatif** (moindre privilège) :

| Droits | Tables |
|---|---|
| `SELECT` | catalogue |
| `SELECT, INSERT, UPDATE` | users, tenants, sites, tenant_modules, tenant_memberships, subscriptions, catalog_categories, suppliers, catalog_articles |
| `SELECT, INSERT, UPDATE, DELETE` | auth_sessions, roles, role_permissions, membership_sites, membership_roles |
| `SELECT, INSERT` | audit_logs (append-only) |

Pas de `DELETE` sur tenants ni subscriptions : l'expiration ne supprime jamais de données.

## Ajouter une table tenant-scoped (règle pour les modules futurs)

1. Modèle avec `TenantScopedMixin` (colonne `tenant_id` + filtre ORM automatique).
2. Dans la migration : `ENABLE` + `FORCE ROW LEVEL SECURITY`, politique `tenant_isolation`,
   droits minimaux pour le rôle applicatif.
3. Clés étrangères composites `(tenant_id, …)` vers les autres tables du tenant.
4. Tests d'isolation (SQL et API).
