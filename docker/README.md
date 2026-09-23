# Docker

Images de **développement** utilisées par `docker-compose.yml` (racine du dépôt) :

| Fichier | Service |
|---|---|
| `backend/Dockerfile` | API FastAPI (Uvicorn, rechargement via volume monté) ; aussi utilisé par le service `migrate` |
| `frontend/Dockerfile` | Serveur de développement Vite |
| `postgres/init/01-app-role.sh` | Crée le rôle applicatif `stockmanager_app` (sans `BYPASSRLS`) au premier démarrage de la base |

Au démarrage, le service `migrate` applique les migrations puis synchronise le catalogue
(profils, plans, politiques) ; l'API démarre ensuite.

Le contexte de build est la racine du dépôt. Les images de production
(SPA statique derrière un reverse proxy, Uvicorn/Gunicorn) seront définies ultérieurement.

```bash
docker compose up --build
docker compose run --rm -it backend stockmanager create-tenant --help
docker compose down        # ajoute -v pour supprimer les données PostgreSQL locales
```
