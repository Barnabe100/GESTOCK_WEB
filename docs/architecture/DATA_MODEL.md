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

### Licences (Phase 3.3-B2, isolées par RLS, [ADR-0034](../adr/0034-licences-et-signing-service.md))

Licence d'un site, générée par TechNova depuis un paiement confirmé, signée (Ed25519) par le
Signing Service — la clé privée n'est jamais en base. Voir [`LICENSING.md`](LICENSING.md).

| Table | Colonnes principales | Règles |
|---|---|---|
| `licenses` | `tenant_id`, `site_id`, `subscription_id`, `payment_id`, `license_number` (`LIC-AAAA-NNNNN`, unique global, séquence `license_number_seq`), `license_version`, `supersedes_id`, `plan_code`, `billing_period`, `valid_from` / `valid_until` (jours inclus, fuseau de l'entreprise), `timezone`, `starts_at` / `ends_at` (instants UTC), `max_activations` (1–10 000), `modules`, `features`, `limits` (JSONB, figés), `issued_at`, `issued_by`, `key_id`, `payload` (JSONB signé), `signature`, `payload_sha256`, `status` (`ISSUED` / `REVOKED`), `revoked_at`, `revoked_by`, `revocation_reason` | FK composites `(tenant_id, site_id, subscription_id)` → `subscriptions (tenant_id, site_id, id)`, `(tenant_id, subscription_id, payment_id)` → `subscription_payments (tenant_id, subscription_id, id)`, `(tenant_id, supersedes_id)` → `licenses` ; index uniques partiels : une licence `ISSUED` par paiement, une réémission par licence ; `CHECK` (période, instants, postes, version ⇔ `supersedes_id`, révocation complète, format du numéro) ; déclencheur `licenses_final` (seule transition : `ISSUED` → `REVOKED`, aucune autre colonne modifiable, même pour le propriétaire) |

Droits : rôle applicatif `SELECT` (politique `tenant_isolation`) ; rôle de la console `SELECT`
(`platform_read`), `INSERT` d'une licence `ISSUED` (`platform_issue`), `UPDATE (status,
revoked_at, revoked_by, revocation_reason, updated_at)` d'une licence `ISSUED` vers `REVOKED`
(`platform_revoke`), `USAGE` de la séquence ; aucune suppression. `subscriptions` reçoit
l'unicité `(tenant_id, site_id, id)` et `subscription_payments` l'unicité `(tenant_id,
subscription_id, id)`, cibles des FK composites.

### Postes (Phase 3.3-B3, isolés par RLS, [ADR-0035](../adr/0035-postes-activations.md))

| Table | Colonnes principales | Règles |
|---|---|---|
| `license_activations` | `tenant_id`, `site_id`, `subscription_id`, `license_id` (licence en vigueur à l'activation), `installation_id` (UUID aléatoire de l'installation), `label`, `client_version`, `activated_at`, `activated_by`, `last_seen_at`, `status` (`ACTIVE` / `RELEASED`), `released_at`, `released_by`, `release_source` (`TENANT` / `TECHNOVA`), `release_reason` | FK composites `(tenant_id, site_id, subscription_id)` → `subscriptions (tenant_id, site_id, id)`, `(tenant_id, license_id)` → `licenses` ; index unique partiel `(tenant_id, installation_id) WHERE status = 'ACTIVE'` ; index `(tenant_id, subscription_id, status)` ; `CHECK` libération complète ⇔ `RELEASED`, libellé et raison non vides ; déclencheur `license_activations_final` (identité immuable, poste libéré figé) |

Droits : rôle applicatif `SELECT, INSERT` et `UPDATE (label, client_version, last_seen_at,
status, released_at, released_by, release_source, release_reason, updated_at)` ; rôle de la
console `SELECT` et `UPDATE` des colonnes de libération d'un poste `ACTIVE` vers `RELEASED`
(`platform_release`) ; aucune suppression.

### Renouvellement et rappels d'échéance (Phase 3.3-B4, isolés par RLS, [ADR-0036](../adr/0036-renouvellement-et-notifications.md))

| Table | Colonnes principales | Règles |
|---|---|---|
| `notifications` | `tenant_id`, `site_id`, `subscription_id`, `kind` (`subscription.expiry`), `step` (jours avant l'échéance, négatif après), `reference_date` (dernier jour couvert, fuseau de l'entreprise), `status` (`SENT` / `SKIPPED`), `data` (JSONB : jours restants, plan, statut effectif, essai), `created_at` | Unicité `(tenant_id, subscription_id, kind, step, reference_date)` (idempotence du job) ; FK composites `(tenant_id, site_id)` → `sites`, `(tenant_id, subscription_id)` → `subscriptions` ; index `(tenant_id, created_at)` |
| `notification_reads` | `tenant_id`, `notification_id`, `user_id`, `read_at` | Clé `(notification_id, user_id)` : lu / non lu **par membre** ; FK composite `(tenant_id, notification_id)` → `notifications` |

Colonnes ajoutées (migration 0023) : `plans.monthly_activation_price`,
`plans.annual_activation_price` (prix d'un poste supplémentaire ; le prix de la période est
celui du premier poste) ; `subscriptions.activation_price_at_subscription` (tarif figé, `CHECK` :
jamais sans prix du premier poste) ; formule fixe premier poste + (postes − 1) × poste
supplémentaire — la migration 0024 retire `plans.included_activations` et
`subscriptions.included_activations_at_subscription` introduits par 0023 ; `subscription_payments.requested_activations` (1 à 10 000, demande
explicite ; figée par le déclencheur de finalité). Droits : rôle applicatif `SELECT` sur
`notifications`, `SELECT, INSERT` sur `notification_reads` (politique `own_reads` : membre
courant seulement) ; rôle de la console `SELECT, INSERT` sur `notifications` (job), `UPDATE` des
colonnes de tarif par poste des plans et des abonnements ; aucune mise à jour ni suppression de
notification.

### Tenant (isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `tenants` | `name` (raison sociale), `slug` (unique), `status`, `business_profile_code` (profil d'origine / d'inscription ; le profil métier est porté par chaque site, migration 0039), `country_code` → `geo_countries` (obligatoire pour tout nouveau tenant), `currency` (figée), `locale`, `timezone` ; entreprise : `trade_name`, `email`, `phone`, `address`, `city`, `region`, `website`, `tax_id` (IFU), `trade_register` (RCCM), `description`, `logo_url` (https) — **source unique de l'identité de l'entreprise** pour les documents (projection `DocumentIdentity`, ADR-0027 ; aucune copie) | RLS sur `id` |
| `sites` | `tenant_id`, `name`, `code`, `kind` (store/warehouse/restaurant/other : nature physique, jamais un profil), `address`, `phone`, `is_active`, `business_profile_code` (profil d'activité **du site**, migration 0039 : initialisé avec le profil de l'entreprise ; ne pilote pas encore modules ni capacités — paliers suivants) | `UNIQUE(tenant_id, code)`, `UNIQUE(tenant_id, id)` ; FK `business_profile_code` → `business_profiles` (`ON DELETE RESTRICT`, `NOT NULL`) ; lecture de cette colonne seule accordée au rôle de la console |
| `tenant_modules` | `tenant_id`, `module_code`, `enabled` | PK `(tenant_id, module_code)` |
| `subscriptions` | `tenant_id`, `site_id` (1 site = 1 abonnement, ADR-0033 : clé étrangère composite `(tenant_id, site_id)`, unique ; nul seulement pour l'abonnement d'inscription en attente du premier site — au plus un par entreprise), `plan_code`, `billing_period`, `status`, `started_at`, `current_period_start/end`, `cancelled_at`, prix figé, `requested_activations` (postes demandés, ≥ 1, défaut 1) | |
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
| `catalog_articles` | `tenant_id`, `reference` (50), `designation` (255), `category_id`, `unit` (20), `main_supplier_id`, `purchase_price`, `sale_price` (`NUMERIC(18,2)`), `min_stock`, `max_stock` (`NUMERIC(18,3)`), `description`, `barcode`, `is_active`, `stock_managed` (`BOOLEAN NOT NULL DEFAULT true`, Lot 3-A, migration 0026 : `false` = vendu sans stock), `decimal_quantity_allowed` (`BOOLEAN NOT NULL DEFAULT false`, Lot 3-B, migration 0027 : `false` = quantités vendues entières), `lot_tracked`, `expiry_tracked` (`BOOLEAN NOT NULL DEFAULT false`, Lot 3-G, migration 0034 ; activation fermée jusqu'au Lot 3-H, P1-b) | `CHECK NOT expiry_tracked OR lot_tracked`, `CHECK NOT lot_tracked OR stock_managed` ; unique `(tenant_id, lower(reference))` ; unique partiel `(tenant_id, barcode) WHERE is_active` ; `CHECK` prix ≥ 0, `min_stock` ≥ 0, `max_stock` ≥ `min_stock` ; FK composites vers catégorie et fournisseur du même tenant |
| `catalog_packagings` | `tenant_id`, `article_id`, `name` (50, libre), `conversion` (`NUMERIC(18,3)`, unités de base par conditionnement), `sale_price` (`NUMERIC(18,2)`, prix propre ; `NULL` = prix non configuré, conditionnement invendable — migration 0028), `is_active` — Lot 3-B ([ADR-0040](../adr/0040-quantites-decimales-conditionnements.md)) : jamais supprimé ; conversion figée dès qu'une ligne de vente l'utilise | FK composite `(tenant_id, article_id)` ; unique partiel `(tenant_id, article_id, lower(name)) WHERE is_active` ; `CHECK conversion > 0`, `sale_price ≥ 0` ; RLS `ENABLE` + `FORCE` |
| `catalog_barcodes` | `tenant_id`, `article_id`, `packaging_id` (nul = unité de base), `code` (50, texte libre), `kind` (`PRIMARY` = miroir de `catalog_articles.barcode` tenu par déclencheur, `ADDITIONAL`, `PACKAGING`), `is_active` (tenu par déclencheurs : article actif ; pour un code de conditionnement, article ET conditionnement actifs — migration 0031) — Lot 3-D ([ADR-0042](../adr/0042-codes-barres-multiples.md), migration 0030) : un code identifie UNE présentation ; un élément désactivé libère ses codes (conservés) | unique partiel `(tenant_id, code) WHERE is_active` (unicité commune : articles et conditionnements) ; unique partiel `(tenant_id, article_id) WHERE kind = 'PRIMARY'` ; FK composites `(tenant_id, article_id)` et `(tenant_id, article_id, packaging_id)` → `catalog_packagings (tenant_id, article_id, id)` (unique ajoutée) ; `CHECK` nature valide, `(kind = 'PACKAGING') = (packaging_id IS NOT NULL)`, code non vide sans espaces de bord ; RLS `ENABLE` + `FORCE` |
| `catalog_site_articles` | `tenant_id`, `site_id`, `article_id`, `is_active`, `added_at`, `added_by`, `removed_at`, `removed_by`, horodatages — **assortiment** d'un site (Recette, étape 1, [ADR-0046](../adr/0046-assortiment-par-site.md), migration 0038) : articles du catalogue que ce site propose ; distinct du catalogue et du stock (aucun niveau créé) ; retrait = `is_active` faux (jamais supprimée), réactivation = même ligne ; reprise par usage réel (`added_by` nul = « reprise initiale ») | unique `(tenant_id, site_id, article_id)` ; index `(tenant_id, article_id)` ; FK composites `(tenant_id, site_id)` → `sites` et `(tenant_id, article_id)` → `catalog_articles` (RESTRICT) ; `CHECK is_active = (removed_at IS NULL)` et `removed_by` seulement avec `removed_at` ; RLS `ENABLE` + `FORCE` |

Pas de colonne de stock sur l'article : le stock est tenu par site (Phase 2.2). Droits du rôle
applicatif : `SELECT, INSERT, UPDATE` (jamais de suppression physique).

### Stock (Phase 2.2, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `document_sequences` | `tenant_id`, `sequence_key`, `next_value` | PK `(tenant_id, sequence_key)` ; incrément atomique `INSERT … ON CONFLICT DO UPDATE … RETURNING` (plateforme, réutilisable) |
| `stock_levels` | `tenant_id`, `site_id`, `article_id`, `quantity` (`NUMERIC(18,3)`), `average_cost` (CMUP, `NUMERIC(18,4)`), `min_stock`, `max_stock` (surcharges du site, nullables) | unique `(tenant_id, site_id, article_id)` ; `CHECK quantity ≥ 0`, `average_cost ≥ 0`, `max ≥ min` ; modifié uniquement par `StockService` (quantité, CMUP) et le service des seuils |
| `stock_locations` | `tenant_id`, `site_id`, `name` (100), `is_active`, horodatages — Lot 3-F ([ADR-0044](../adr/0044-emplacements-par-site.md), migration 0033) : emplacement physique d'UN site, jamais supprimé | unique `(tenant_id, site_id, lower(name))` ; unique `(tenant_id, site_id, id)` (cible de FK) ; FK composite `(tenant_id, site_id)` → `sites` ; `CHECK` nom non vide sans espaces de bord ; RLS `ENABLE` + `FORCE` |
| `stock_article_locations` | `tenant_id`, `site_id`, `article_id`, `location_id`, horodatages — emplacement COURANT (au plus un) d'un article sur un site ; aucune quantité | unique `(tenant_id, site_id, article_id)` ; FK composites `(tenant_id, article_id)` → `catalog_articles` et **`(tenant_id, site_id, location_id)` → `stock_locations`** (emplacement d'un autre site inaffectable) ; RLS `ENABLE` + `FORCE` |
| `stock_lots` | `tenant_id`, `article_id`, `number` (50), `expiry_date`, `manufacturing_date` (`DATE`, nullables), `created_by`, horodatages — Lot 3-G ([ADR-0045](../adr/0045-lots-et-peremption-stock-reception.md), migration 0034) : lot d'un article, créé à la validation d'une réception, **figé** (ni modifié ni supprimé), aucun coût | unique `(tenant_id, article_id, lower(number))` ; unique `(tenant_id, article_id, id)` (cible des FK composites) ; FK `(tenant_id, article_id)` → `catalog_articles` ; `CHECK` numéro sans espaces de bord, fabrication ≤ péremption ; RLS `ENABLE` + `FORCE` |
| `stock_lot_levels` | `tenant_id`, `site_id`, `article_id`, `lot_id`, `quantity` (`NUMERIC(18,3)`, unité de base) — solde d'un lot sur un site, ventilation du stock du site (Σ lots = stock pour un article suivi), tenu par `StockService` | unique `(tenant_id, site_id, lot_id)` ; `CHECK quantity ≥ 0` ; FK `(tenant_id, site_id)` → `sites`, **`(tenant_id, article_id, lot_id)` → `stock_lots`** ; RLS `ENABLE` + `FORCE` |
| `stock_settings` | `tenant_id` (unique), `expiry_warning_days` (0 à 365) — seuil « bientôt périmé » du tenant (ligne absente : 30 jours) | RLS `ENABLE` + `FORCE` |
| `stock_movements` | `tenant_id`, `site_id`, `article_id`, `movement_type`, `quantity` (signée), `quantity_before/after`, `unit_cost`, `average_cost_before/after`, `source_type`, `source_id`, `source_line_id`, `source_number` (numéro lisible du document source, Phase 2.4, nul pour les mouvements antérieurs), `origin_movement_id`, `user_id`, `comment`, `occurred_at` | **append-only** ; `CHECK quantity ≠ 0`, `quantity_after = quantity_before + quantity`, `quantity_after ≥ 0` ; unique `(tenant_id, source_line_id, movement_type, site_id)` (anti double application, par site depuis la 2.5 ; **Lot 3-H-A, migration 0035 : `(tenant_id, source_line_id, movement_type, site_id, lot_id)` `NULLS NOT DISTINCT`** — un mouvement par lot pour une ligne répartie, toujours un seul sans lot) ; source polymorphe sans FK (ADR-0014) ; Lot 3-C (migration 0029) : `packaging_id`, `packaging_name`, `packaging_conversion`, `packaging_quantity` (présentation saisie, nuls = unité de base ; `CHECK` instantané complet ou absent, `abs(quantity) = packaging_quantity × packaging_conversion`) ; Lot 3-G (migration 0034) : `lot_id` (réception, annulation de réception ; FK composite `(tenant_id, article_id, lot_id)` → `stock_lots`) ; Lot 3-H-A : `lot_id` aussi sur les ventes, sorties et leurs annulations (obligatoire pour un article suivi, interdit sinon — garde-fou de `StockService`) |
| `stock_transfer_line_lots` | `tenant_id`, `transfer_line_id`, `article_id`, `lot_id`, `position`, `quantity` (`NUMERIC(18,3)`, unité de base, > 0) — Lot 3-H-B1 ([ADR-0045](../adr/0045-lots-et-peremption-stock-reception.md), migration 0036) : choix manuels des lots d'une ligne de transfert BROUILLON (pris au site source, reçus sous le même lot au site destination ; supprimés et recréés avec la ligne, revalidés à la validation ; jamais source historique) | unique `(transfer_line_id, lot_id)` ; FK composites `(tenant_id, transfer_line_id, article_id)` → `stock_transfer_lines (tenant_id, id, article_id)` (`ON DELETE CASCADE`) et `(tenant_id, article_id, lot_id)` → `stock_lots` ; RLS `ENABLE` + `FORCE` |
| `stock_exit_line_lots` | `tenant_id`, `exit_line_id`, `article_id`, `lot_id`, `position`, `quantity` (`NUMERIC(18,3)`, unité de base, > 0) — Lot 3-H-A ([ADR-0045](../adr/0045-lots-et-peremption-stock-reception.md), migration 0035) : choix manuels des lots d'une ligne de sortie BROUILLON (supprimés et recréés avec la ligne, revalidés à la validation ; jamais source historique) | unique `(exit_line_id, lot_id)` ; FK composites `(tenant_id, exit_line_id, article_id)` → `stock_exit_lines (tenant_id, id, article_id)` (`ON DELETE CASCADE`) et `(tenant_id, article_id, lot_id)` → `stock_lots` ; RLS `ENABLE` + `FORCE` |
| `stock_exit_reasons` | `tenant_id`, `code` (motifs système), `label`, `description`, `is_system`, `is_active` | unique `(tenant_id, lower(label))` et `(tenant_id, code)` |
| `stock_entries` | `tenant_id`, `number`, `site_id`, `kind` (`PURCHASE` \| `INITIAL_STOCK`), `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `operation_date`, `supplier_id`, `document_reference`, `comment`, auteurs et dates de création / validation / annulation, `cancellation_reason` | numéro unique par tenant ; `CHECK` fournisseur obligatoire pour un achat ; motif obligatoire si annulée ; index `(tenant_id, supplier_id)` (Lot 3-E, migration 0032 : fiche fournisseur) |
| `stock_entry_lines` | `tenant_id`, `entry_id`, `line_no`, `article_id`, `quantity` (> 0), `unit_cost` (`NUMERIC(18,2)`, par présentation), `amount` ; Lot 3-C (migration 0029) : `packaging_id`, `packaging_name`, `packaging_conversion` (instantané, nuls = unité de base), `base_quantity` (`NUMERIC(18,3)`, quantité de stock) ; `quantity` dans la présentation saisie ; Lot 3-G (migration 0034) : `lot_number` (50), `lot_expiry_date`, `lot_manufacturing_date` (saisis dans le brouillon), `lot_id` (résolu à la validation) | unique `(entry_id, article_id, COALESCE(lower(lot_number), '')) WHERE packaging_id IS NULL` et `(entry_id, packaging_id, COALESCE(lower(lot_number), '')) WHERE packaging_id IS NOT NULL` (Lot 3-G : une ligne par présentation et par lot) ; `CHECK base_quantity = quantity × COALESCE(packaging_conversion, 1)` ; FK composite `(tenant_id, article_id, lot_id)` → `stock_lots` ; `CHECK` lot complet, fabrication ≤ péremption |
| `stock_exits` | idem entrées avec `reason_id`, `beneficiary`, `reference` | FK composite vers le motif |
| `stock_exit_lines` | `tenant_id`, `exit_id`, `line_no`, `article_id`, `quantity` (> 0), `unit_cost` (`NUMERIC(18,4)`), `amount` ; Lot 3-C (migration 0029) : `packaging_id`, `packaging_name`, `packaging_conversion` (instantané, nuls = unité de base), `base_quantity` (`NUMERIC(18,3)`, quantité de stock) ; `quantity` dans la présentation saisie | coût (CMUP du site) et montant **figés à la validation** ; unicité par présentation et `CHECK` comme les entrées ; unique `(tenant_id, id, article_id)` (Lot 3-H-A, cible de la FK composite des choix de lots) |

Toutes les FK sont composites `(tenant_id, …)` (site, article, fournisseur, motif, document).
Les motifs système sont créés au provisioning (`tenant_setup` du module) et par la migration
`0004` pour les entreprises existantes.

### Transferts inter-sites (Phase 2.5, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `stock_transfers` | `tenant_id`, `number` (`TRF-000001`), `source_site_id`, `destination_site_id`, `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `operation_date`, `comment`, `created_by`, `validated_at`/`_by`, `cancelled_at`/`_by`, `cancellation_reason` | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, id)` ; FK composites vers **les deux** sites du même tenant ; `CHECK source_site_id <> destination_site_id` ; validé ⇒ `validated_at` ; annulé ⇒ date et motif ; index `(tenant_id, operation_date)` |
| `stock_transfer_lines` | `tenant_id`, `transfer_id`, `line_no`, `article_id`, `quantity` (`NUMERIC(18,3)`), `unit_cost` (`NUMERIC(18,4)`, CMUP source figé à la validation), `amount` (`NUMERIC(18,2)`) ; Lot 3-C (migration 0029) : `packaging_id`, `packaging_name`, `packaging_conversion` (instantané, nuls = unité de base), `base_quantity` (`NUMERIC(18,3)`, quantité de stock) ; `quantity` dans la présentation saisie | FK composites vers le transfert (`ON DELETE CASCADE`), l'article et le conditionnement ; unique par présentation (Lot 3-C, remplace `UNIQUE (transfer_id, article_id)`) ; `CHECK quantity > 0`, coût ≥ 0 |

Numéro : séquence `stock_transfer`. Le stock n'est modifié que par `StockService`
(mouvements `TRANSFER_OUT` / `TRANSFER_IN`, `source_type = 'stock_transfer'`). Détails :
[`CATALOGUE_STOCK.md` §8](CATALOGUE_STOCK.md#8-transferts-inter-sites-phase-25).

### Inventaires (Phase 2.6, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `inventories` | `tenant_id`, `number` (`INV-000001`), `site_id`, `status` (`DRAFT` \| `COUNTING` \| `READY_TO_VALIDATE` \| `VALIDATED` \| `CANCELLED`), `inventory_type` (`FULL` \| `TARGETED`), `comment`, `created_by`, `started_at`/`_by`, `completed_at`/`_by`, `validated_at`/`_by`, `cancelled_at`/`_by`, `cancellation_reason` | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, id)` ; FK composite vers `sites` ; dates obligatoires selon le statut ; annulé ⇒ motif ; index `(tenant_id, created_at)` ; jamais supprimé (pas de `DELETE` pour le rôle applicatif) |
| `inventory_lines` | `tenant_id`, `inventory_id`, `article_id`, `lot_tracked` (Lot 3-H, migration 0037 : suivi par lot de l'article FIGÉ au démarrage, relu sous verrou à la validation), `stock_theoretical_initial`, `stock_theoretical_at_validation`, `quantity_physical`, `quantity_variance` (`NUMERIC(18,3)`), `unit_cost` (`NUMERIC(18,4)`, CMUP figé), `adjustment_value` (`NUMERIC(18,2)`, signé), `counted_at`/`_by` ; Lot 3-C (migration 0029) : `count_packaging_id`, `count_packaging_name`, `count_packaging_conversion`, `count_packaging_quantity`, `count_unit_quantity` (comptage en conditionnement + vrac ; `quantity_physical` calculée par le serveur) | `CHECK` comptage en conditionnement complet ou absent, `quantity_physical = count_packaging_quantity × count_packaging_conversion + count_unit_quantity` ; FK composites vers l'inventaire (`ON DELETE CASCADE`) et l'article ; `UNIQUE (inventory_id, article_id)` ; quantités ≥ 0 ; `quantity_variance = quantity_physical − stock_theoretical_at_validation` ; compté ⇒ `counted_at` ; Lot 3-H : `UNIQUE (tenant_id, id, article_id)` (cible des lignes de lots) |
| `inventory_line_lots` | `tenant_id`, `inventory_line_id`, `article_id`, `lot_id` (nul pour un lot découvert nouveau), `discovered`, `lot_number` (50), `expiry_date`, `manufacturing_date` (lot découvert), `stock_theoretical_initial`, `stock_theoretical_at_validation`, `quantity_physical`, `quantity_variance` (`NUMERIC(18,3)`, unité de base), `counted_at`/`_by`, `count_packaging_*`, `count_unit_quantity` — Lot 3-H ([ADR-0045](../adr/0045-lots-et-peremption-stock-reception.md), migration 0037) : comptage PAR LOT d'une ligne suivie ; lots attendus (solde non nul au démarrage, non saisis = 0) et découverts (lot créé à la validation seulement) ; **aucun coût** | unique `(inventory_line_id, lot_id)` ; index unique partiel `(inventory_line_id, lower(lot_number)) WHERE lot_id IS NULL` ; FK composites `(tenant_id, inventory_line_id, article_id)` → `inventory_lines (tenant_id, id, article_id)` (`ON DELETE CASCADE`), `(tenant_id, article_id, lot_id)` → `stock_lots`, `(tenant_id, count_packaging_id)` → `catalog_packagings` ; `CHECK` lot attendu ⇒ `lot_id`, découvert ⇒ numéro, dates réservées aux découverts et ordonnées, quantités ≥ 0, écart cohérent, présentation complète ; RLS `ENABLE` + `FORCE` |

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
| `sales` | `tenant_id`, `number` (`String(64)`, **nul au brouillon**, `VENT-{SITE}-{ANNÉE}-{SÉQUENCE}` à la validation, historique `VTE-000001` conservé), `site_id`, `customer_id` (nullable : vente ordinaire, entièrement payée), `status` (`DRAFT` \| `VALIDATED` \| `CANCELLED`), `sale_date`, `subtotal`, `total` (`NUMERIC(18,2)`), `notes`, `created_by`, `validated_at`/`_by`, `cancelled_at`/`_by`, `cancellation_reason`, `is_credit`, `credit_override_by` / `_at` / `_reason` / `_amount` (Lot 1), `expired_lot_override_by` / `_at` / `_reason` (Lot 3-H-A, migration 0035 : dérogation à la vente d'un lot périmé) | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, id)` ; FK composites vers `sites` et `customers` ; `CHECK` montants ≥ 0, validée ⇒ `validated_at`, annulée ⇒ motif, validée ⇒ numéro (`validated_has_number`), exception de crédit complète ou absente, dérogation lot périmé complète ou absente (`expired_lot_override_complete`) ; déclencheur `sales_number_immutable` (numéro d'une vente validée définitif) ; index `(tenant_id, sale_date)` |
| `sale_lines` | `tenant_id`, `sale_id`, `line_no`, `article_id`, `quantity` (`NUMERIC(18,3)`, dans la présentation vendue), `unit_price` (copié du catalogue : article ou conditionnement), `line_total` (`NUMERIC(18,2)`) ; Lot 3-B : `packaging_id`, `packaging_name`, `packaging_conversion` (instantané figé, nuls = unité de base), `base_quantity` (`NUMERIC(18,3)`, quantité en unité de base, celle du stock) | FK composites vers la vente (`ON DELETE CASCADE`), l'article et le conditionnement ; unique `(sale_id, article_id) WHERE packaging_id IS NULL` et `(sale_id, packaging_id) WHERE packaging_id IS NOT NULL` ; `CHECK quantity > 0`, prix et montant ≥ 0, instantané complet ou absent, `base_quantity = quantity × COALESCE(packaging_conversion, 1)` |

Phase 3.0 : `channel` (`BACKOFFICE` par défaut \| `POS`, dimension de reporting) et
`idempotency_key` (`UNIQUE (tenant_id, idempotency_key)`, encaissement en une étape du POS).

Numéro : séquence `sale` de `document_sequences`. Le stock n'est jamais modifié par ces
tables : la validation passe par `StockService` (mouvements `SALE`, `source_type = 'sale'`).
Détails : [`SALES.md`](SALES.md).

### Paiements des ventes (Phase 2.7, isolés par RLS)

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `payments` | `tenant_id`, `number` (`PAY-000001`), `sale_id`, `site_id`, `amount` (`NUMERIC(18,2)`, montant imputé), `method` (type figé : `CASH` \| `MOBILE_MONEY` \| `CARD` \| `BANK_TRANSFER` \| `OTHER`), `payment_method_id` (moyen configuré, FK composite), `method_label` (libellé figé), `amount_received` / `change_given` (espèces, monnaie calculée par le serveur, `CHECK cash_change_consistent`), `provider` (historique), `status` (`PENDING` \| `COMPLETED` \| `CANCELLED`), `reference`, `paid_at`, `idempotency_key`, `created_by`, `cancelled_at`/`_by`, `cancellation_reason` | `UNIQUE (tenant_id, number)`, `UNIQUE (tenant_id, idempotency_key)` ; FK composite `(tenant_id, sale_id, site_id)` → `sales (tenant_id, id, site_id)` (même tenant et même site) et vers `sites` ; `CHECK amount > 0` ; annulé ⇒ date et motif ; index `(tenant_id, sale_id)`, `(tenant_id, paid_at)` ; jamais supprimé |

Numéro : séquence `payment`. Aucun état d'encaissement stocké sur `sales` : payé / reste /
état sont calculés à partir des paiements `COMPLETED`. Rôle applicatif : mise à jour des seules
colonnes d'annulation (Lot 1). Détails : [`PAYMENTS.md`](PAYMENTS.md).

### Moyens de paiement et caisse par site (Lot 1, isolés par RLS, [ADR-0037](../adr/0037-encaissement.md))

| Table | Colonnes principales | Contraintes notables |
|---|---|---|
| `payment_methods` | `tenant_id`, `label` (≤ 60), `kind` (type immuable), `integration_mode` (`MANUAL` \| `API` réservé), `reference_required`, `is_active`, `sort_order`, `created_by` | `UNIQUE (tenant_id, id)` (cible des paiements) ; libellé unique par entreprise (casse ignorée) ; jamais supprimé (`SELECT, INSERT`, `UPDATE` de `label`, `integration_mode`, `reference_required`, `is_active`, `sort_order`, `updated_at`) |
| `payment_method_sites` | `tenant_id`, `payment_method_id`, `site_id`, `is_enabled`, `updated_at`, `updated_by` | PK `(payment_method_id, site_id)` ; FK composites vers le moyen et le site ; ligne absente = disponible |
| `cash_site_settings` | `tenant_id`, `site_id` (PK), `enabled`, `updated_at`, `updated_by` | FK composite vers `sites` ; absente = caisse non activée ; verrou exclusif à la désactivation |

Numéros de vente : `document_sequences` (clé `{site_id}:sale:{année}`, `BIGINT`) ;
`site_has_numbers` fige le code d'un site qui a émis un numéro.

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
| sites, tenant_modules, tenant_memberships, membership_sites, membership_roles, roles, role_permissions, subscriptions, catalog_categories, suppliers, catalog_articles, customers, document_sequences, stock_* (dont transferts, emplacements, lots et réglages), sales, sale_lines, catalog_packagings, catalog_barcodes, catalog_site_articles | `tenant_isolation` : `tenant_id = app_current_tenant_id()` (lecture et écriture) |
| tenant_memberships | + `own_memberships_read` : **sans tenant actif**, l'utilisateur lit ses propres appartenances |
| tenants | `tenant_isolation` sur `id` + `member_tenants_read` (**sans tenant actif**) |
| audit_logs | lecture : tenant actif ; insertion : tenant actif ou `tenant_id` nul |

4. **Droits du rôle applicatif** (moindre privilège) :

| Droits | Tables |
|---|---|
| `SELECT` | catalogue, licenses (émises et révoquées par TechNova seule) |
| `SELECT, INSERT, UPDATE` | users, tenants, sites, tenant_modules, tenant_memberships, subscriptions, roles (jamais supprimés, ADR-0015), catalog_categories, suppliers, catalog_articles, customers, document_sequences, stock_levels, stock_exit_reasons, stock_entries, stock_exits, stock_transfers, sales |
| `SELECT, INSERT, UPDATE, DELETE` | auth_sessions, role_permissions, membership_sites, membership_roles, stock_entry_lines, stock_exit_lines, stock_transfer_lines, sale_lines (lignes de brouillon), stock_exit_line_lots (Lot 3-H-A : choix de lots d'une sortie brouillon), stock_transfer_line_lots (Lot 3-H-B1 : choix de lots d'un transfert brouillon), inventory_line_lots (Lot 3-H : comptage par lot d'un inventaire ouvert) |
| `SELECT, INSERT` | audit_logs, stock_movements (append-only), subscription_payments (décision : TechNova seule), catalog_packagings (+ `UPDATE (name, conversion, sale_price, is_active, updated_at)` ; jamais supprimés, Lot 3-B) |
| `SELECT, INSERT, DELETE` + `UPDATE (is_active, updated_at)` | catalog_barcodes (Lot 3-D : retrait d'un code audité ; code et porteur jamais modifiés) |
| `SELECT, INSERT` + `UPDATE (name, is_active, updated_at)` | stock_locations (Lot 3-F : jamais supprimés) |
| `SELECT, INSERT, UPDATE` | catalog_site_articles (Recette, étape 1 : retrait = désactivation, jamais `DELETE`) |
| `SELECT, INSERT, DELETE` + `UPDATE (location_id, updated_at)` | stock_article_locations (Lot 3-F : retrait = ligne supprimée, audité) |
| `SELECT, INSERT` | stock_lots (Lot 3-G : lots figés, jamais supprimés) |
| `SELECT, INSERT` + `UPDATE (quantity, updated_at)` | stock_lot_levels (Lot 3-G : soldes tenus par `StockService`) |
| `SELECT, INSERT` + `UPDATE (expiry_warning_days, updated_at)` | stock_settings (Lot 3-G) |

Pas de `DELETE` sur tenants ni subscriptions : l'expiration ne supprime jamais de données.

5. **Droits du rôle de la console TechNova** (`stockmanager_platform`, sans `BYPASSRLS`,
   ADR-0031) : `SELECT` sur le catalogue ; `UPDATE` des seules colonnes commerciales de
   `plans` ; `SELECT` sur `users` (comptes TechNova seulement, RLS) et `UPDATE` des colonnes de
   verrouillage ; `platform_sessions` et `platform_audit_logs` ci-dessus. Depuis 3.2-G
   (migration 0018, politiques RLS `TO` ce rôle) : `tenants` — lecture de `id`, `name`,
   `trade_name`, `slug`, `status`, `business_profile_code`, `country_code`, `currency`,
   `locale`, `timezone`, `created_at`, `updated_at`, mise à jour de `status` seul ;
   `subscriptions` — lecture (depuis 0020 : un abonnement par site), mise à jour de `plan_code`, `status`, `current_period_start`,
   `current_period_end`, `price_at_subscription`, `currency_at_subscription` ; `sites` —
   lecture de `tenant_id`, `is_active` ; `tenant_memberships` — lecture de `tenant_id`,
   `status` (compteurs) ; `audit_logs` — **insertion seule** d'entrées miroir (politique
   permissive `platform_mirror_insert` et restrictive `platform_mirror_only` : tenant
   renseigné, `user_id` nul). **Aucun droit** de suppression, aucune lecture d'`audit_logs`,
   des coordonnées des entreprises, des utilisateurs, ni d'aucune table métier. Depuis 3.3-A
   (migration 0019) : `subscription_payments` — lecture, mise à jour des seules colonnes de
   décision d'une ligne `PENDING` (ADR-0032). Depuis 3.3-B1 (migration 0020) : `sites` — lecture de
   `id`, `name`, `code` ; `tenant_memberships` — lecture de `id`, `is_owner`, `all_sites` ;
   `membership_sites` — lecture (compteurs d'utilisateurs par site, ADR-0033). Depuis 3.3-B2
   (migration 0021) : `licenses` — lecture, émission (`ISSUED`), révocation seulement ;
   séquence `license_number_seq` (ADR-0034). Depuis 3.3-B3 (migration 0022) :
   `license_activations` — lecture, libération d'un poste actif seulement (ADR-0035). Depuis
   3.3-B4 (migration 0023) : `notifications` — lecture et insertion (job des rappels, ADR-0036).

## Ajouter une table tenant-scoped (règle pour les modules futurs)

1. Modèle avec `TenantScopedMixin` (colonne `tenant_id` + filtre ORM automatique).
2. Dans la migration : `ENABLE` + `FORCE ROW LEVEL SECURITY`, politique `tenant_isolation`,
   droits minimaux pour le rôle applicatif.
3. Clés étrangères composites `(tenant_id, …)` vers les autres tables du tenant.
4. Tests d'isolation (SQL et API).
