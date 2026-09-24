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

Les transferts vérifient aussi qu'un plan STANDARD n'y a pas accès : créez une seconde
entreprise, puis remplacez son mot de passe provisoire par `E2e-Standard-2026` (ou
`E2E_STANDARD_EMAIL`, `E2E_STANDARD_PASSWORD`, `E2E_STANDARD_TENANT`) :

```bash
echo 'Provisoire-E2E-Std-1' | uv run stockmanager create-tenant --name "Démo E2E Standard" \
  --slug demo-e2e-standard --profile quincaillerie --plan STANDARD \
  --owner-email e2e-standard@example.com --owner-name "Propriétaire Standard" \
  --owner-password-stdin
```

La rétrogradation ENTREPRISE → STANDARD utilise une troisième entreprise, basculée par le test
avec `stockmanager change-plan` (exécutée dans `../backend`, ou `E2E_BACKEND_DIR`) puis remise
en ENTREPRISE ; mot de passe définitif `E2e-Retrograde-2026` (ou `E2E_DOWNGRADE_EMAIL`,
`E2E_DOWNGRADE_PASSWORD`, `E2E_DOWNGRADE_TENANT`) :

```bash
echo 'Provisoire-E2E-Retro-1' | uv run stockmanager create-tenant --name "Démo E2E Rétrogradé" \
  --slug demo-e2e-downgrade --profile quincaillerie --plan ENTREPRISE \
  --owner-email e2e-downgrade@example.com --owner-name "Propriétaire Rétrogradé" \
  --owner-password-stdin
```

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

## Suites

- `customers.e2e.ts` (Phase 2.3) : clients, contrôle du Vendeur, mobile.
- `sales.e2e.ts` (Phase 2.4) : prépare par l'API un article (prix 1 500), 10 unités en stock
  et un client, puis vente complète (brouillon, validation, stock 10 → 7, mouvement, audit,
  double validation refusée), Vendeur sans droit d'annulation, annulation par
  l'Administrateur (stock rétabli), mobile.
- `transfers.e2e.ts` (Phase 2.5) : crée au besoin le site « Dépôt E2E » (l'entreprise de test
  devient multi-sites) et, par test, un article stocké sur les deux sites ; transfert complet
  (stock 100 → 70 et 20 → 50, CMUP 1 400, mouvements, audit), stock insuffisant, plan STANDARD
  en consultation seule, rétrogradation ENTREPRISE → STANDARD (historique conservé et
  consultable, aucune opération), mobile.
- `inventories.e2e.ts` (Phase 2.6) : prépare par l'API un article (20 u) ; parcours complet
  (création ciblée, démarrage, saisie 17, vente de 2 pendant le comptage, fin du comptage,
  validation confirmée → écart −1 sur le stock courant, stock 17, mouvement d'ajustement),
  Vendeur en consultation seule, isolation site et entreprise, plan STANDARD, mobile.
- `ui.e2e.ts` (Phase 2.5-B, Design System) : navigation groupée, tableau de bord (indicateurs,
  actions rapides), liste standard (recherche, « Aucun résultat », réinitialisation),
  désactivation confirmée (annuler puis confirmer), entrée de stock saisie et validée par
  l'interface, niveau de stock mis à jour.
