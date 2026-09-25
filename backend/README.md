# StockManager Web — Backend

API REST FastAPI (SQLAlchemy 2 synchrone, PostgreSQL). Voir
[l'architecture](../docs/architecture/ARCHITECTURE.md), [le modèle de données](../docs/architecture/DATA_MODEL.md)
et [l'API](../docs/architecture/API.md).

## Base de données : deux rôles

| Variable | Rôle | Usage |
|---|---|---|
| `SM_MIGRATION_DATABASE_URL` | propriétaire (`stockmanager`) | migrations Alembic, `stockmanager catalog sync` |
| `SM_DATABASE_URL` | applicatif (`stockmanager_app`, **sans BYPASSRLS**) | API, `stockmanager create-tenant` |

Le rôle applicatif est créé par `docker/postgres/init/01-app-role.sh` ; ses droits sont
accordés par les migrations (`SM_DB_APP_ROLE`).

Inscription publique (Phase 3.2, ADR-0025) :

| Variable | Défaut | Rôle |
|---|---|---|
| `SM_SIGNUP_ENABLED` | `true` | Ouvre ou ferme `POST /api/v1/public/signup` |
| `SM_SIGNUP_RATE_LIMIT_ATTEMPTS` | `5` | Inscriptions tentées par adresse IP et par fenêtre |
| `SM_SIGNUP_RATE_LIMIT_WINDOW_MINUTES` | `60` | Durée de la fenêtre |
| `SM_SALES_CONTACT_EMAIL` | — | Adresse « Contacter TechNova » des offres sur contact |

Derrière un reverse proxy, lancez uvicorn avec `--proxy-headers --forwarded-allow-ips <IP du
proxy>` : sinon toutes les inscriptions partagent l'adresse du proxy (et sa limite).

## Commandes

```bash
uv sync
uv run alembic upgrade head                   # migrations
uv run stockmanager catalog check             # valider profils / plans / politiques
uv run stockmanager catalog sync              # les synchroniser en base
uv run stockmanager create-tenant --help      # provisionner une entreprise
uv run uvicorn app.main:app --reload --port 8000   # http://localhost:8000/api/v1/docs

uv run pytest                                 # tests (PostgreSQL réel, voir ci-dessous)
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run alembic check                          # aucune dérive modèles / migrations
```

## Tests

Les tests utilisent une base dédiée, **recréée** à chaque session (schéma vidé puis
migrations). L'API y tourne sous le rôle applicatif : la RLS est réellement appliquée.

| Variable | Défaut |
|---|---|
| `SM_TEST_DATABASE_URL` | `postgresql+psycopg://stockmanager_app:stockmanager_app@localhost:5432/stockmanager_test` |
| `SM_TEST_MIGRATION_DATABASE_URL` | `postgresql+psycopg://stockmanager:stockmanager@localhost:5432/stockmanager_test` |

## Organisation

| Dossier | Contenu |
|---|---|
| `app/core/` | Configuration, sessions + contexte RLS + filtre ORM, sécurité, erreurs |
| `app/api/v1/` | Agrégation des routeurs, montage protégé des routeurs de modules |
| `app/platform/` | Socle SaaS : identité, tenants/sites, accès, catalogue, abonnements, capacités, audit, provisioning |
| `app/modules/` | Modules métier (déclarés `planned` en Phase 1) |
| `app/shared/` | UUIDv7, horloge, schémas communs |
| `app/cli.py` | Commande `stockmanager` |
| `migrations/` | Migrations Alembic (RLS et droits inclus) |
| `tests/` | Tests pytest |
