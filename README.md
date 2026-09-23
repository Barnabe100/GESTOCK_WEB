# StockManager Web

Plateforme **SaaS multi-tenant** de gestion commerciale polyvalente, éditée par **TechNova**.

Un **Core commun**, des **profils d'activité** (alimentation, quincaillerie, restaurant,
boutique…) et des **modules spécialisés** : l'interface et les fonctionnalités
s'adaptent au secteur, à l'abonnement, aux modules activés et aux droits de chaque
utilisateur.

> **État actuel : phase 0 — fondations.** Le dépôt contient la structure, des
> squelettes techniques exécutables et la documentation d'architecture.
> **Aucune fonctionnalité métier n'est implémentée.**

## Documentation

- [Architecture initiale](docs/architecture/ARCHITECTURE.md)
- [Décisions d'architecture (ADR)](docs/adr/README.md)
- [Consignes pour les assistants IA](CLAUDE.md)

## Structure

```text
GESTOCK_WEB/
├── backend/             # API FastAPI (Python 3.11+, uv)
├── frontend/            # SPA React + TypeScript (Vite)
├── docker/              # Dockerfiles
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
| Frontend | React 19, TypeScript, Vite, React Router, TanStack Query, React Hook Form, Zod, PrimeReact |
| Backend | Python, FastAPI, Pydantic, SQLAlchemy 2, Alembic |
| Base de données | PostgreSQL 16 |
| API | REST (`/api/v1`, OpenAPI) |
| Infrastructure | Docker, Docker Compose |

## Démarrage rapide

### Avec Docker

```bash
docker compose up --build
```

- Frontend : http://localhost:5173
- API : http://localhost:8000/api/v1/health
- Documentation OpenAPI : http://localhost:8000/api/v1/docs

### Sans Docker

Prérequis : Python ≥ 3.11 avec [uv](https://docs.astral.sh/uv/), Node.js ≥ 22.

```bash
# Backend
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# Frontend (autre terminal)
cd frontend
npm install
npm run dev
```

Le serveur Vite relaie `/api` vers `http://localhost:8000`.

## Contrôles qualité

```bash
# Backend
cd backend && uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy app

# Frontend
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
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
