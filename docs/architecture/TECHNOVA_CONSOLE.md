# Console d'administration TechNova (Phases 3.2-F, 3.2-G et 3.3-A)

Décisions : [ADR-0031](../adr/0031-console-technova.md) ; paiements :
[ADR-0032](../adr/0032-paiements-abonnement.md). Ce document décrit le périmètre livré,
l'exploitation et ce qui relève de l'infrastructure ou des phases suivantes.

```text
                    TECHNOVA
                       │
              Platform Admin (users.is_platform_admin, CLI uniquement)
                       │
             ┌─────────▼─────────┐
             │ TechNova Console  │  processus app.console.main, rôle SQL stockmanager_platform
             └─────────┬─────────┘
     ┌────────────┬────────┴─────┬──────────────┐
 Plans/Tarifs     Tenants    Abonnements    Catalogue · Audit (plateforme)
     │           (métadonnées) │ activation       │
     ▼                          │ transitoire      ▼
 DB commerciale                 ▼            TOML / code : LECTURE SEULE
     │              Paiements (3.3-A) · Licences → 3.3-B
     ▼
 GET /public/plans · inscription · prix figé des souscriptions

 Données métier des entreprises (ventes, stock, clients…) : AUCUN accès TechNova.
```

## 1. TechNova ≠ tenant

| | Utilisateur d'entreprise | Administrateur TechNova |
|---|---|---|
| Identité | `User` global | `User` global, `is_platform_admin = true`, compte **dédié** |
| Accès | `TenantMembership` (rôles, sites) | aucune appartenance ; console uniquement |
| Création | inscription, `/members`, CLI `create-tenant` | CLI `stockmanager platform-admin create` |
| Application | API des entreprises (`/api/v1`) | console (`/platform-api/v1`), processus distinct |
| Rôle SQL | `stockmanager_app` | `stockmanager_platform` |
| Visibilité mutuelle | ne voit pas les comptes TechNova (RLS) | ne voit que les comptes TechNova (RLS) |

Un administrateur d'entreprise (Administrateur, Gestionnaire, Vendeur, Consultant, rôle
personnalisé, propriétaire) n'a **aucun** chemin vers la console : ses identifiants y sont
refusés (`invalid_credentials`), son jeton n'y est pas reconnu, et aucune API ne permet de
modifier `is_platform_admin` (`extra="forbid"` et droits par colonne en base).

## 2. Exploitation

```bash
# Rôle SQL de la console (une fois ; automatique au premier démarrage du conteneur db)
PGHOST=… PGPASSWORD=… POSTGRES_USER=stockmanager POSTGRES_DB=stockmanager \
  POSTGRES_PLATFORM_PASSWORD=… ./docker/postgres/init/02-platform-role.sh
uv run alembic upgrade head                           # migration 0017 (vérifie le rôle)

# Administrateurs TechNova (rôle propriétaire ; audités dans le journal de la plateforme)
uv run stockmanager platform-admin create --email ops@technova.example --name "Ops TechNova"
#   mot de passe : saisie masquée, SM_PLATFORM_ADMIN_PASSWORD ou --password-stdin
uv run stockmanager platform-admin list
uv run stockmanager platform-admin revoke --email ops@technova.example  # compte fermé

# Console (processus distinct)
SM_PLATFORM_DATABASE_URL=postgresql+psycopg://stockmanager_platform:…@…/stockmanager \
  uv run uvicorn app.console.main:app --port 8001
```

Interface : `/tech-admin` (application React distincte, chargée à la demande ; Vite relaie
`/platform-api` vers le port 8001, `VITE_PLATFORM_API_PROXY_TARGET`).

Variables : `SM_PLATFORM_DATABASE_URL`, `SM_DB_PLATFORM_ROLE`, `SM_PLATFORM_API_PREFIX`,
`SM_PLATFORM_COOKIE_NAME`, `SM_PLATFORM_COOKIE_SECURE`, `SM_PLATFORM_SESSION_TTL_MINUTES`
(480), `SM_PLATFORM_SESSION_IDLE_MINUTES` (30).

## 3. API de la console (`/platform-api/v1`)

| Méthode | Chemin | Rôle |
|---|---|---|
| POST | `/auth/login` | connexion (cookie `HttpOnly`, `SameSite=Strict`) |
| POST | `/auth/logout` | déconnexion (session révoquée) |
| GET | `/me` | administrateur connecté |
| GET | `/dashboard` | indicateurs des offres, du catalogue, et agrégats des entreprises et abonnements (comptages en base) |
| GET | `/plans`, `/plans/{code}` | paramètres commerciaux + structure technique (lecture seule) |
| PATCH | `/plans/{code}/commercial` | paramètres commerciaux ; `reason` obligatoire |
| GET | `/catalog` | modules, permissions, fonctionnalités, limites, profils, rôles de base, politiques, devises |
| GET | `/audit` | journal de la plateforme (paginé ; filtres `action`, `target_type`, `target_id`, `tenant_id`) |
| GET | `/tenants` | entreprises : métadonnées paginées (`limit`, `offset`, `sort` : `name`, `created_at`, `current_period_end`, `status`) ; filtres `search`, `status`, `plan_code`, `subscription_status` (statut **effectif**) |
| GET | `/tenants/{id}` | identité plateforme, utilisation (sites, utilisateurs / limites du plan), abonnement (plan, statuts stocké et effectif, période, prix figé), actions possibles et propositions de dates |
| POST | `/tenants/{id}/suspend` · `/reactivate` | statut de l'entreprise ; `reason` obligatoire |
| POST | `/tenants/{id}/subscription/activate` | activation manuelle transitoire (`period_start`, `period_end` facultatifs, `reason`) |
| POST | `/tenants/{id}/subscription/extend` | prolongation (`period_end`, `reason`) |
| POST | `/tenants/{id}/subscription/change-plan` | changement de plan (`plan_code`, `reason`) |
| GET | `/payments` | paiements d'abonnement paginés (tri `created_at` décroissant par défaut, `amount`, `status`, `decided_at`) ; filtres `status`, `tenant_id`, `search` (référence) |
| GET | `/payments/{id}` | détail : entreprise, plan, montant, devise, période, moyen, référence, statut, décision |
| POST | `/payments/{id}/confirm` · `/reject` | décision définitive d'un paiement `PENDING` (`reason` ; motif du rejet visible par l'entreprise) |

Toute requête modifiante exige l'en-tête `X-TechNova-Console: 1` (`403
console_header_required` sinon). Aucune route n'existe pour créer ou promouvoir un
administrateur, ni pour lire une donnée métier d'une entreprise (test automatisé sur le schéma
OpenAPI). Un compte ou un jeton d'entreprise reçoit `401` ; une entreprise inconnue, `404
tenant_not_found`.

## 4. Offres & tarifs

Champs modifiables (noms du modèle `Plan`) : `listed`, `monthly_price`,
`monthly_price_enabled`, `annual_price`, `annual_price_enabled`, `currency`,
`price_display_enabled`, `contact_required`, `commercial_description`, `display_order`,
`trial_days`. Seuls les champs envoyés sont modifiés.

Validations **serveur** (codes d'erreur) : montant ≥ 0 à 2 décimales et essai 0–365, ordre
0–9999, description ≤ 2000 (`validation_error`) ; devise hors référentiel des pays
(`unknown_currency`) ; période proposée sans prix (`price_required`) ; prix sans devise
(`currency_required`) ; prix affiché sans période proposée (`price_display_without_period`) ;
plan publié sans période ni contact (`plan_not_subscribable`) ; plan retiré du catalogue
publié (`plan_inactive`) ; aucune modification (`no_changes`) ; raison vide ou blanche
(`validation_error`). L'interface signale les mêmes erreurs mais n'est jamais la barrière.

Interface : champs groupés (Publication, Tarification, Présentation commerciale, Essai),
raison obligatoire, **confirmation** récapitulant chaque changement (« Prix annuel :
120 000 F CFA → 150 000 F CFA »), historique du plan (journal filtré). `trial_days = 0` :
aucun essai, jamais de durée par défaut. Aucun plan `DEMO` n'existe : un prix nul ne
signifie que « gratuit » pour la période proposée.

Effets : `GET /public/plans` (plans actifs et publiés ; prix seulement si affichés ; périodes
ouvertes seulement ; `self_service` = règle de l'inscription) et l'inscription lisent ces
valeurs immédiatement. `catalog sync` ne les modifie jamais (test). Une souscription garde le
prix et la devise figés à sa création (test).

## 5. Audit de la plateforme

`platform_audit_logs` : append-only (droits + déclencheur), distinct de `audit_logs`. Chaque
modification d'un plan enregistre qui (identifiant et e-mail figé), quoi (`plan.published`,
`plan.unpublished`, `plan.commercial.updated`), quand, sur quel plan, **avant**, **après** (seuls
les champs modifiés, montants en chaînes) et **pourquoi** (raison). Également :
`auth.login.succeeded|failed`, `auth.logout`, `platform_admin.created|revoked` (auteur
`cli:<compte système>`). Aucune entrée n'est écrite dans le journal d'un tenant pour une
modification générale d'un plan ; la colonne `tenant_id` prépare le double audit des
opérations sur un tenant (3.2-G).

## 6. Tenants et abonnements (Phase 3.2-G)

**Tenant ≠ abonnement.** Le statut de l'entreprise (`active` / `suspended`) et celui de son
abonnement sont indépendants : `Tenant = active` avec `Subscription = expired` (consultation,
export, renouvellement selon la politique d'abonnement) est différent de
`Tenant = suspended` avec `Subscription = active` (plus aucun accès des utilisateurs de
l'entreprise, `403 tenant_suspended`). Aucune donnée n'est jamais supprimée.

| Action | Condition | Effet | Audit |
|---|---|---|---|
| Suspendre | entreprise active | `tenants.status = suspended` | `tenant.suspended` |
| Réactiver | entreprise suspendue | `tenants.status = active` | `tenant.reactivated` |
| Activer (transitoire) | abonnement `pending_activation` ou `trial` | `active`, période choisie (24 mois au plus) ; **aucun paiement** | `subscription.manually_activated` |
| Prolonger | abonnement `active` / `past_due` / `expired` | nouvelle échéance postérieure (période échue : repart du jour) | `subscription.extended` |
| Changer de plan | plan actif différent | plan + prix / devise figés au tarif actuel du nouveau plan ; période inchangée | `subscription.plan_changed` |

Chaque action : raison obligatoire, confirmation explicite (récapitulatif + case « Je
confirme » + bouton), verrou de la ligne, état compatible exigé (une action rejouée répond
`409` : `tenant_already_suspended`, `tenant_not_suspended`, `subscription_not_activable`,
`subscription_not_extendable` ; dates : `422 invalid_period`, `period_too_long` ; plan :
`plan_unchanged`, `unknown_plan`). Les dates sont des jours du fuseau de l'entreprise
(ADR-0028) ; l'échéance est le début (minuit local) du jour indiqué.

**Double audit** (même transaction) : `platform_audit_logs` (auteur TechNova, tenant, cible,
avant, après, raison) et entrée miroir dans `audit_logs` de l'entreprise (même action,
`user_id` nul, `actor = "technova"`, raison, avant / après, identifiant de l'entrée
plateforme ; jamais l'identité de l'agent), visible dans son journal d'audit.

Tableau de bord : entreprises (total, actives, suspendues) et abonnements par statut effectif
(actifs, en essai, en attente d'activation, échéance dépassée, expirés, à renouveler sous 30
jours), calculés par agrégats SQL.

Droits SQL ajoutés (migration 0018) : voir [`DATA_MODEL.md`](DATA_MODEL.md) ; aucune table
métier.

## 7. Paiements d'abonnement (Phase 3.3-A)

```text
PLAN → SUBSCRIPTION → PAYMENT (3.3-A) → LICENCE (3.3-B) → ACTIVATION
                        │
   entreprise : déclare (PENDING) ── TechNova : CONFIRMED | REJECTED (définitif)
```

**Payment CONFIRMED ≠ activation.** Confirmer un paiement atteste seulement que TechNova l'a
reçu : l'abonnement et l'entreprise ne changent pas (statut, période, plan). L'activation
viendra de l'import d'une licence signée (3.3-B), qui référencera un paiement confirmé ; en
attendant, l'activation manuelle transitoire (§ 6) reste l'outil de TechNova. Aucune clé,
aucune signature, aucune licence en 3.3-A.

| Étape | Qui | Règles |
|---|---|---|
| Déclaration | entreprise (`subscription.payment.declare`, nature `billing`) | montant, période (≤ 24 mois), moyen, référence ; devise fixée par le serveur ; idempotente ; possible abonnement en attente d'activation ou expiré ; aucun champ de décision accepté |
| Confirmation | TechNova | paiement `PENDING`, verrou de la ligne, raison obligatoire, `decided_by` / `decided_at`, double audit `subscription_payment.confirmed` |
| Rejet | TechNova | idem, raison = **motif visible par l'entreprise**, double audit `subscription_payment.rejected` |

Décision **définitive** : `PENDING → CONFIRMED` ou `PENDING → REJECTED`, aucune autre
transition (service, politique RLS `platform_decide`, contraintes et déclencheur
`subscription_payments_final`). Deux administrateurs qui décident en même temps : une seule
décision réussit, l'autre reçoit `409 payment_already_decided`. Après un rejet, l'entreprise
déclare un nouveau paiement.

Interface : entrée « Paiements » (liste filtrable, actualisation), fiche du paiement
(récapitulatif, décision, historique), actions visibles seulement si `PENDING`, confirmation
explicite (raison + case « Je confirme »), un seul envoi à la fois, `409` affiché puis fiche
relue ; depuis la fiche d'une entreprise, « Voir les paiements ». Côté entreprise : section
« Paiements de l'abonnement » de la page Abonnement (historique, filtre de statut, motif de
rejet, formulaire de déclaration si la permission est détenue).

La console voit le nom de l'entreprise et le plan, jamais le déclarant (utilisateur de
l'entreprise) ; le journal de l'entreprise ne contient jamais l'identité de l'agent TechNova.
Droits SQL (migration 0019) : voir [`DATA_MODEL.md`](DATA_MODEL.md).

## 8. Ce qui est implémenté, prévu côté infrastructure, futur

| Sujet | Statut |
|---|---|
| Processus, rôle SQL, droits minimaux, RLS des comptes, cookie de session, anti-CSRF, verrouillage, audit append-only | **Implémenté** (code, migration 0017, tests) |
| Restriction réseau (VPN, liste d'adresses, reverse proxy dédié à la console) | **Infrastructure** : non réalisée par le dépôt ; Compose publie le port 8001 sur `127.0.0.1` seulement |
| TLS, `SM_PLATFORM_COOKIE_SECURE=true` | **Infrastructure / configuration** de production |
| MFA des administrateurs TechNova | **Futur** |
| Tenants (liste, détail, suspension, réactivation), abonnements (changement de plan, activation manuelle transitoire, prolongation), double audit | **Implémenté** (3.2-G, migration 0018, tests) |
| Paiements d'abonnement (déclaration par l'entreprise, confirmation / rejet TechNova, double audit, aucune activation) | **Implémenté** (3.3-A, migration 0019, tests, ADR-0032) |
| Licences (outil de signature séparé, clé privée hors StockManager, import `.lic`) | **3.3-B** |
| Catalogue technique éditable, limites modifiables, paramètres SaaS en base, support avec accès aux données métier | **Futur / réévaluation** (hors console en 3.2-F) |
