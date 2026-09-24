# StockManager Web — Architecture

> **Statut : architecture validée (Phase 0) — socle plateforme implémenté (Phase 1).**
> Éditeur : TechNova · Produit : StockManager · Projet : GESTOCK_WEB
> Décisions : [`docs/adr/`](../adr/README.md) · Modèle de données : [`DATA_MODEL.md`](DATA_MODEL.md)
> · API : [`API.md`](API.md)

---

## 1. Contexte et objectifs

StockManager Web est une plateforme **SaaS multi-tenant** de gestion commerciale
polyvalente (alimentation, commerce général, quincaillerie, vêtements, cosmétique,
électronique, pièces détachées, librairie, restaurants, maquis, cafés, bars,
fast-food, puis d'autres secteurs).

Principe fondateur : **un Core commun + des profils d'activité + des modules spécialisés**.
On ne construit pas une application par secteur. Ajouter un secteur doit se faire
par **configuration et ajout de modules**, sans reconstruire le Core.

Le projet Desktop historique (`GESTOK_ENTREP`) est une **référence fonctionnelle et
métier uniquement**. Son architecture technique n'est pas reprise.

## 2. Vue d'ensemble

```text
┌──────────────────┐  ┌────────────────────┐  ┌──────────────────────┐
│  React Web (SPA) │  │ Flutter Mobile     │  │ Customer Mobile Web  │
│  back-office/POS │  │ gérant (V2.5)      │  │ menu QR (V2)         │
└────────┬─────────┘  └─────────┬──────────┘  └──────────┬───────────┘
         │ REST /api/v1         │ REST /api/v1           │ REST /api/v1/public
         └──────────────────────┼────────────────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  FastAPI — monolithe modulaire│
                 │  core │ platform │ modules    │
                 └──────────────┬───────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  PostgreSQL (RLS par tenant) │
                 └──────────────────────────────┘
```

- **Un seul backend** (monolithe modulaire, [ADR-0001](../adr/0001-monolithe-modulaire.md))
  sert tous les clients. Toute règle métier et toute décision d'autorisation y vivent.
- **Les clients sont des vues** : ils affichent ce que le backend autorise et
  n'appliquent aucune règle métier faisant foi.

## 3. Principes directeurs (non négociables)

1. **Le backend est la seule frontière de sécurité.** Le frontend masque des
   éléments pour l'ergonomie, jamais pour la sécurité.
2. **Isolation des tenants à chaque requête**, en profondeur (contexte applicatif +
   filtrage des requêtes + Row-Level Security PostgreSQL).
3. **Pas de `if business_type == "restaurant"`.** Le comportement dépend de
   *capacités* résolues (modules actifs + permissions), jamais du nom d'un secteur.
4. **Le stock ne change que via le service de stock central**, qui produit un
   mouvement traçable pour chaque variation et refuse tout stock négatif.
5. **Montants et quantités en `Decimal`** (jamais `float`), de la base à l'API.
6. **Opérations critiques idempotentes** : une vente validée n'est jamais appliquée deux fois.
7. **Tout ce qui est sensible est audité.**
8. **Séparation stricte frontend / backend**, contrat = API REST documentée (OpenAPI).

## 4. Multi-tenant et multi-sites

### 4.1 Hiérarchie

```text
Plateforme (TechNova)
└── Tenant (entreprise cliente)          ← abonnement, profil d'activité, modules
    ├── Sites (boutique, dépôt, restaurant…)
    │   ├── stocks, ventes, caisses, inventaires  (données "site-scoped")
    │   └── accès utilisateurs par site
    ├── Données partagées du tenant (catalogue, clients, fournisseurs…)
    └── Utilisateurs, rôles, permissions
```

### 4.2 Stratégie d'isolation — [ADR-0002](../adr/0002-strategie-multi-tenant.md)

- Base PostgreSQL **partagée**, schéma partagé, colonne **`tenant_id` obligatoire**
  sur toute table appartenant à un tenant ; `site_id` sur les données propres à un site.
- **Trois couches de défense** :
  1. **Contexte de requête** : le `tenant_id` provient **exclusivement** de l'identité
     authentifiée (jeton), jamais d'un paramètre fourni par le client.
  2. **Couche d'accès aux données** : les dépôts (repositories) filtrent
     systématiquement par `tenant_id` ; aucune requête tenant-scoped ne s'écrit à la main
     sans passer par ce mécanisme.
  3. **Row-Level Security PostgreSQL** : chaque transaction exécute
     `set_config('app.tenant_id', …, true)` ; les politiques RLS rejettent toute ligne
     d'un autre tenant. Le rôle SQL applicatif n'est ni propriétaire des tables ni
     `BYPASSRLS` (les migrations utilisent un rôle distinct).
- Les identifiants de ressources sont des UUID (non énumérables).

**Mise en œuvre (Phase 1)** — détail dans [`DATA_MODEL.md`](DATA_MODEL.md) :

| Couche | Mécanisme |
|---|---|
| Contexte | `app/platform/context.py` : le `tenant_id` vient du jeton ; `set_db_context()` le pose sur la session ; un évènement `after_begin` l'applique à **chaque** transaction (`app/core/db.py`). |
| ORM | Marqueur `TenantFiltered` : un hook `do_orm_execute` ajoute `WHERE tenant_id = <tenant actif>` à toute requête ORM sur ces entités. |
| Base | RLS `ENABLE` + `FORCE` sur 10 tables ; fonctions `app_current_tenant_id()` / `app_current_user_id()` ; lecture de ses propres appartenances **uniquement sans tenant actif** (écran de choix). |
| Droits | Rôle `stockmanager_app` : `SELECT` sur le catalogue, pas de `DELETE` sur tenants/abonnements, audit en `SELECT, INSERT` seulement. |

Chaque couche est testée isolément (tests SQL directs sous le rôle applicatif, test du
filtre ORM avec une connexion qui ignore la RLS, tests API inter-tenants).

### 4.3 Chaîne de contrôle d'une requête

```text
User ──► Tenant ──► Site ──► Permission ──► Resource
 │         │          │           │              │
 │ jeton   │ depuis   │ en-tête   │ rôle(s) de   │ la ressource appartient
 │ valide  │ le jeton │ X-Site-Id │ l'utilisateur│ au tenant (RLS) et au
 │ actif   │ + actif  │ vérifié   │ sur ce site  │ site autorisé
```

Chaque étape est une **dépendance FastAPI** composable (`app/platform/context.py`) :
`CurrentUser` (jeton, session non révoquée, compte actif) → `ActiveUser` (mot de passe à
jour) → `TenantContext` (tenant du jeton, appartenance active, tenant actif, site
`X-Site-Id` accessible, capacités résolues) → `require_permission("…")` /
`require_module("…")`.
Un échec à n'importe quelle étape interrompt la requête (401/403/404).
Une ressource d'un autre tenant répond **404** (on ne révèle pas son existence).

### 4.4 Sites

- Un utilisateur (identité globale) appartient à un ou plusieurs tenants via
  `tenant_memberships` ([ADR-0007](../adr/0007-utilisateurs-memberships-mot-de-passe-provisoire.md)).
- `all_sites` ou `membership_sites` déterminent sur quels sites il peut agir.
- Les rôles (`membership_roles`) sont attribués **globalement au tenant** (`site_id` nul)
  ou **limités à un site** (appliqués seulement quand ce site est actif).
- Le site actif est transmis par l'en-tête `X-Site-Id` et **revalidé côté serveur**.
- La **consolidation** (rapports multi-sites) exige une permission dédiée
  (ex. `reports.consolidated.view`).
- Les **transferts inter-sites** produisent deux mouvements de stock liés
  (sortie site A, entrée site B), avec un état « en transit » si nécessaire.

## 5. Modularité : Core, modules, profils, plans

### 5.1 Vocabulaire

| Concept | Rôle | Exemple |
|---|---|---|
| **Module** | Unité fonctionnelle déployable, avec code, dépendances, permissions, navigation | `catalog`, `stock`, `pos`, `restaurant.tables` |
| **Business Profile** | Configuration déclarative d'un secteur : modules proposés, terminologie, préréglages | `alimentation`, `quincaillerie`, `restaurant` |
| **Subscription Plan** | Ce que le client a acheté : modules autorisés, limites | `STANDARD`, `ENTREPRISE` (mensuel / annuel) |
| **Activated Modules** | Modules effectivement activés par le tenant (dans les limites ci-dessus) | un maquis qui n'active pas `restaurant.qr` |
| **Permission** | Droit d'un utilisateur sur une action | `sales.sale.validate` |

### 5.2 Résolution des capacités — [ADR-0003](../adr/0003-resolution-des-capacites.md)

```text
modules_effectifs(tenant) = profil.modules_disponibles
                          ∩ plan.modules_autorisés
                          ∩ tenant.modules_activés
                          (+ fermeture sur les dépendances ; module requis absent ⇒ module inactif)

permissions_effectives(user, site) = permissions des rôles de l'utilisateur (tenant + site)
                                   ∩ permissions déclarées par les modules_effectifs
```

Ce calcul est fait **par le backend uniquement**, dans un service unique
(`app/platform/capabilities/service.py`). Les modules **core** (`dashboard`, `organization`,
`users`, `audit`, `subscription`) sont toujours actifs. Les permissions sont ensuite
filtrées par la politique d'abonnement ([ADR-0011](../adr/0011-politique-abonnement.md)) ;
celles qui sont bloquées sont exposées dans `restricted_permissions`. Il sert à deux choses :

1. **Application** : chaque routeur de module est protégé par `require_module("pos")`
   et chaque endpoint par `require_permission(...)`. Un module inactif répond 403/404
   même si l'appel est forgé à la main.
2. **Exposition** : `GET /api/v1/me/capabilities` renvoie au client :

```json
{
  "tenant": { "id": "…", "name": "Entreprise ABC" },
  "profile": { "code": "restaurant", "name": "Restaurant / Maquis / Café / Bar / Fast-food" },
  "plan": { "code": "ENTREPRISE", "billing_period": "monthly", "status": "active" },
  "site": { "id": "…", "name": "Maquis Ouaga 2000" },
  "modules": ["dashboard", "catalog", "stock", "pos", "restaurant.tables", "restaurant.kitchen"],
  "permissions": ["organization.site.view", "users.member.manage", "…"],
  "restricted_permissions": [],
  "navigation": ["dashboard", "restaurant.tables", "restaurant.orders", "restaurant.kitchen", "pos", "…"],
  "terminology": { "fr": { "catalog": { "item": "Produit", "items": "Produits" } } }
}
```

### 5.3 Manifeste de module (backend)

Chaque module déclare un manifeste ; un **registre** les collecte au démarrage
(pas de découverte « magique » : liste explicite).

```python
# Forme réelle (app/platform/registry.py). Exemple pour un futur module :
ModuleManifest(
    code="stock",
    depends_on=("catalog",),
    permissions=(
        PermissionDef("stock.level.view", AccessKind.READ),
        PermissionDef("stock.movement.create", AccessKind.WRITE),
        PermissionDef("stock.report.export", AccessKind.EXPORT),
    ),
    router=router,  # monté sous /api/v1/stock, protégé automatiquement par require_module
)
```

Le registre (`get_registry()`) regroupe les modules du socle (`app/platform/manifests.py`)
et les modules métier (`app/modules/`). Il est validé au démarrage : dépendances connues,
absence de cycle, permissions préfixées par le code du module. Les modules métier non encore
réalisés sont **déclarés sans implémentation** (statut `planned`, `app/modules/planned.py`)
pour que profils et plans puissent les référencer ; ils ne sont ni routés ni affichés.
Modules réalisés : `catalog`, `suppliers` (2.1), `stock`, `alerts` (2.2).

Règles de dépendance :

- un module dépend du Core/de la plateforme et des modules **déclarés** dans `depends_on` ;
- un module n'importe jamais les modèles internes d'un autre : il passe par son
  **service public** (`modules/<m>/service.py` ou interface dédiée) ;
- pas de dépendance circulaire (contrôle automatisé prévu, ex. `import-linter`).

### 5.4 Business Profiles

Les profils sont des **données** : un fichier TOML par profil dans
`backend/app/platform/catalog/data/profiles/`, validé contre le registre puis synchronisé en
base (`stockmanager catalog sync`). Profils livrés : `alimentation`, `commerce_general`,
`quincaillerie`, `restaurant`.

```toml
# backend/app/platform/catalog/data/profiles/restaurant.toml (extrait)
code = "restaurant"
name = "Restaurant / Maquis / Café / Bar / Fast-food"
modules = ["catalog", "stock", "sales", "payments", "cash_register", "pos",
           "restaurant.menu", "restaurant.tables", "restaurant.orders",
           "restaurant.kitchen", "restaurant.recipes", "..."]
optional_modules = ["restaurant.qr"]   # proposé, désactivé à la création
navigation = ["dashboard", "restaurant.tables", "restaurant.orders", "restaurant.kitchen",
              "pos", "restaurant.menu", "..."]

[terminology.fr.catalog]
item = "Produit"
items = "Produits"
```

La validation refuse un module inconnu, une dépendance non proposée ou une entrée de
navigation hors profil.

**Ajouter un secteur** = ajouter un profil (et, si besoin, un nouveau module).
Aucune modification du Core ni de conditions dispersées.

### 5.5 Plans d'abonnement

- Offres : **STANDARD** et **ENTREPRISE**, facturation **mensuelle** ou **annuelle**.
  Pas de licence perpétuelle.
- Structure ([ADR-0012](../adr/0012-politiques-de-plan.md)) : **Plan → limites → modules →
  fonctionnalités → politiques**, entièrement en données (`plans.toml`). Les modules
  déclarent les limites qu'ils comptent (`LimitDef`) et leurs fonctionnalités optionnelles ;
  `PlanPolicy` est le seul point d'application (`ensure_capacity`, `require_feature`).
- Valeurs validées : STANDARD = 1 site, 5 utilisateurs, sans `restaurant.qr` ;
  ENTREPRISE = illimité.
- L'abonnement a un statut (`trial`, `active`, `past_due`, `expired`, `suspended`,
  `cancelled`) ; le statut effectif est calculé à la lecture.
- Une **politique centrale** (`subscription_policies.toml`) indique, par statut, les
  natures de permissions autorisées (`read`, `write`, `export`, `admin`, `billing`).
  Voir [ADR-0011](../adr/0011-politique-abonnement.md).

### 5.6 Feature flags

Distincts des modules : bascules fines (ex. `pos.allow_partial_payment`), portées
par le profil (valeur par défaut), le plan (autorisé ou non) et le tenant
(paramètre). Le mécanisme « fonctionnalité de plan » existe (déclaration dans le manifeste,
`features` du plan, `require_feature`) ; aucune fonctionnalité n'est encore déclarée.

### 5.7 Workflows

Les cycles de vie (vente, commande restaurant, inventaire, transfert) sont modélisés
comme des **machines à états explicites** côté backend (états + transitions
autorisées + permission requise par transition). Exemple futur, cuisine :

```text
nouvelle → en_attente → en_preparation → prete → servie
```

Une transition invalide est refusée par le backend, quel que soit le client.

## 6. Sécurité

- **Authentification** ([ADR-0010](../adr/0010-authentification-et-tenant-actif.md)) :
  jeton d'accès JWT court (15 min) **lié au tenant actif** + jeton de rafraîchissement
  opaque, stocké haché, rotatif (fenêtre de grâce multi-onglets, détection de
  réutilisation) et révocable (cookie `HttpOnly; Secure; SameSite=Strict`). Mots de passe
  en **Argon2id**, verrouillage après échecs, changement obligatoire du mot de passe
  provisoire.
- **Provisioning** ([ADR-0006](../adr/0006-provisioning-des-tenants.md)) :
  `TenantProvisioningService`, utilisé par la CLI `stockmanager create-tenant`, sans
  `BYPASSRLS`.
- **Autorisation** : RBAC ; permissions nommées `module.ressource.action`, déclarées
  par les manifestes avec leur nature ; rôles définis **par tenant**. Le propriétaire est
  un attribut de l'appartenance (toutes les permissions des modules actifs). Modèles de
  rôles système (`role_templates.toml`) : Administrateur, Consultation — les rôles métier
  (Gérant, Caissier, Magasinier, Serveur, Cuisine…) viendront avec leurs modules.
  **Anti-escalade** : un non-propriétaire ne peut accorder que ce qu'il détient.
- **Administration plateforme** (TechNova) : espace et identités séparés des
  utilisateurs tenants.
- **Audit** : journal `audit_log` (tenant, site, utilisateur, action, entité,
  avant/après, IP, horodatage) alimenté explicitement par les services pour les
  actions sensibles (connexion, droits, stock, ventes, caisse, annulations).
- **Endpoints publics** (menu QR, V2) : espace `/api/v1/public/…`, accès par jeton
  de table signé, périmètre minimal en lecture + création de commande, limitation de débit.
- **Validation** : toute entrée validée par Pydantic ; erreurs au format
  *Problem Details* (RFC 9457).
- **Limitation de débit (production)** : à configurer au niveau du reverse proxy, par
  adresse IP, sur `/api/v1/auth/*` (en complément du verrouillage par compte).

## 7. Architecture frontend

### 7.1 Pile

React · TypeScript · Vite · React Router · TanStack Query · React Hook Form · Zod · PrimeReact
(version : voir [ADR-0005](../adr/0005-versions-frontend.md)) · react-i18next
([ADR-0009](../adr/0009-i18n-et-terminologie.md)).

### 7.2 Structure

```text
frontend/src/
├── app/        # amorçage : App, routeur, ProtectedApp, registre des modules (modules.ts)
├── core/
│   ├── api/            # client HTTP (jeton en mémoire, rafraîchissement), types
│   ├── auth/           # AuthProvider (session, tenant actif), préférences d'onglet
│   ├── capabilities/   # CapabilitiesProvider : /me/capabilities, site actif, can()
│   ├── i18n/           # i18next, ressources fr, application de la terminologie
│   └── modules/        # types FrontendModule, buildNavigation / buildRoutes
├── layouts/    # AppLayout (menu dynamique, sélecteur de site, changement de tenant)
├── modules/    # un dossier par module (même code que le backend)
│   └── <module>/
│       ├── index.ts    # manifeste frontend (navigation, routes, permissions)
│       ├── api.ts      # hooks TanStack Query
│       └── *Page.tsx   # écrans chargés à la demande
├── pages/      # hors module : connexion, changement de mot de passe, choix du tenant
└── shared/     # UI générique (FormField, PageHeader, ErrorMessage…), utilitaires
```

### 7.3 Interface dynamique

```ts
// illustratif — non implémenté
export interface FrontendModule {
  code: string;                 // identique au code du module backend
  routes: RouteObject[];        // pages en lazy loading
  navigation: NavItem[];        // { key, labelKey, icon, path, permission? }
}
```

- Un **registre** liste explicitement les modules frontend (`src/app/modules.ts`).
- Au démarrage de session, le client charge `/me/capabilities` ; routes et menu sont
  **générés** à partir du registre filtré par `modules` + `permissions`, ordonnés selon
  `navigation`, et libellés selon `terminology` du profil.
- Un module absent n'est ni routé ni chargé (code splitting par module).
- Les gardes de route côté client sont **ergonomiques uniquement**.

### 7.4 Règles

- **Pas de logique métier faisant foi dans React** : totaux, remises, stock,
  statuts viennent du backend. Un calcul local (ex. aperçu du panier POS) est
  toujours recalculé et validé par le backend.
- État serveur : **TanStack Query** ; état local : React. Pas de store global
  tant que le besoin n'est pas démontré.
- Formulaires : **React Hook Form + Zod** (validation ergonomique ; la validation
  de référence reste celle du backend).
- Types d'API générés depuis l'OpenAPI du backend (ex. `openapi-typescript`) —
  prévu en phase 1.
- Montants reçus en **chaînes décimales** ; pas d'arithmétique monétaire en `number`.

## 8. Architecture backend

### 8.1 Structure

```text
backend/app/
├── main.py            # fabrique d'application (create_app)
├── cli.py             # commande `stockmanager` (adaptateur vers les services)
├── models.py          # import de tous les modèles (métadonnées Alembic)
├── core/              # config, db (sessions + contexte RLS + filtre ORM), security, errors
├── api/v1/            # agrégation des routeurs, montage des routeurs de modules
├── platform/
│   ├── registry.py, manifests.py, context.py, models_base.py
│   ├── identity/      # utilisateurs, sessions, authentification
│   ├── tenancy/       # tenants, sites, activation des modules
│   ├── access/        # appartenances, rôles, permissions, affectations aux sites
│   ├── catalog/       # profils, plans, politiques (data/*.toml) + synchronisation
│   ├── subscriptions/ # abonnement, statut effectif, limites
│   ├── capabilities/  # résolution des capacités, /me/capabilities
│   ├── audit/         # journal d'audit
│   └── provisioning/  # TenantProvisioningService
├── modules/           # modules métier (planned.py en Phase 1 ; paquets à venir)
│   └── <module>/
│       ├── manifest.py    # déclaration (code, dépendances, permissions, routeur)
│       ├── router.py      # HTTP uniquement : validation, dépendances, appel du service
│       ├── schemas.py     # Pydantic (entrées / sorties)
│       ├── service.py     # règles métier, transactions, événements
│       ├── repository.py  # accès données (filtrage tenant/site systématique)
│       └── models.py      # modèles SQLAlchemy 2
└── shared/            # types valeur (Money, Quantity), identifiants, erreurs communes
```

### 8.2 Couches

`router → service → repository → base`. Le routeur ne contient aucune règle métier ;
le service porte les règles et les **frontières transactionnelles** (unité de
travail : une requête = une transaction, commit à la fin si succès).

### 8.3 Événements de domaine

- Les effets **obligatoirement atomiques** (vente validée ⇒ mouvements de stock)
  sont des **appels de service directs dans la même transaction**.
- Les effets **secondaires** (audit enrichi, alertes de stock, notifications mobiles)
  passent par des événements de domaine en processus, puis par une table
  **outbox** lorsque des consommateurs asynchrones apparaîtront (mobile V2.5).

## 9. Données et règles métier transverses

### 9.1 Conventions

| Sujet | Convention |
|---|---|
| Identifiants | UUIDv7 générés par l'application (`app/shared/ids.py`) : triables, générables hors ligne (POS offline, mobile) |
| Colonnes communes | `id`, `tenant_id`, (`site_id`), `created_at`, `updated_at`, `created_by` |
| Dates | `timestamptz` stockées en UTC ; fuseau d'affichage par tenant |
| Montants | `NUMERIC(18,2)` ↔ `Decimal` ; devise par tenant (XOF par défaut, hypothèse à confirmer) |
| Quantités | `NUMERIC(18,3)` ↔ `Decimal` (vente au kg, au litre, au mètre) |
| API | montants/quantités sérialisés en **chaînes** |
| Suppression | pas de suppression physique des documents commerciaux ; annulation / archivage |
| Migrations | Alembic, une migration par changement, revue obligatoire |

### 9.2 Stock — [ADR-0004](../adr/0004-stock-service-central.md)

- `stock_levels (tenant_id, site_id, item_id, quantity)` avec **contrainte
  `CHECK (quantity >= 0)`** en base.
- `stock_movements` : **journal append-only** (type, quantité signée, coût unitaire,
  document source, utilisateur, horodatage). Jamais modifié ni supprimé ; une
  erreur se corrige par un mouvement inverse.
- **Un seul point d'entrée** : `StockService`. Il verrouille les lignes concernées
  (`SELECT … FOR UPDATE`, dans un ordre déterministe pour éviter les interblocages),
  refuse toute sortie rendant le stock négatif, écrit le mouvement et le niveau
  **dans la même transaction** que le document source.

### 9.3 Ventes et paiements

- Cycle explicite (`brouillon → validée → [annulée]`), transition de validation
  **conditionnelle** (`UPDATE … WHERE status = 'brouillon'`) + **clé d'idempotence**
  fournie par le client (double clic, réseau instable, futur POS offline).
- Le total est recalculé côté serveur ; la somme des paiements est contrôlée
  (paiement mixte, partiel, crédit client selon capacités).
- Caisse : sessions (ouverture / fond de caisse / mouvements / fermeture /
  rapprochement) — module dédié en V1.

## 10. Extensibilité prévue

| Extension | Point d'ancrage |
|---|---|
| Variantes, code-barres | Modèle catalogue : article ↔ *unités vendables* (SKU) dès la V1 pour éviter une migration lourde |
| Lots, péremption | Niveaux de stock par lot optionnels, derrière le module `stock.lots` |
| Promotions, marges | Services de tarification séparés, appelés par ventes/POS |
| Menu restaurant | **Catalogue interne → produits commerciaux → menu → disponibilité → menu QR** ; le menu référence des produits sans les dupliquer ; la disponibilité (manuelle, rupture, épuisé, horaires, automatique selon stock) est un état propre au menu, vérifié par le backend à l'ajout au panier |
| Customer Mobile Web (QR) | Point d'entrée frontend distinct et léger + API publique restreinte |
| Mobile Flutter | Même API REST ; aucune logique spécifique côté serveur |
| POS offline | UUID générés côté client + idempotence + file de synchronisation |
| Nouveaux secteurs | Nouveau profil (+ modules si nécessaire) |

## 11. Infrastructure

- **Développement** : `docker compose up --build` (PostgreSQL 16 avec création du rôle
  applicatif, service `migrate` = migrations + catalogue, backend avec rechargement,
  frontend Vite). Sans Docker : `uv` pour le backend, `npm` pour le frontend.
- **Deux rôles PostgreSQL** : propriétaire (`SM_MIGRATION_DATABASE_URL` : migrations,
  catalogue) et applicatif (`SM_DATABASE_URL` : API et CLI de provisioning, sans
  `BYPASSRLS`). Le nom du rôle applicatif est configurable (`SM_DB_APP_ROLE`).
- **Production (à définir)** : reverse proxy (TLS) servant la SPA statique et relayant
  `/api` vers FastAPI (Uvicorn/Gunicorn), PostgreSQL managé ou dédié avec sauvegardes,
  secrets par variables d'environnement.
- Configuration par variables d'environnement préfixées `SM_` (backend) et `VITE_` (frontend).

## 12. Qualité

- Backend : `pytest`, `ruff` (lint + format), `mypy --strict`.
- Frontend : `vitest`, `eslint`, `prettier`, `tsc`.
- **Tests d'isolation tenant obligatoires** : tout endpoint tenant-scoped a un test
  prouvant qu'un utilisateur du tenant B ne peut ni lire ni modifier les données du tenant A.
- Tests d'intégration sur un **vrai PostgreSQL** (RLS, contraintes, verrous), l'API
  s'exécutant sous le rôle applicatif.
- **Taille du bundle frontend** : surveillée à chaque build (paquet principal ≈ 168 kB gzip
  en Phase 1) ; chaque module métier est chargé à la demande (lazy loading) pour la maîtriser.
- **CI GitHub Actions** (`.github/workflows/ci.yml`) : backend (ruff, mypy strict,
  validation du catalogue, pytest avec PostgreSQL 16, `alembic check`, réversibilité des
  migrations), frontend (eslint, prettier, tsc, vitest, build), validation Compose.

## 13. Feuille de route technique proposée

| Phase | Contenu | Correspond à |
|---|---|---|
| **0 — Fondations** ✅ | Structure du repo, squelettes, documentation, décisions | — |
| **1 — Socle plateforme** ✅ | Base de données + Alembic, tenants, sites, utilisateurs, appartenances, auth, RBAC, registre de modules, capacités, profils/plans (données), abonnements, audit, provisioning CLI, shell frontend (login, layout, navigation dynamique), CI | V1 |
| **2 — Catalogue & stock** 🔄 | 2.1 ✅ catégories, fournisseurs, articles · 2.2 ✅ stock par site, entrées/sorties, mouvements, alertes · puis transferts, inventaires ([`CATALOGUE_STOCK.md`](CATALOGUE_STOCK.md)) | V1 |
| **3 — Ventes & encaissement** | Clients, ventes, paiements, caisse, POS | V1 |
| **4 — Pilotage** | Rapports, alertes, abonnements | V1 |
| suivantes | V1.5 → V3 selon la roadmap produit | — |

Chaque phase démarre **après validation explicite**.

## 14. Questions ouvertes (à trancher par TechNova)

Tranchées le 2026-09-23 : PrimeReact 10 MIT, multi-tenant RLS, TenantMembership,
CLI de provisioning, mot de passe provisoire, SQLAlchemy synchrone, react-i18next, XOF par
défaut, français d'abord (voir ADR-0005 à 0009).

Tranchées le 2026-09-23 (validation Phase 1) : ADR-0010, ADR-0011, valeurs des plans,
structure des plans (ADR-0012).

Restent ouvertes :

1. **Hébergement cible** de la production (et reverse proxy : TLS, limitation de débit).
2. Questions de la Phase 2 : voir [`CATALOGUE_STOCK.md`](CATALOGUE_STOCK.md).
