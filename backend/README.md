# StockManager Web — Backend

API REST FastAPI. Voir [l'architecture](../docs/architecture/ARCHITECTURE.md) (§8).

## Commandes

```bash
uv sync                                            # installe les dépendances (.venv)
uv run uvicorn app.main:app --reload --port 8000   # http://localhost:8000/api/v1/docs
uv run pytest                                      # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

Configuration : variables d'environnement préfixées `SM_` (voir `.env.example`).

## Organisation

| Dossier | Contenu |
|---|---|
| `app/core/` | Configuration, infrastructure technique |
| `app/api/v1/` | Agrégation des routeurs versionnés |
| `app/platform/` | Socle SaaS (tenants, sites, utilisateurs, RBAC, plans, profils, capacités, audit) — phase 1 |
| `app/modules/` | Modules métier — à partir de la phase 2 |
| `app/shared/` | Types et utilitaires partagés |
| `tests/` | Tests pytest |
