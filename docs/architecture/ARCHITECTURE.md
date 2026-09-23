# StockManager Web — Architecture initiale

> **Statut : PROPOSITION — en attente de validation.**
> Éditeur : TechNova · Produit : StockManager · Projet : GESTOCK_WEB
> Les décisions structurantes sont détaillées dans [`docs/adr/`](../adr/README.md).

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
     `SET LOCAL app.tenant_id = '<uuid>'` ; les politiques RLS rejettent toute ligne
     d'un autre tenant. Le rôle SQL applicatif n'est ni propriétaire des tables ni
     `BYPASSRLS` (les migrations utilisent un rôle distinct).
- Les identifiants de ressources sont des UUID (non énumérables).

### 4.3 Chaîne de contrôle d'une requête

```text
User ──► Tenant ──► Site ──► Permission ──► Resource
 │         │          │           │              │
 │ jeton   │ depuis   │ en-tête   │ rôle(s) de   │ la ressource appartient
 │ valide  │ le jeton │ X-Site-Id │ l'utilisateur│ au tenant (RLS) et au
 │ actif   │ + actif  │ vérifié   │ sur ce site  │ site autorisé
```

Chaque étape est une **dépendance FastAPI** composable : `current_user` →
`current_tenant` → `current_site` → `require_permission("stock.movement.create")`.
Un échec à n'importe quelle étape interrompt la requête (401/403/404).
Une ressource d'un autre tenant répond **404** (on ne révèle pas son existence).

### 4.4 Sites

- `user_site_access` détermine sur quels sites un utilisateur peut agir.
- Les rôles peuvent être attribués **globalement au tenant** ou **limités à un site**.
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
(`platform/capabilities`). Il sert à deux choses :

1. **Application** : chaque routeur de module est protégé par `require_module("pos")`
   et chaque endpoint par `require_permission(...)`. Un module inactif répond 403/404
   même si l'appel est forgé à la main.
2. **Exposition** : `GET /api/v1/me/capabilities` renvoie au client :

```json
{
  "tenant": { "id": "…", "name": "Entreprise ABC" },
  "profile": { "code": "restaurant", "terminology": { "catalog.item": "Produit" } },
  "plan": { "code": "ENTREPRISE", "billing_period": "monthly", "status": "active" },
  "site": { "id": "…", "name": "Maquis Ouaga 2000" },
  "modules": ["dashboard", "catalog", "stock", "pos", "restaurant.tables", "restaurant.kitchen"],
  "permissions": ["pos.sale.create", "restaurant.order.create", "…"],
  "navigation": ["dashboard", "restaurant.tables", "restaurant.orders", "restaurant.kitchen", "pos", "…"]
}
```

### 5.3 Manifeste de module (backend)

Chaque module déclare un manifeste ; un **registre** les collecte au démarrage
(pas de découverte « magique » : liste explicite).

```python
# app/modules/stock/manifest.py  (illustratif — non implémenté)
MANIFEST = ModuleManifest(
    code="stock",
    name="Stock",
    version="1.0",
    depends_on=["catalog"],
    permissions=[
        Permission("stock.level.view", "Consulter les niveaux de stock"),
        Permission("stock.movement.create", "Saisir des entrées / sorties"),
        Permission("stock.transfer.create", "Créer un transfert inter-sites"),
    ],
    router=router,              # monté sous /api/v1/stock, protégé par require_module("stock")
)
```

Règles de dépendance :

- un module dépend du Core/de la plateforme et des modules **déclarés** dans `depends_on` ;
- un module n'importe jamais les modèles internes d'un autre : il passe par son
  **service public** (`modules/<m>/service.py` ou interface dédiée) ;
- pas de dépendance circulaire (contrôle automatisé prévu, ex. `import-linter`).

### 5.4 Business Profiles

Les profils sont des **données** (fichiers de configuration versionnés puis
enregistrés en base), pas du code :

```yaml
# illustratif
code: restaurant
label: Restaurant / Maquis / Café / Bar / Fast-food
modules: [dashboard, catalog, stock, customers, sales, payments, cash_register, pos,
          reports, restaurant.menu, restaurant.tables, restaurant.orders,
          restaurant.kitchen, restaurant.qr, restaurant.recipes]
navigation: [dashboard, restaurant.tables, restaurant.orders, restaurant.kitchen,
             pos, restaurant.menu, catalog, stock, restaurant.recipes, customers, reports]
terminology:
  catalog.item: Produit
defaults:
  pos.mode: table_service
```

```yaml
code: quincaillerie
modules: [dashboard, catalog, stock, sales, pos, cash_register, customers,
          suppliers, inventory_count, reports]
navigation: [dashboard, catalog, stock, sales, pos, customers, suppliers,
             inventory_count, reports]
terminology:
  catalog.item: Article
```

**Ajouter un secteur** = ajouter un profil (et, si besoin, un nouveau module).
Aucune modification du Core ni de conditions dispersées.

### 5.5 Plans d'abonnement

- Offres : **STANDARD** et **ENTREPRISE**, facturation **mensuelle** ou **annuelle**.
  Pas de licence perpétuelle.
- Un plan définit les **modules autorisés** et des **limites** (nombre de sites,
  d'utilisateurs, etc.). Contenu exact : **à définir par TechNova** (voir §14).
- L'abonnement a un statut (`trial`, `active`, `past_due`, `suspended`, `cancelled`)
  pris en compte par la résolution des capacités.

### 5.6 Feature flags

Distincts des modules : bascules fines (ex. `pos.allow_partial_payment`), portées
par le profil (valeur par défaut), le plan (autorisé ou non) et le tenant
(paramètre). Lus via le même service de capacités.

### 5.7 Workflows

Les cycles de vie (vente, commande restaurant, inventaire, transfert) sont modélisés
comme des **machines à états explicites** côté backend (états + transitions
autorisées + permission requise par transition). Exemple futur, cuisine :

```text
nouvelle → en_attente → en_preparation → prete → servie
```

Une transition invalide est refusée par le backend, quel que soit le client.

## 6. Sécurité

- **Authentification** : jeton d'accès JWT court (≈15 min) + jeton de rafraîchissement
  opaque, stocké haché en base, rotatif et révocable (cookie `HttpOnly; Secure;
  SameSite=Strict` pour le web, corps de réponse pour le mobile).
  Mots de passe hachés en **Argon2id**.
- **Autorisation** : RBAC ; permissions nommées `module.ressource.action`, déclarées
  par les manifestes ; rôles définis **par tenant** (avec des modèles de rôles
  fournis par défaut : Propriétaire, Gérant, Caissier, Magasinier, Serveur, Cuisine…).
- **Administration plateforme** (TechNova) : espace et identités séparés des
  utilisateurs tenants.
- **Audit** : journal `audit_log` (tenant, site, utilisateur, action, entité,
  avant/après, IP, horodatage) alimenté explicitement par les services pour les
  actions sensibles (connexion, droits, stock, ventes, caisse, annulations).
- **Endpoints publics** (menu QR, V2) : espace `/api/v1/public/…`, accès par jeton
  de table signé, périmètre minimal en lecture + création de commande, limitation de débit.
- **Validation** : toute entrée validée par Pydantic ; erreurs au format
  *Problem Details* (RFC 9457).

## 7. Architecture frontend

### 7.1 Pile

React · TypeScript · Vite · React Router · TanStack Query · React Hook Form · Zod · PrimeReact
(version : voir [ADR-0005](../adr/0005-versions-frontend.md)).

### 7.2 Structure

```text
frontend/src/
├── app/        # amorçage : providers, routeur, client de requêtes, layout racine
├── core/       # transverse : client API, auth, capacités, registre de modules, i18n
├── modules/    # un dossier par module fonctionnel (même code que le module backend)
│   └── <module>/
│       ├── index.ts        # manifeste frontend (routes, navigation, permissions)
│       ├── api.ts          # hooks TanStack Query de ce module
│       ├── pages/ …        # écrans (chargés à la demande)
│       └── components/ …
├── shared/     # composants UI génériques, utilitaires sans logique métier
└── pages/      # pages hors module (accueil, erreurs)
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

- Un **registre** liste explicitement les modules frontend.
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
├── core/              # config, base de données, sécurité, logs, erreurs — aucune règle métier
├── api/v1/            # agrégation des routeurs versionnés
├── platform/          # SaaS : tenants, sites, users, auth, rbac, plans, profils,
│                      #   registre de modules, capacités, audit, paramètres
├── modules/           # modules métier (catalog, stock, sales, pos, restaurant.* …)
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
| Identifiants | UUID (v7 de préférence : triables, générables hors ligne — utile pour POS offline et mobile) |
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

- **Développement** : `docker compose up --build` (PostgreSQL 16, backend avec
  rechargement, frontend Vite). Sans Docker : `uv` pour le backend, `npm` pour le frontend.
- **Production (à définir)** : reverse proxy (TLS) servant la SPA statique et relayant
  `/api` vers FastAPI (Uvicorn/Gunicorn), PostgreSQL managé ou dédié avec sauvegardes,
  secrets par variables d'environnement.
- Configuration par variables d'environnement préfixées `SM_` (backend) et `VITE_` (frontend).

## 12. Qualité

- Backend : `pytest`, `ruff` (lint + format), `mypy --strict`.
- Frontend : `vitest`, `eslint`, `prettier`, `tsc`.
- **Tests d'isolation tenant obligatoires** : tout endpoint tenant-scoped a un test
  prouvant qu'un utilisateur du tenant B ne peut ni lire ni modifier les données du tenant A.
- Tests d'intégration sur un **vrai PostgreSQL** (RLS, contraintes, verrous).
- CI GitHub Actions (prévue en phase 1) exécutant l'ensemble de ces contrôles.

## 13. Feuille de route technique proposée

| Phase | Contenu | Correspond à |
|---|---|---|
| **0 — Fondations** *(cette intervention)* | Structure du repo, squelettes, documentation, décisions | — |
| **1 — Socle plateforme** | Base de données + Alembic, tenants, sites, utilisateurs, auth, RBAC, registre de modules, capacités, profils/plans (données), audit, shell frontend (login, layout, navigation dynamique), CI | V1 |
| **2 — Catalogue & stock** | Articles, catégories, fournisseurs, stock, entrées/sorties, mouvements, transferts, inventaires | V1 |
| **3 — Ventes & encaissement** | Clients, ventes, paiements, caisse, POS | V1 |
| **4 — Pilotage** | Rapports, alertes, abonnements | V1 |
| suivantes | V1.5 → V3 selon la roadmap produit | — |

Chaque phase démarre **après validation explicite**.

## 14. Questions ouvertes (à trancher par TechNova)

1. **PrimeReact** : rester sur la v10 (MIT) ou adopter la v11 (licence commerciale PrimeUI) ? — [ADR-0005](../adr/0005-versions-frontend.md)
2. **Utilisateur ↔ tenant** : un utilisateur appartient-il à un seul tenant (proposé pour la V1) ou peut-il en rejoindre plusieurs (comptable, consultant) ?
3. **Contenu des plans** STANDARD / ENTREPRISE : modules inclus, limites (sites, utilisateurs, caisses).
4. **Abonnement expiré** : lecture seule, blocage, période de grâce ?
5. **Devise(s)** : XOF uniquement au départ ? Multi-devise envisagée ?
6. **Langues** : français seul au départ ? (l'i18n est prévue de toute façon pour la terminologie par profil)
7. **Hébergement cible** de la production.
8. **Accès à `GESTOK_ENTREP`** : il n'est pas attaché à cette session ; un accès en lecture sera utile en phase 2/3 pour extraire les règles métier détaillées.
