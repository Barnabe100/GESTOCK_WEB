# Modèle de données

Migrations : `0001` socle plateforme · `0002` fonctionnalités de plan · `0003` catalogue et
fournisseurs · `0004` stock · `0005` rôles (RBAC) · `0006` clients · `0007` ventes ·
`0008` transferts inter-sites
(`backend/migrations/versions/`).
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
| `business_sectors` | `code` | Secteur (Phase 3.1) : nom, description, ordre, icône, `is_active` |
| `ux_profiles` | `code` | Profil UX : navigation (rubriques), tableau de bord (widgets, raccourcis), terminologie, thème (JSONB) |
| `business_profiles` | `code` (`<secteur>.<activité>`) | Nom, `sector_code` → `business_sectors`, `ux_profile_code` → `ux_profiles`, ordre ; surcharges : navigation, tableau de bord, terminologie, thème (JSONB) ; réglages. `CHECK` : un profil actif a secteur et profil UX |
| `business_profile_modules` | `profile_code, module_code` | Modules proposés ; `default_enabled` |
| `plans` | `code` | Structure (catalogue) : nom, limites (JSONB, codes déclarés par les modules), fonctionnalités (JSONB), `grace_days`. Paramètres commerciaux (base, console TechNova, jamais écrasés par la synchronisation) : `listed`, `price_display_enabled`, `monthly_price[_enabled]`, `annual_price[_enabled]`, `currency`, `contact_required`, `commercial_description`, `display_order`, `trial_days` |
| `plan_modules` | `plan_code, module_code` | Modules inclus |
| `geo_countries` | `code` (ISO 3166-1 alpha-2) | Pays : nom, devise, indicatif, fuseau par défaut, `is_active` (inscription) |
| `subscription_access_policies` | `status` | Natures d'accès autorisées (`text[]`) |

Source : `backend/app/platform/catalog/data/*.toml`, synchronisés par `stockmanager catalog sync`.

### Identité (global)

| Table | Colonnes principales |
|---|---|
| `users` | `email` (unique, minuscules — contrainte `CHECK`), `full_name`, `password_hash` (Argon2id), `is_active`, `must_change_password`, `failed_login_count`, `locked_until`, `last_login_at`, `locale` |
| `auth_sessions` | `user_id`, `refresh_token_hash`, `previous_token_hash`, `rotated_at`, `expires_at`, `revoked_at`, `user_agent`, `ip_address` |

`users.is_platform_admin` (Phase 3.2-F, ADR-0031) : administrateur TechNova, attribué par la
CLI seulement. RLS sur `users` (`ENABLE`, sans `FORCE`) : le rôle applicatif ne voit ni n'écrit
que `NOT is_platform_admin` (politique `tenant_app_users`) et n'a de droits INSERT / UPDATE que
par colonne, **sans** `is_platform_admin` ; le rôle de la console ne voit que les comptes
TechNova (`platform_admins_read`, `platform_admins_lockout`).

### Plateforme TechNova (Phase 3.2-F, hors tenant — rôle de la console seulement)

| Table | Colonnes principales | Accès |
|---|---|---|
| `platform_sessions` | `user_id`, `token_hash`, `created_at`, `expires_at`, `last_used_at`, `revoked_at`, `ip_address`, `user_agent` | console : `SELECT, INSERT, UPDATE` |
| `platform_audit_logs` | `occurred_at`, `actor_user_id`, `actor_label`, `action`, `target_type`, `target_id`, `tenant_id` (3.2-G), `before`, `after`, `reason`, `data`, `ip_address`, `user_agent` | console : `SELECT, INSERT` ; déclencheur `platform_audit_logs_append_only` (UPDATE / DELETE refusés, même au propriétaire) |

Aucun droit pour le rôle applicatif des tenants sur ces tables.

### Paiements d'abonnement (Phase 3.3-A, isolés par RLS, [ADR-0032](../adr/0032-paiements-abonnement.md))

Paiement de l'abonnement **à TechNova** (distinct des paiements des ventes `payments`).

| Table | Colonnes principales | Règles |
|---|---|---|
| `subscription_payments` | `tenant_id`, `subscription_id` (FK composite `(tenant_id, subscription_id)`), `amount` `NUMERIC(18,2)` > 0, `currency` (ISO, fixée par le serveur), `period_start`, `period_end` (dates, fin > début), `payment_method`, `declared_reference`, `idempotency_key` (unique par tenant), `declared_by`, `status` (`PENDING` / `CONFIRMED` / `REJECTED`), `decided_by`, `decided_at`, `rejection_reason`, `created_at`, `updated_at` | `CHECK` : décision renseignée ⇔ statut décidé, motif ⇔ rejet, référence et motif non vides ; déclencheur `subscription_payments_final` (ligne décidée figée, données déclarées immuables) ; index `(tenant_id, created_at)`, `(status, created_at)` |

Droits : rôle applicatif `SELECT, INSERT` (politique `tenant_isolation`, jamais de modification
ni de suppression) ; rôle de la console `SELECT` (`platform_read`) et `UPDATE (status,
decided_by, decided_at, rejection_reason, updated_at)` d'une ligne `PENDING` vers `CONFIRMED` /
`REJECTED` seulement (`platform_decide`), ni insertion ni suppression. `subscriptions` reçoit
l'unicité `(tenant_id, id)`, cible de la clé étrangère composite. Un paiement confirmé ne
modifie ni l'abonnement ni l'entreprise.

### Tenant (isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `tenants` | `name` (raison sociale), `slug` (unique), `status`, `business_profile_code`, `country_code` → `geo_countries` (obligatoire pour tout nouveau tenant), `currency` (figée), `locale`, `timezone` ; entreprise : `trade_name`, `email`, `phone`, `address`, `city`, `region`, `website`, `tax_id` (IFU), `trade_register` (RCCM), `description`, `logo_url` (https) — **source unique de l'identité de l'entreprise** pour les documents (projection `DocumentIdentity`, ADR-0027 ; aucune copie) | RLS sur `id` |
| `sites` | `tenant_id`, `name`, `code`, `kind` (store/warehouse/restaurant/other), `address`, `phone`, `is_active` | `UNIQUE(tenant_id, code)`, `UNIQUE(tenant_id, id)` |
| `tenant_modules` | `tenant_id`, `module_code`, `enabled` | PK `(tenant_id, module_code)` |
| `subscriptions` | `tenant_id` (unique), `plan_code`, `billing_period`, `status`, `started_at`, `current_period_start/end`, `cancelled_at` | |
| `tenant_memberships` | `tenant_id`, `user_id`, `status` (`active` / `suspended` = « Inactif »), `is_owner`, `all_sites` — **appartenance au tenant**, seule ressource administrée par le tenant ; `users` = identité globale, jamais modifiée par un administrateur de tenant (ADR-0029) | `UNIQUE(tenant_id, user_id)` ; jamais supprimée (désactivation) |
| `roles` | `tenant_id`, `name`, `description`, `template_code`, `is_system`, `is_active` | nom des rôles personnalisés unique par tenant, casse ignorée (index partiel `lower(name)`) ; un exemplaire par modèle (`(tenant_id, template_code)`) ; `CHECK is_system = (template_code IS NOT NULL)` ; rôle de base : nom, description et permissions résolus depuis son modèle (ADR-0013, ADR-0015) ; jamais supprimé (désactivé) |
| `role_permissions` | `tenant_id`, `role_id`, `permission_code` | FK `(tenant_id, role_id)` |
| `membership_roles` | `tenant_id`, `membership_id`, `role_id`, `site_id` (nullable) | FK composites vers membre, rôle et site du **même tenant** ; unicité `NULLS NOT DISTINCT` |
| `membership_sites` | `tenant_id`, `membership_id`, `site_id` | FK composites |
| `onboarding_steps` | `tenant_id`, `step_code`, `status` (`NOT_STARTED`/`IN_PROGRESS`/`COMPLETED`), `completed_at`, `completed_by` → `users` (nul : constat par évaluation), `metadata` (JSONB : `trigger`, démarrage manuel) | `UNIQUE(tenant_id, step_code)` ; `CHECK` : `completed_at` renseigné si et seulement si `COMPLETED` ; déclencheur `onboarding_steps_forward_only` (statut qui n'avance que, `COMPLETED` définitif) ; RLS ; rôle applicatif sans `DELETE` ; lignes créées au premier accès (Phase 3.2-B, ADR-0026) |
| `audit_logs` | `tenant_id` (nullable), `site_id`, `user_id`, `action`, `entity_type`, `entity_id`, `data` (JSONB), `ip_address`, `user_agent`, `occurred_at` | Index `(tenant_id, occurred_at)` |

### Catalogue et fournisseurs (Phase 2.1, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `catalog_categories` | `tenant_id`, `name` (100), `is_active` | unique `(tenant_id, lower(name))` |
| `suppliers` | `tenant_id`, `name` (150), `contact_name`, `phone`, `email`, `address`, `city`, `country`, `notes`, `is_active` | nom non unique (règle SUP-02) |
| `catalog_articles` | `tenant_id`, `reference` (50), `designation` (255), `category_id`, `unit` (20), `main_supplier_id`, `purchase_price`, `sale_price` (`NUMERIC(18,2)`), `min_stock`, `max_stock` (`NUMERIC(18,3)`), `description`, `barcode`, `is_active` | unique `(tenant_id, lower(reference))` ; unique partiel `(tenant_id, barcode) WHERE is_active` ; `CHECK` prix ≥ 0, `min_stock` ≥ 0, `max_stock` ≥ `min_stock` ; FK composites vers catégorie et fournisseur du même tenant |

Pas de colonne de stock sur l'article : le stock est tenu par site (Phase 2.2). Droits du rôle
applicatif : `SELECT, INSERT, UPDATE` (jamais de suppression physique).

### Stock (Phase 2.2, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `document_sequences` | `tenant_id`, `sequence_key`, `next_value` | PK `(tenant_id, sequence_key)` ; incrément atomique `INSERT … ON CONFLICT DO UPDATE … RETURNING` (plateforme, réutilisable) |
| `stock_levels` | `tenant_id`, `site_id`, `article_id`, `quantity` (`NUMERIC(18,3)`), `average_cost` (CMUP, `NUMERIC(18,4)`), `min_stock`, `max_stock` (surcharges du site, nullables) | unique `(tenant_id, site_id, article_id)` ; `CHECK quantity ≥ 0`, `average_cost ≥ 0`, `max ≥ min` ; modifié uniquement par `StockService` (quantité, CMUP) et le service des seuils |
| `stock_movements` | `tenant_id`, `site_id`, `article_id`, `movement_type`, `quantity` (signée), `quantity_before/after`, `unit_cost`, `average_cost_before/after`, `source_type`, `source_id`, `source_line_id`, `source_number` (numéro lisible du document source, Phase 2.4, nul pour les mouvements antérieurs), `origin_movement_id`, `user_id`, `comment`, `occurred_at` | **append-only** ; `CHECK quantity ≠ 0`, `quantity_after = quantity_before + quantity`, `quantity_after ≥ 0` ; unique `(tenant_id, source_line_id, movement_type, site_id)` (anti double application, par site depuis la 2.5) ; source polymorphe sans FK (ADR-0014) |
| `stock_exit_reasons` | `tenant_id`, `code` (motifs système), `label`, `description`, `is_system`, `is_active` | unique `(tenant_id, lower(label))` et `(tenant_id, code)` |
| `stock_entries` | `tenant_id`, `number`, `site_id`, `kind` (`PURCHASE` \| `INITIAL_STOCK`), `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `operation_date`, `supplier_id`, `document_reference`, `comment`, auteurs et dates de création / validation / annulation, `cancellation_reason` | numéro unique par tenant ; `CHECK` fournisseur obligatoire pour un achat ; motif obligatoire si annulée |
| `stock_entry_lines` | `tenant_id`, `entry_id`, `line_no`, `article_id`, `quantity` (> 0), `unit_cost` (`NUMERIC(18,2)`), `amount` | article unique par document |
| `stock_exits` | idem entrées avec `reason_id`, `beneficiary`, `reference` | FK composite vers le motif |
| `stock_exit_lines` | `tenant_id`, `exit_id`, `line_no`, `article_id`, `quantity` (> 0), `unit_cost` (`NUMERIC(18,4)`), `amount` | coût (CMUP du site) et montant **figés à la validation** |

Toutes les FK sont composites `(tenant_id, …)` (site, article, fournisseur, motif, document).
Les motifs système sont créés au provisioning (`tenant_setup` du module) et par la migration
`0004` pour les entreprises existantes.

### Transferts inter-sites (Phase 2.5, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `stock_transfers` | `tenant_id`, `number` (`TRF-000001`), `source_site_id`, `destination_site_id`, `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `operation_date`, `comment`, `created_by`, `validated_at`/`_by`, `cancelled_at`/`_by`, `cancellation_reason` | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, id)` ; FK composites vers **les deux** sites du même tenant ; `CHECK source_site_id <> destination_site_id` ; validé ⇒ `validated_at` ; annulé ⇒ date et motif ; index `(tenant_id, operation_date)` |
| `stock_transfer_lines` | `tenant_id`, `transfer_id`, `line_no`, `article_id`, `quantity` (`NUMERIC(18,3)`), `unit_cost` (`NUMERIC(18,4)`, CMUP source figé à la validation), `amount` (`NUMERIC(18,2)`) | FK composites vers le transfert (`ON DELETE CASCADE`) et l'article ; `UNIQUE (transfer_id, article_id)` ; `CHECK quantity > 0`, coût ≥ 0 |

Numéro : séquence `stock_transfer`. Le stock n'est modifié que par `StockService`
(mouvements `TRANSFER_OUT` / `TRANSFER_IN`, `source_type = 'stock_transfer'`). Détails :
[`CATALOGUE_STOCK.md` §8](CATALOGUE_STOCK.md#8-transferts-inter-sites-phase-25).

### Inventaires (Phase 2.6, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `inventories` | `tenant_id`, `number` (`INV-000001`), `site_id`, `status` (`DRAFT` \| `COUNTING` \| `READY_TO_VALIDATE` \| `VALIDATED` \| `CANCELLED`), `inventory_type` (`FULL` \| `TARGETED`), `comment`, `created_by`, `started_at`/`_by`, `completed_at`/`_by`, `validated_at`/`_by`, `cancelled_at`/`_by`, `cancellation_reason` | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, id)` ; FK composite vers `sites` ; dates obligatoires selon le statut ; annulé ⇒ motif ; index `(tenant_id, created_at)` ; jamais supprimé (pas de `DELETE` pour le rôle applicatif) |
| `inventory_lines` | `tenant_id`, `inventory_id`, `article_id`, `stock_theoretical_initial`, `stock_theoretical_at_validation`, `quantity_physical`, `quantity_variance` (`NUMERIC(18,3)`), `unit_cost` (`NUMERIC(18,4)`, CMUP figé), `adjustment_value` (`NUMERIC(18,2)`, signé), `counted_at`/`_by` | FK composites vers l'inventaire (`ON DELETE CASCADE`) et l'article ; `UNIQUE (inventory_id, article_id)` ; quantités ≥ 0 ; `quantity_variance = quantity_physical − stock_theoretical_at_validation` ; compté ⇒ `counted_at` |

Numéro : séquence `inventory`. Le stock n'est modifié qu'à la validation, par `StockService`
(mouvements `ADJUSTMENT`, `source_type = 'inventory_count'`). Détails :
[`INVENTORY.md`](INVENTORY.md).

### Clients (Phase 2.3, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `customers` | `tenant_id`, `code` (20), `customer_type` (`INDIVIDUAL` \| `BUSINESS`), `name` (150), `legal_name` (200), `tax_id`, `phone`, `phone2` (normalisés), `email`, `address`, `city`, `country`, `notes` (1000), `credit_limit` (`NUMERIC(18,2)`, nullable), `is_active` | `UNIQUE (tenant_id, code)` ; `UNIQUE (tenant_id, id)` (cible des futures FK composites : ventes, créances) ; `CHECK credit_limit ≥ 0` ; téléphone et email **non uniques** ; index GIN trigrammes (`pg_trgm`) sur code, nom, raison sociale, téléphones et email ([ADR-0016](../adr/0016-recherche-trigrammes.md)) ; aucun `site_id` (client de l'entreprise) ; aucun solde stocké |

Référence `CLI-000001` : séquence `customer` de `document_sequences`. Détails :
[`CLIENTS.md`](CLIENTS.md).

### Ventes (Phase 2.4, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `sales` | `tenant_id`, `number` (`VTE-000001`), `site_id`, `customer_id` (nullable : vente comptant anonyme), `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `sale_date`, `subtotal`, `total` (`NUMERIC(18,2)`), `notes`, `created_by`, `validated_at`/`_by`, `cancelled_at`/`_by`, `cancellation_reason` | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, id)` (cible des futurs paiements) ; FK composites vers `sites` et `customers` ; `CHECK` montants ≥ 0, validée ⇒ `validated_at`, annulée ⇒ motif ; index `(tenant_id, sale_date)` |
| `sale_lines` | `tenant_id`, `sale_id`, `line_no`, `article_id`, `quantity` (`NUMERIC(18,3)`), `unit_price` (copié du catalogue), `line_total` (`NUMERIC(18,2)`) | FK composites vers la vente (`ON DELETE CASCADE`) et l'article ; `UNIQUE (sale_id, article_id)` ; `CHECK quantity > 0`, prix et montant ≥ 0 |

Phase 3.0 : `channel` (`BACKOFFICE` par défaut \| `POS`, dimension de reporting) et
`idempotency_key` (`UNIQUE (tenant_id, idempotency_key)`, encaissement en une étape du POS).

Numéro : séquence `sale` de `document_sequences`. Le stock n'est jamais modifié par ces
tables : la validation passe par `StockService` (mouvements `SALE`, `source_type = 'sale'`).
Détails : [`SALES.md`](SALES.md).

### Paiements des ventes (Phase 2.7, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `payments` | `tenant_id`, `number` (`PAY-000001`), `sale_id`, `site_id`, `amount` (`NUMERIC(18,2)`), `method` (`CASH` \| `MOBILE_MONEY` \| `CARD` \| `BANK_TRANSFER` \| `OTHER`), `provider`, `status` (`PENDING` \| `COMPLETED` \| `CANCELLED`), `reference`, `paid_at`, `idempotency_key`, `created_by`, `cancelled_at`/`_by`, `cancellation_reason` | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, idempotency_key)` ; FK composite `(tenant_id, sale_id, site_id)` → `sales (tenant_id, id, site_id)` (même tenant et même site) et vers `sites` ; `CHECK amount > 0` ; annulé ⇒ date et motif ; index `(tenant_id, sale_id)`, `(tenant_id, paid_at)` ; jamais supprimé |

Numéro : séquence `payment`. Aucun état d'encaissement stocké sur `sales` : payé / reste /
état sont calculés à partir des paiements `COMPLETED`. Détails : [`PAYMENTS.md`](PAYMENTS.md).

### Caisse (Phase 2.9, isolée par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `cash_registers` | `tenant_id`, `code` (`CAI-001`), `site_id`, `name`, `description`, `is_active`, `created_by` | `UNIQUE (tenant_id, code)`, `UNIQUE (tenant_id, id, site_id)` ; FK composite vers `sites` ; jamais supprimée |
| `cash_sessions` | `tenant_id`, `number` (`SES-000001`), `cash_register_id`, `site_id`, `status` (`OPEN` \| `CLOSED`), `opening_float`, `opened_at`/`_by`, `closed_at`/`_by`, `theoretical_balance`, `counted_balance`, `variance`, `closing_note` | FK composite `(tenant_id, cash_register_id, site_id)` → caisse ; **index unique partiel** : une session `OPEN` par caisse ; `CHECK` fond ≥ 0, fermée ⇒ comptée, écart = compté − théorique |
| `cash_movements` | `tenant_id`, `cash_session_id`, `cash_register_id`, `site_id`, `movement_type`, `amount` (> 0), `category`, `reason`, `reference`, `source_type`/`_id`/`_number`, `payment_id`, `idempotency_key`, `occurred_at`, `created_by` | FK composites vers la session et vers `payments (tenant_id, id, site_id)` (nouvelle unicité) ; `UNIQUE (tenant_id, payment_id, movement_type)`, `UNIQUE (tenant_id, idempotency_key)` ; manuel ⇒ nature et motif ; vente ⇒ paiement ; append-only (`SELECT, INSERT`) |

Solde théorique = Σ mouvements signés (jamais stocké hors instantané de clôture). Détails :
[`CASH_REGISTER.md`](CASH_REGISTER.md).

### Créances (Phase 2.8) : aucune table

Une créance ouverte est une vente `VALIDATED` dont le reste dû (`total` − paiements
`COMPLETED`) est positif ; l'exposition d'un client est la somme de ces restes. Tout est
calculé à la lecture (`sales/credit.py`) : aucune table, aucune colonne, aucune migration.
`customers.credit_limit` (`NULL` = non configurée) est contrôlée à la validation d'une vente.
Détails : [`RECEIVABLES.md`](RECEIVABLES.md).

Les énumérations sont stockées en texte avec contrainte `CHECK` (évolution plus simple qu'un
type PostgreSQL natif).

## Isolation (ADR-0002)

1. **Contexte** : chaque transaction exécute
   `set_config('app.tenant_id', …, true)` et `set_config('app.user_id', …, true)`.
2. **ORM** : toute entité `TenantFiltered` reçoit automatiquement `WHERE tenant_id = <tenant actif>`.
3. **RLS** (`ENABLE` + `FORCE`) :

| Table | Politiques |
|---|---|
| sites, tenant_modules, tenant_memberships, membership_sites, membership_roles, roles, role_permissions, subscriptions, catalog_categories, suppliers, catalog_articles, customers, document_sequences, stock_* (9 tables, dont transferts), sales, sale_lines | `tenant_isolation` : `tenant_id = app_current_tenant_id()` (lecture et écriture) |
| tenant_memberships | + `own_memberships_read` : **sans tenant actif**, l'utilisateur lit ses propres appartenances |
| tenants | `tenant_isolation` sur `id` + `member_tenants_read` (**sans tenant actif**) |
| audit_logs | lecture : tenant actif ; insertion : tenant actif ou `tenant_id` nul |

4. **Droits du rôle applicatif** (moindre privilège) :

| Droits | Tables |
|---|---|
| `SELECT` | catalogue |
| `SELECT, INSERT, UPDATE` | users, tenants, sites, tenant_modules, tenant_memberships, subscriptions, roles (jamais supprimés, ADR-0015), catalog_categories, suppliers, catalog_articles, customers, document_sequences, stock_levels, stock_exit_reasons, stock_entries, stock_exits, stock_transfers, sales |
| `SELECT, INSERT, UPDATE, DELETE` | auth_sessions, role_permissions, membership_sites, membership_roles, stock_entry_lines, stock_exit_lines, stock_transfer_lines, sale_lines (lignes de brouillon) |
| `SELECT, INSERT` | audit_logs, stock_movements (append-only), subscription_payments (décision : TechNova seule) |

Pas de `DELETE` sur tenants ni subscriptions : l'expiration ne supprime jamais de données.

5. **Droits du rôle de la console TechNova** (`stockmanager_platform`, sans `BYPASSRLS`,
   ADR-0031) : `SELECT` sur le catalogue ; `UPDATE` des seules colonnes commerciales de
   `plans` ; `SELECT` sur `users` (comptes TechNova seulement, RLS) et `UPDATE` des colonnes de
   verrouillage ; `platform_sessions` et `platform_audit_logs` ci-dessus. Depuis 3.2-G
   (migration 0018, politiques RLS `TO` ce rôle) : `tenants` — lecture de `id`, `name`,
   `trade_name`, `slug`, `status`, `business_profile_code`, `country_code`, `currency`,
   `locale`, `timezone`, `created_at`, `updated_at`, mise à jour de `status` seul ;
   `subscriptions` — lecture, mise à jour de `plan_code`, `status`, `current_period_start`,
   `current_period_end`, `price_at_subscription`, `currency_at_subscription` ; `sites` —
   lecture de `tenant_id`, `is_active` ; `tenant_memberships` — lecture de `tenant_id`,
   `status` (compteurs) ; `audit_logs` — **insertion seule** d'entrées miroir (politique
   permissive `platform_mirror_insert` et restrictive `platform_mirror_only` : tenant
   renseigné, `user_id` nul). **Aucun droit** de suppression, aucune lecture d'`audit_logs`,
   des coordonnées des entreprises, des utilisateurs, ni d'aucune table métier. Depuis 3.3-A
   (migration 0019) : `subscription_payments` — lecture, mise à jour des seules colonnes de
   décision d'une ligne `PENDING` (ADR-0032).

## Ajouter une table tenant-scoped (règle pour les modules futurs)

1. Modèle avec `TenantScopedMixin` (colonne `tenant_id` + filtre ORM automatique).
2. Dans la migration : `ENABLE` + `FORCE ROW LEVEL SECURITY`, politique `tenant_isolation`,
   droits minimaux pour le rôle applicatif.
3. Clés étrangères composites `(tenant_id, …)` vers les autres tables du tenant.
4. Tests d'isolation (SQL et API).
