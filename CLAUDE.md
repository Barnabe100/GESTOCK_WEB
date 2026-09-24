# CLAUDE.md — Consignes pour les assistants IA

Ce fichier guide Claude (et tout assistant) travaillant sur **StockManager Web**
(dépôt `GESTOCK_WEB`, éditeur TechNova). À lire avant toute modification.

## Projet

Plateforme SaaS **multi-tenant, multi-sites** de gestion commerciale.
Architecture : **Core commun + profils d'activité + modules spécialisés**.
Référence complète : [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md)
et [`docs/adr/`](docs/adr/README.md).

**Phase actuelle : 2 — catalogue et stock.** Sous-phases livrées : 2.1 (modules `catalog` et
`suppliers`), 2.2 (modules `stock` — niveaux et CMUP par site, entrées, sorties, motifs,
journal des mouvements, seuils par site — et `alerts`), consolidation du RBAC (rôles de
base Administrateur / Gestionnaire / Vendeur / Consultant, rôles personnalisés, ADR-0015),
2.3 (module `customers` : référentiel clients, [`CLIENTS.md`](docs/architecture/CLIENTS.md)),
2.4 (module `sales` : ventes simples au comptant, validation via `StockService`,
[`SALES.md`](docs/architecture/SALES.md), ADR-0017 ; prix toujours lus dans le catalogue),
2.5 (transferts inter-sites dans `stock`, fonctionnalité de plan `stock.transfers` :
`StockService.transfer`, ADR-0018 ; une permission peut dépendre d'une fonctionnalité ;
sans la fonctionnalité, l'historique reste consultable en lecture seule)
et 2.5-B (Design System de l'interface, [`DESIGN_SYSTEM.md`](docs/architecture/DESIGN_SYSTEM.md) ;
aucune règle métier modifiée).
Inventaires et autres modules métier (paiements, créances, POS, caisse, restaurant…) :
seulement déclarés `planned`
(`backend/app/modules/planned.py`). Ne pas les
commencer sans validation explicite ; s'arrêter à la fin de chaque sous-phase.

Règles métier de référence (issues du Desktop, identifiants CAT/SUP/ART/STK/ENT/SOR/ALR) :
[`docs/architecture/CATALOGUE_STOCK.md`](docs/architecture/CATALOGUE_STOCK.md).

Documents clés : [`docs/architecture/DATA_MODEL.md`](docs/architecture/DATA_MODEL.md),
[`docs/architecture/API.md`](docs/architecture/API.md).

## Méthode de travail

- **Analyser avant de modifier.** Ne rien supposer : lire le code et la doc concernés.
- **Attendre la validation explicite** de l'utilisateur avant chaque grande phase
  (voir la feuille de route, §13 de l'architecture). Ne pas anticiper une phase.
- Ne rien supprimer sans justification explicite.
- **Tester chaque changement** (commandes ci-dessous) et rapporter les résultats tels quels.
- **Documenter les décisions importantes** dans une nouvelle ADR (`docs/adr/`).
- Le dépôt `GESTOK_ENTREP` (Desktop historique) est une **référence métier en lecture
  seule** : ne jamais le modifier ; ne pas copier son architecture technique.

## Règles d'architecture (non négociables)

1. **Le backend est la seule frontière de sécurité.** Le frontend ne fait que masquer
   pour l'ergonomie.
2. **Isolation des tenants** : `tenant_id` dérivé du jeton uniquement (jamais d'un
   paramètre client), filtre ORM automatique (`TenantFiltered`), RLS PostgreSQL
   (`ENABLE` + `FORCE`). Toute nouvelle table tenant-scoped : `TenantScopedMixin`,
   politique RLS + droits minimaux dans sa migration, FK composites `(tenant_id, …)`, tests
   d'isolation SQL **et** API. Jamais de `BYPASSRLS` pour le rôle applicatif.
3. Chaîne de contrôle : **User → Tenant → Membership → Rôle(s) → Permission → Site →
   Resource**, via les dépendances de `app/platform/context.py` (`TenantContext`,
   `require_permission`, `require_module`). Chaque endpoint tenant-scoped exige une permission.
   **RBAC** ([ADR-0015](docs/adr/0015-rbac-roles-de-base-et-personnalises.md)) : les rôles ne
   sont que des regroupements de permissions ; **jamais** de test sur un nom ou un code de rôle.
   Rôles de base (données : `role_templates.toml`) + rôles personnalisés du tenant ; aucun
   rôle supprimé (désactivation) ; anti-escalade par portée (tenant / site) et par sites.
4. **Jamais de `if business_type == "…"`** (ni backend, ni frontend). Tester une
   capacité : `require_module(...)`, `require_permission(...)`, `can(...)` côté client.
   Profils, plans et politiques d'abonnement sont des **données**
   (`backend/app/platform/catalog/data/*.toml`). Toute permission déclare sa nature
   (`read`/`write`/`export`/`admin`/`billing`) : c'est elle que la politique d'abonnement filtre.
5. **Stock** : uniquement via `StockService` ; mouvements append-only ; stock jamais négatif
   (contrainte en base + contrôle service) ; verrouillage de lignes.
6. **Idempotence** des opérations critiques (validation de vente, paiement).
7. **Decimal** pour montants (`NUMERIC(18,2)`) et quantités (`NUMERIC(18,3)`) ;
   jamais `float` ; chaînes dans l'API.
8. **Audit** des actions sensibles.
9. **Pas de logique métier faisant foi dans React.** Le backend recalcule et valide tout.
10. Modules : dépendances déclarées dans le manifeste ; pas d'import des modèles
    internes d'un autre module ; pas de cycle.
11. **Transactions explicites** : SQLAlchemy synchrone ; les services ne valident pas,
    l'endpoint (ou la CLI) appelle `db.commit()`. Écritures d'audit dans la même transaction.
12. **Listes** : pagination, tri (liste blanche, `text_sort` pour les textes) et recherche
    côté serveur (`app/shared/pagination.py`). Montants/quantités : types `Money`/`Quantity`
    (`app/shared/schemas.py`), sérialisés en chaînes JSON.
13. **Textes d'interface** : toujours via i18n (`t(...)`), jamais en dur ; vocabulaire métier
    via l'espace de noms `terminology` (surchargé par le profil). Erreurs API = `code` stable
    traduit dans `errors.json`.
14. **Interface** : suivre le Design System ([`DESIGN_SYSTEM.md`](docs/architecture/DESIGN_SYSTEM.md)) —
    jetons `--sm-*`, composants de `shared/ui` (`PageHeader`, `FilterBar`, `ServerTable`,
    `RowActions`, `StatusBadge`, `EmptyState`, `confirmAction`…), statuts à tonalité unique,
    confirmation des actions sensibles. PrimeReact uniquement.

## Structure

```text
backend/app/
  core/       config, BD, sécurité, logs (aucune règle métier)
  api/v1/     agrégation des routeurs
  platform/   tenants, sites, users, auth, RBAC, plans, profils, registre, capacités, audit
  modules/    modules métier : <module>/{manifest,router,schemas,service,models,api}.py
              (api.py = interface publique utilisée par les autres modules)
  shared/     types valeur, identifiants, erreurs
frontend/src/
  app/        providers, routeur
  core/       client API, auth, capacités, registre de modules
  modules/    un dossier par module (même code que le backend)
  shared/     composants UI et utilitaires génériques
  pages/      pages hors module
docker/       Dockerfiles ; docker-compose.yml à la racine
docs/         architecture/ et adr/
```

## Commandes

```bash
# PostgreSQL local : deux rôles (propriétaire + applicatif sans BYPASSRLS)
docker compose up -d db          # crée aussi le rôle stockmanager_app

# Backend (depuis backend/)
uv sync
uv run alembic upgrade head                 # rôle propriétaire (SM_MIGRATION_DATABASE_URL)
uv run stockmanager catalog sync            # profils, plans, politiques
uv run stockmanager create-tenant --name "…" --slug … --profile restaurant \
    --plan STANDARD --owner-email … --owner-name "…"
uv run stockmanager change-plan --tenant-id … --plan ENTREPRISE   # données conservées, audité
uv run uvicorn app.main:app --reload --port 8000
uv run pytest        # PostgreSQL requis : SM_TEST_DATABASE_URL / SM_TEST_MIGRATION_DATABASE_URL
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run alembic check                        # aucune dérive modèles / migrations

# Frontend (depuis frontend/)
npm install
npm run dev          # proxy /api -> http://localhost:8000
npm run lint && npm run format:check
npm run typecheck
npm test
npm run build
npm run e2e          # Playwright contre la pile démarrée (voir frontend/e2e/README.md)

# Stack complète
docker compose up --build
```

## Conventions

- Code (identifiants, noms de fichiers) en **anglais** ; documentation, messages
  utilisateur et commits descriptifs en **français** acceptés.
- API REST sous `/api/v1`, ressources au pluriel, erreurs au format Problem Details (RFC 9457).
- Permissions nommées `module.ressource.action` (ex. `stock.movement.create`).
- Variables d'environnement : `SM_*` (backend), `VITE_*` (frontend). Ne jamais commiter de `.env`.
- Migrations Alembic : une par changement de schéma, jamais modifiée après fusion ;
  générer avec `alembic revision --autogenerate`, puis ajouter RLS et droits à la main.
- Versions épinglées délibérément : PrimeReact 10 (MIT) et TypeScript 6.0 — voir ADR-0005
  avant toute montée de version.
