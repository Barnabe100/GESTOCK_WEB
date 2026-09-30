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
cd backend && uv run uvicorn app.console.main:app --port 8001  # terminal 2 (console TechNova)
cd frontend && npm run dev                                  # terminal 3
cd frontend && npm run e2e                                  # terminal 4
```

Licences (`console-licenses.e2e.ts`) : Signing Service local avec une clé **éphémère**
générée **hors du dépôt** (jamais versionnée), et console lancée avec son secret HMAC et le
trousseau public correspondant :

```bash
D=/tmp/sm-signing && cd signing-service && uv sync
uv run signing-service-keygen --key-id dev-ed25519-e2e --out $D/private.pem \
  | grep -v '^#' > $D/public_keys.toml
head -c 48 /dev/urandom | base64 | tr -d '\n' > $D/client-secret && chmod 600 $D/client-secret
SIGNING_KEY_ID=dev-ed25519-e2e SIGNING_PRIVATE_KEY_FILE=$D/private.pem \
SIGNING_CLIENT_SECRET_FILE=$D/client-secret \
  uv run uvicorn --factory signing_service.app:create_app --port 8100      # terminal 5
# terminal 2, à la place : console avec le Signing Service
cd backend && SM_SIGNING_SERVICE_URL=http://127.0.0.1:8100 \
  SM_SIGNING_CLIENT_SECRET="$(cat $D/client-secret)" SM_LICENSE_PUBLIC_KEYS_FILE=$D/public_keys.toml \
  uv run uvicorn app.console.main:app --port 8001
```

Renouvellement (`renewal.e2e.ts`, Phase 3.3-B4) : même Signing Service ; le scénario lance
lui-même le job des rappels (`stockmanager notifications run --now …`, dans `../backend` ou
`E2E_BACKEND_DIR`) avec une date proche de l'échéance de sa propre licence. Le job étant
global, il crée aussi des rappels (idempotents) pour les autres entreprises de la base de
développement.

La console TechNova exige le rôle SQL `stockmanager_platform`
(`docker/postgres/init/02-platform-role.sh`) ; `console.e2e.ts` crée lui-même un
administrateur TechNova par la CLI (`stockmanager platform-admin create`).

Variables : `E2E_BASE_URL` (défaut `http://localhost:5173`), `E2E_CHROMIUM_PATH` (navigateur
déjà installé, ex. `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`). Le projet `mobile`
(390 × 844) rejoue les tests marqués `@mobile`. Les données créées portent un suffixe unique :
la suite peut être rejouée sur la même base.

## Suites

- `console-licenses.e2e.ts` (Phase 3.3-B2) : paiement confirmé → génération de la licence dans
  la console (postes proposés puis ajustés, raison, confirmation), téléchargement du `.lic`
  signé, licence et postes autorisés visibles par l'entreprise (site actif), révocation
  (site suspendu), réémission (nouveau numéro, site de nouveau actif), journal de l'entreprise ;
  sans paiement confirmé : aucune licence, aucune route de licence côté entreprise ; postes
  (installations simulées par l'API) : quota par site et message, idempotence, contrôle,
  « postes autorisés · utilisés · disponibles », libération par l'entreprise puis par
  TechNova. **Exige le Signing Service** (voir « Exécution »).
- `console-payments.e2e.ts` (Phase 3.3-A) : entreprises (abonnement en attente d'activation)
  et administrateurs TechNova créés à chaque exécution ; déclaration d'un paiement par
  l'entreprise (« En attente »), confirmation puis rejet motivé dans la console, statuts et
  motif visibles par l'entreprise, abonnement **non activé** après confirmation, journal de
  l'entreprise ; isolation entre deux entreprises ; décisions simultanées (une seule réussit,
  `409 payment_already_decided`).
- `console-tenants.e2e.ts` (Phase 3.2-G) : entreprise et administrateur TechNova créés à chaque
  exécution ; recherche, fiche, activation manuelle transitoire, changement de plan, suspension
  (accès refusé) et réactivation, journal de la plateforme, entrées miroir du journal de
  l'entreprise ; liste sur mobile.
- `console.e2e.ts` (Phase 3.2-F) : console TechNova — administrateur créé par la CLI,
  connexion, offres & tarifs (publication de STANDARD, raison obligatoire, confirmation,
  nouvelle valeur, historique, journal de la plateforme, effet sur `/public/plans`), refus d'un
  compte d'entreprise, mobile. STANDARD est remis à ses valeurs neutres.
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
  `payments.e2e.ts`, `pos.e2e.ts` et `receivables.e2e.ts` activent la caisse de la boutique et
  ouvrent au besoin la session **du propriétaire** sur un poste « Caisse E2E » libre
  (`ensureCashOpen`, Lot 1 : session = site + poste + utilisateur).
- `encaissement.e2e.ts` (Lot 1, ADR-0037) : crée par la CLI une entreprise à chaque exécution
  (deux sites **sans caisse**) ; vente en espèces sans session (montant reçu 10 000 pour 7 500,
  monnaie 2 500 calculée par le serveur, numéro `VENT-{SITE}-{ANNÉE}-000001`) ; moyen configuré
  « Orange Money » (référence obligatoire) créé par l'interface puis encaissé sur un site sans
  caisse (séquence propre au site) ; paiement mixte (monnaie sur la seule partie espèces) ;
  caisse activée pour un site, session, vente en espèces, désactivation refusée tant que la
  session est ouverte, clôture puis désactivation ; crédit refusé sans client, accepté avec ;
  isolation site A ≠ site B ≠ autre entreprise.
- `signup.e2e.ts` (Phase 3.2-A) : publie le plan STANDARD le temps du test (SQL propriétaire,
  `../backend` ou `E2E_BACKEND_DIR`), inscription complète depuis la page de connexion,
  abonnement en attente d'activation (site accepté, catégorie refusée), refus générique d'un
  e-mail existant, mobile. Démarrer le backend avec une limite d'inscriptions suffisante :
  `SM_SIGNUP_RATE_LIMIT_ATTEMPTS=1000 uv run uvicorn app.main:app --port 8000`.
- `roles.e2e.ts` (Phase 3.2-E) : entreprise créée par la CLI ; un utilisateur « RH » (rôle
  personnalisé) ne se voit proposer que les permissions qu'il détient, les rôles de base hors de
  son périmètre sont signalés ; refus serveur d'une création de rôle forcée par l'API.
- `users.e2e.ts` (Phase 3.2-D) : crée par la CLI (`../backend` ou `E2E_BACKEND_DIR`) des
  entreprises à chaque exécution ; ajout d'un utilisateur (rôle, site), compte global réutilisé
  (un même compte dans deux entreprises), identité en lecture seule, désactivation limitée à
  l'entreprise (l'autre reste active), refus d'un rôle non délégable, mobile.
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
