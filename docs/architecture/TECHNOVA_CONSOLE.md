# Console d'administration TechNova (Phase 3.2-F)

Décisions : [ADR-0031](../adr/0031-console-technova.md). Ce document décrit le périmètre livré,
l'exploitation et ce qui relève de l'infrastructure ou des phases suivantes.

```text
                    TECHNOVA
                       │
              Platform Admin (users.is_platform_admin, CLI uniquement)
                       │
             ┌─────────▼─────────┐
             │ TechNova Console  │  processus app.console.main, rôle SQL stockmanager_platform
             └─────────┬─────────┘
          ┌────────────┼────────────┐
      Plans/Tarifs   Audit      Catalogue
          │        (plateforme)     │
          ▼                    TOML / code : LECTURE SEULE
   DB commerciale (plans.*)
          │
          ▼
   GET /public/plans · inscription · prix figé des souscriptions
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
| GET | `/dashboard` | indicateurs des offres et du catalogue (aucune donnée de tenant) |
| GET | `/plans`, `/plans/{code}` | paramètres commerciaux + structure technique (lecture seule) |
| PATCH | `/plans/{code}/commercial` | paramètres commerciaux ; `reason` obligatoire |
| GET | `/catalog` | modules, permissions, fonctionnalités, limites, profils, rôles de base, politiques, devises |
| GET | `/audit` | journal de la plateforme (paginé ; filtres `action`, `target_type`, `target_id`) |

Toute requête modifiante exige l'en-tête `X-TechNova-Console: 1` (`403
console_header_required` sinon). Aucune route n'existe pour créer ou promouvoir un
administrateur, ni pour lire un tenant (test automatisé sur le schéma OpenAPI).

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

## 6. Ce qui est implémenté, prévu côté infrastructure, futur

| Sujet | Statut |
|---|---|
| Processus, rôle SQL, droits minimaux, RLS des comptes, cookie de session, anti-CSRF, verrouillage, audit append-only | **Implémenté** (code, migration 0017, tests) |
| Restriction réseau (VPN, liste d'adresses, reverse proxy dédié à la console) | **Infrastructure** : non réalisée par le dépôt ; Compose publie le port 8001 sur `127.0.0.1` seulement |
| TLS, `SM_PLATFORM_COOKIE_SECURE=true` | **Infrastructure / configuration** de production |
| MFA des administrateurs TechNova | **Futur** |
| Tenants (liste, détail, suspension, réactivation), abonnements (changement de plan, activation, prolongation), double audit | **3.2-G** |
| Paiements (déclaration, confirmation TechNova) | **3.3-A** |
| Licences (outil de signature séparé, clé privée hors StockManager, import `.lic`) | **3.3-B** |
| Catalogue technique éditable, limites modifiables, paramètres SaaS en base, support avec accès aux données métier | **Futur / réévaluation** (hors console en 3.2-F) |
