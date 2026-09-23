# Docker

Images de **développement** utilisées par `docker-compose.yml` (racine du dépôt) :

| Fichier | Service |
|---|---|
| `backend/Dockerfile` | API FastAPI (Uvicorn, rechargement via volume monté) |
| `frontend/Dockerfile` | Serveur de développement Vite |

Le contexte de build est la racine du dépôt. Les images de production
(SPA statique derrière un reverse proxy, Uvicorn/Gunicorn) seront définies ultérieurement.

```bash
docker compose up --build
docker compose down        # ajoute -v pour supprimer les données PostgreSQL locales
```
