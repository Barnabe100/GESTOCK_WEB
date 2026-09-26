# Docker

Images de **développement** utilisées par `docker-compose.yml` (racine du dépôt) :

| Fichier | Service |
|---|---|
| `backend/Dockerfile` | API FastAPI (Uvicorn, rechargement via volume monté) ; aussi utilisé par le service `migrate` |
| `frontend/Dockerfile` | Serveur de développement Vite |
| `postgres/init/01-app-role.sh` | Crée le rôle applicatif `stockmanager_app` (sans `BYPASSRLS`) au premier démarrage de la base |
| `postgres/init/02-platform-role.sh` | Crée le rôle de la console TechNova `stockmanager_platform` (sans `BYPASSRLS`, ADR-0031) ; à exécuter à la main sur un volume existant (la migration 0017 vérifie sa présence) |

Au démarrage, le service `migrate` applique les migrations puis synchronise le catalogue
(profils, plans, politiques) ; l'API démarre ensuite. Le service `console` (console TechNova, processus distinct,
port 8001 publié sur `127.0.0.1` seulement) démarre aussi ; le frontend relaie `/platform-api`
vers lui.

Le **Signing Service** des licences (ADR-0034) n'est **pas** dans ce Compose ni dans ces
images : il est déployé à part avec sa propre image (`signing-service/Dockerfile`), sa clé
privée montée depuis l'hôte du service. Sans lui, la console fonctionne mais la génération de
licence répond `503 signing_service_unavailable` (configuration : `SM_SIGNING_SERVICE_URL`,
`SM_SIGNING_CLIENT_SECRET`, `SM_LICENSE_PUBLIC_KEYS_FILE`).

Le contexte de build est la racine du dépôt. Les images de production
(SPA statique derrière un reverse proxy, Uvicorn/Gunicorn) seront définies ultérieurement.

```bash
docker compose up --build
docker compose run --rm -it backend stockmanager create-tenant --help
docker compose run --rm -it backend stockmanager platform-admin create --email … --name "…"
docker compose down        # ajoute -v pour supprimer les données PostgreSQL locales
```
