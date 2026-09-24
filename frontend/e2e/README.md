# Tests de bout en bout (Playwright)

Ils s'exécutent contre la **pile réelle** : PostgreSQL (RLS active, rôle applicatif sans
`BYPASSRLS`), backend FastAPI et frontend. Ils ne démarrent aucun serveur.

## Préparation (une fois)

```bash
docker compose up -d db
cd backend
uv run alembic upgrade head && uv run stockmanager catalog sync
SM_OWNER_PASSWORD='Provisoire-E2E-1' uv run stockmanager create-tenant \
  --name "Démo E2E" --slug demo-e2e --profile quincaillerie --plan ENTREPRISE \
  --owner-email e2e-owner@example.com --owner-name "Propriétaire E2E"
```

À la première connexion, remplacez le mot de passe provisoire par `E2e-Proprietaire-2026`
(ou fixez vos valeurs avec `E2E_OWNER_EMAIL`, `E2E_OWNER_PASSWORD`, `E2E_TENANT_NAME`).

## Exécution

```bash
cd backend && uv run uvicorn app.main:app --port 8000     # terminal 1
cd frontend && npm run dev                                  # terminal 2
cd frontend && npm run e2e                                  # terminal 3
```

Variables : `E2E_BASE_URL` (défaut `http://localhost:5173`), `E2E_CHROMIUM_PATH` (navigateur
déjà installé, ex. `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`). Le projet `mobile`
(390 × 844) rejoue les tests marqués `@mobile`. Les données créées portent un suffixe unique :
la suite peut être rejouée sur la même base.
