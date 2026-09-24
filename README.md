# StockManager Web

Plateforme **SaaS multi-tenant** de gestion commerciale polyvalente, éditée par **TechNova**.

Un **Core commun**, des **profils d'activité** (alimentation, quincaillerie, restaurant,
boutique…) et des **modules spécialisés** : l'interface et les fonctionnalités
s'adaptent au secteur, à l'abonnement, aux modules activés et aux droits de chaque
utilisateur.

> **État actuel : phase 1 — socle plateforme.** Multi-tenant (RLS PostgreSQL), multi-sites,
> utilisateurs multi-entreprises, authentification, rôles et permissions, profils d'activité,
> plans et abonnements, registre de modules, capacités et interface dynamique, audit,
> provisioning par CLI, CI.
> **Phase 2 en cours** : catalogue (catégories, articles) et fournisseurs (2.1) ; stock par
> site, entrées, sorties, journal des mouvements, seuils par site et alertes (2.2).
> RBAC consolidé : rôles de base (Administrateur, Gestionnaire, Vendeur, Consultant) et
> rôles personnalisés par entreprise (ADR-0015). Référentiel clients (2.3). Ventes simples au
> comptant (2.4) : brouillon, validation par le moteur de stock, annulation.
> Transferts, inventaires, paiements, créances, caisse, POS et restaurant ne sont pas commencés.

## Documentation

- [Architecture](docs/architecture/ARCHITECTURE.md)
- [Modèle de données](docs/architecture/DATA_MODEL.md)
- [API REST](docs/architecture/API.md)
- [Catalogue et stock : règles métier](docs/architecture/CATALOGUE_STOCK.md)
- [Clients : règles métier](docs/architecture/CLIENTS.md)
- [Ventes simples : cycle de vie et règles](docs/architecture/SALES.md)
- [Décisions d'architecture (ADR)](docs/adr/README.md)
- [Consignes pour les assistants IA](CLAUDE.md)

## Structure

```text
GESTOCK_WEB/
├── backend/             # API FastAPI (Python 3.11+, uv)
├── frontend/            # SPA React + TypeScript (Vite)
├── .github/workflows/   # Intégration continue
├── docker/              # Dockerfiles, initialisation PostgreSQL
├── docs/
│   ├── architecture/    # Architecture de référence
│   └── adr/             # Décisions d'architecture
├── docker-compose.yml   # Environnement de développement local
├── CLAUDE.md
└── README.md
```

## Pile technique

| Couche | Technologies |
|---|---|
| Frontend | React 19, TypeScript, Vite, React Router, TanStack Query, React Hook Form, Zod, PrimeReact 10, react-i18next |
| Backend | Python, FastAPI, Pydantic, SQLAlchemy 2 (synchrone), Alembic, Argon2, JWT |
| Base de données | PostgreSQL 16 |
| API | REST (`/api/v1`, OpenAPI) |
| Infrastructure | Docker, Docker Compose |

## Démarrage rapide

### Avec Docker

```bash
docker compose up --build        # base, migrations + catalogue, API, frontend

# Créer une première entreprise (le mot de passe provisoire est demandé)
docker compose run --rm -it backend stockmanager create-tenant \
  --name "Maquis Le Baobab" --slug baobab --profile restaurant --plan STANDARD \
  --owner-email gerant@example.com --owner-name "Awa Traoré"
```

Profils disponibles : `alimentation`, `commerce_general`, `quincaillerie`, `restaurant`.
Plans : `STANDARD`, `ENTREPRISE` (`--billing monthly|annual`, `--trial-days N`).

- Frontend : http://localhost:5173
- API : http://localhost:8000/api/v1/health
- Documentation OpenAPI : http://localhost:8000/api/v1/docs

### Sans Docker

Prérequis : Python ≥ 3.11 avec [uv](https://docs.astral.sh/uv/), Node.js ≥ 22,
PostgreSQL ≥ 15 avec deux rôles : propriétaire `stockmanager` et applicatif
`stockmanager_app` (sans `BYPASSRLS`) — voir `docker/postgres/init/01-app-role.sh`.

```bash
# Backend
cd backend
uv sync
uv run alembic upgrade head
uv run stockmanager catalog sync
uv run stockmanager create-tenant --name "…" --slug … --profile alimentation \
    --plan STANDARD --owner-email … --owner-name "…"
uv run uvicorn app.main:app --reload --port 8000

# Frontend (autre terminal)
cd frontend
npm install
npm run dev
```

Le serveur Vite relaie `/api` vers `http://localhost:8000`.

## Contrôles qualité

```bash
# Backend (PostgreSQL requis pour les tests : voir backend/README.md)
cd backend && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy app

# Frontend
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build

# Bout en bout (pile démarrée, données de test : frontend/e2e/README.md)
cd frontend && npm run e2e
```

## Feuille de route produit

| Version | Périmètre |
|---|---|
| V1 | Core commercial : multi-tenant, multi-sites, utilisateurs, RBAC, articles, catégories, fournisseurs, stock, entrées/sorties, mouvements, ventes, clients, paiements, POS, caisse, inventaires, rapports, audit, abonnements |
| V1.5 | Code-barres, retours, promotions, variantes, documents, marges, statistiques avancées |
| V2 | Restaurant : tables, menus, disponibilité, QR, commandes, cuisine, addition |
| V2.5 | Application mobile, notifications, supervision |
| V3 | Lots, péremption, POS hors ligne, automatisations, analyses avancées, nouveaux secteurs |

## Référence fonctionnelle

Le projet Desktop historique **StockManager** (dépôt `GESTOK_ENTREP`) sert uniquement
de référence fonctionnelle et métier. Il n'est pas modifié par ce projet et son
architecture technique n'est pas reprise.

---

© TechNova — StockManager
