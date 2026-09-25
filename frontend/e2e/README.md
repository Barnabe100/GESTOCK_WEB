# Tests de bout en bout (Playwright)

Ils s'exécutent contre la **pile réelle** : PostgreSQL (RLS active, rôle applicatif sans
`BYPASSRLS`), backend FastAPI et frontend. Ils ne démarrent aucun serveur.

## Préparation (une fois)

```bash
docker compose up -d db
cd backend
uv run alembic upgrade head && uv run stockmanager catalog sync
SM_OWNER_PASSWORD='Provisoire-E2E-1' uv run stockmanager create-tenant \
  --name "Démo E2E" --slug demo-e2e --business-profile retail.quincaillerie --country BF --plan ENTREPRISE \
  --owner-email e2e-owner@example.com --owner-name "Propriétaire E2E"
```

À la première connexion, remplacez le mot de passe provisoire par `E2e-Proprietaire-2026`
(ou fixez vos valeurs avec `E2E_OWNER_EMAIL`, `E2E_OWNER_PASSWORD`, `E2E_TENANT_NAME`).

Les transferts vérifient aussi qu'un plan STANDARD n'y a pas accès : créez une seconde
entreprise, puis remplacez son mot de passe provisoire par `E2e-Standard-2026` (ou
`E2E_STANDARD_EMAIL`, `E2E_STANDARD_PASSWORD`, `E2E_STANDARD_TENANT`) :

```bash
echo 'Provisoire-E2E-Std-1' | uv run stockmanager create-tenant --name "Démo E2E Standard" \
  --slug demo-e2e-standard --business-profile retail.quincaillerie --country BF --plan STANDARD \
  --owner-email e2e-standard@example.com --owner-name "Propriétaire Standard" \
  --owner-password-stdin
```

La rétrogradation ENTREPRISE → STANDARD utilise une troisième entreprise, basculée par le test
avec `stockmanager change-plan` (exécutée dans `../backend`, ou `E2E_BACKEND_DIR`) puis remise
en ENTREPRISE ; mot de passe définitif `E2e-Retrograde-2026` (ou `E2E_DOWNGRADE_EMAIL`,
`E2E_DOWNGRADE_PASSWORD`, `E2E_DOWNGRADE_TENANT`) :

```bash
echo 'Provisoire-E2E-Retro-1' | uv run stockmanager create-tenant --name "Démo E2E Rétrogradé" \
  --slug demo-e2e-downgrade --business-profile retail.quincaillerie --country BF --plan ENTREPRISE \
  --owner-email e2e-downgrade@example.com --owner-name "Propriétaire Rétrogradé" \
  --owner-password-stdin
```

## Exécution

```bash
cd backend && SM_SIGNUP_RATE_LIMIT_ATTEMPTS=1000 uv run uvicorn app.main:app --port 8000  # terminal 1
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
- `payments.e2e.ts` (Phase 2.7) : prépare par l'API un article vendu 10 000 (stock 50) et une
  vente de 100 000 ; paiement complet après validation depuis la fiche (stock diminué, « Payée »,
  solde 0), paiement partiel puis successif et mixte, annulation d'un paiement (« Non payée »,
  stock inchangé), surpaiement refusé, mobile.
- `receivables.e2e.ts` (Phase 2.8) : active le module Créances (entreprise créée avant la 2.8),
  prépare un article vendu 10 000 (100 u en boutique, 20 u au dépôt) ; vente à crédit validée
  par l'interface, créance listée, paiement partiel (créance diminuée), paiement final (créance
  disparue), annulation d'un paiement (créance réapparue) ; limite de crédit (refus au-delà,
  encaissement immédiat accepté, compte client) ; limite non configurée ; vendeur limité à un
  site (créances de son site seulement) et autre entreprise (introuvable) ; mobile.
- `cash.e2e.ts` (Phase 2.9) : sur le site « Dépôt E2E » (sessions restées ouvertes clôturées au
  préalable) : création d'une caisse, ouverture (fond 100 000), vente encaissée en espèces à la
  validation (paiement, mouvement lié, solde 150 000), entrée, sortie, clôture (théorique
  130 000, compté 128 500, écart −1 500), session fermée refusant tout mouvement ; paiement
  espèces sans caisse ouverte refusé ; idempotence (un paiement, un mouvement) ; mobile.
  `payments.e2e.ts` et `receivables.e2e.ts` ouvrent au besoin la caisse « Caisse E2E » de la
  boutique (`ensureCashOpen`).
- `signup.e2e.ts` (Phase 3.2-A) : publie le plan STANDARD le temps du test (SQL propriétaire,
  `../backend` ou `E2E_BACKEND_DIR`), inscription complète depuis la page de connexion,
  abonnement en attente d'activation (site accepté, catégorie refusée), refus générique d'un
  e-mail existant, mobile. Démarrer le backend avec une limite d'inscriptions suffisante :
  `SM_SIGNUP_RATE_LIMIT_ATTEMPTS=1000 uv run uvicorn app.main:app --port 8000`.
- `company.e2e.ts` (Phase 3.2-C) : même préparation que `signup.e2e.ts` ; page Entreprise
  (étoiles des obligatoires, devise figée), informations recommandées → étape `configuration`
  en cours puis terminée (et définitive), aperçu documentaire sans « N/A », entreprise créée
  par la CLI puis rendue « historique » (pays NULL, SQL propriétaire) : pays renseigné depuis
  la page → étape `company` terminée ; mobile.
- `onboarding.e2e.ts` (Phase 3.2-B) : même préparation que `signup.e2e.ts` ; inscription par
  l'API, bandeau du tableau de bord, page d'installation, création du premier site depuis son
  étape (`?create=1`), installation terminée mais abonnement toujours en attente d'activation
  (catégorie refusée, étape non déclarable « terminée »), mobile.
- `profiles.e2e.ts` (Phase 3.1) : crée à chaque exécution, par `stockmanager create-tenant`
  (`../backend` ou `E2E_BACKEND_DIR`), une supérette (`retail.alimentation`) et un restaurant
  (`restaurant.restaurant`) du même propriétaire ; menus et thèmes propres, vente au point de
  vente (alimentation), fonctionnalités restaurant planifiées absentes du menu et sans route,
  quincaillerie (entreprise principale), isolation et changement d'entreprise, mobile.
- `pos.e2e.ts` (Phase 3.0) : article à 10 000 (50 u en boutique), caisse de la boutique ouverte
  au besoin ; vente comptant en espèces (stock 48, vente POS payée, mouvement de caisse), vente
  Mobile Money (aucun mouvement de caisse), vente à crédit (client choisi par F4, créance,
  seconde vente refusée par la limite), paiement mixte 40 000 espèces + 60 000 Mobile Money,
  mobile (onglets Articles / Panier).
- `ui.e2e.ts` (Phase 2.5-B, Design System) : navigation groupée, tableau de bord (indicateurs,
  actions rapides), liste standard (recherche, « Aucun résultat », réinitialisation),
  désactivation confirmée (annuler puis confirmer), entrée de stock saisie et validée par
  l'interface, niveau de stock mis à jour.
