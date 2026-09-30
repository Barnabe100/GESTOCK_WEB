# Licences des sites (Phase 3.3-B)

Référence : [ADR-0033](../adr/0033-abonnement-par-site.md) (1 site = 1 abonnement),
[ADR-0034](../adr/0034-licences-et-signing-service.md) (licences et Signing Service),
[ADR-0032](../adr/0032-paiements-abonnement.md) (paiements d'abonnement).

```text
TENANT ── SITE A ── SUBSCRIPTION A ── PAYMENT (CONFIRMED) ── LICENCE A (.lic signé)
       └─ SITE B ── SUBSCRIPTION B ── PAYMENT (CONFIRMED) ── LICENCE B
```

État de la phase : **B1** ✅ abonnement par site · **B2** ✅ licences, Signing Service, console
· **B3** ✅ postes (activations, [ADR-0035](../adr/0035-postes-activations.md)) · **B4** ✅
renouvellement et rappels d'échéance ([ADR-0036](../adr/0036-renouvellement-et-notifications.md),
§ 9).

## 1. Acteurs et frontières

| Composant | Rôle | Ne fait jamais |
| --- | --- | --- |
| Entreprise (API `/api/v1`) | Déclare ses paiements, **lit** ses licences (page Abonnement) | Générer une licence, modifier droits / quota / validité |
| Console TechNova (`/platform-api/v1`) | Confirme les paiements, **génère**, révoque, réémet, télécharge les licences | Détenir la clé privée, signer |
| Signing Service (`signing-service/`) | **Signe** un payload bien formé (Ed25519) | Lire une base, décider plan / prix / quota, confirmer un paiement, modifier un abonnement |
| PostgreSQL | Stocke la licence signée (payload + signature) | Contenir la clé privée |

La clé privée n'existe **que** dans le processus du Signing Service (fichier monté). Voir
[`signing-service/README.md`](../../signing-service/README.md) (génération, rotation).

## 2. Cycle de vie

```text
PAYMENT CONFIRMED ──génération──▶ ISSUED ──révocation──▶ REVOKED (définitif)
                                     │
                                     └──réémission──▶ REVOKED  +  nouvelle licence ISSUED
                                                                  (version + 1, même période)
État affiché (calculé) : NOT_YET_VALID → ACTIVE → EXPIRED ; REVOKED.
```

- **Génération** : paiement `CONFIRMED` exigé (`409 payment_not_confirmed`), abonnement rattaché
  à un site, entreprise active, une licence au plus par paiement (`409 license_already_issued`).
- **Période** : jours inclus du fuseau de l'entreprise. Début = lendemain de la couverture en
  cours (renouvellement contigu) ; sans couverture, lendemain de la dernière licence si elle est
  échue depuis au plus le délai de grâce (continuité, 3.3-B4), sinon aujourd'hui (jamais
  rétroactif) ; fin = début + période de facturation − 1 jour (01/10/2026 annuel →
  30/09/2027). Une période manuelle transitoire (3.2-G) en cours est remplacée par la première
  licence.
- **Postes** (`max_activations`) : proposé = demande explicite de l'entreprise avec le paiement,
  sinon postes de la licence de référence du site, sinon (première licence)
  `requested_activations` de l'abonnement (défaut 1) ; confirmé ou ajusté par TechNova, figé.
  Consommé par les activations de postes (3.3-B3).
- **Révocation** : définitive ; sans autre licence couvrant ce jour, l'abonnement du site passe
  `suspended` (régularisation seulement), les données sont conservées.
- **Réémission** : révoque l'ancienne (si besoin) et émet une nouvelle licence (nouveau numéro,
  `supersedes_id`) pour le même paiement et la même période ; postes conservés sauf changement
  explicite ; conditions du plan actuel de l'abonnement.

## 3. Effets sur l'abonnement et les capacités

- L'abonnement du site suit ses licences : `active`, `current_period_start` = début de la
  licence en vigueur, `current_period_end` = minuit local du lendemain de la fin de la
  couverture. Échéance, grâce et politiques d'accès : inchangées (statut effectif calculé).
- **Conditions en vigueur** (`PlanTerms`) : celles de la licence en vigueur (modules,
  fonctionnalités, limites figés), sinon celles du plan. Un changement de plan vaut à la
  licence suivante.
- Activation manuelle et prolongation transitoires (console, 3.2-G) : refusées dès qu'une
  licence existe (`409 subscription_license_controlled`).

## 4. Format `.lic` v1

```json
{
  "format": "stockmanager-license",
  "version": 1,
  "key_id": "technova-ed25519-2026-01",
  "payload": {
    "license_id": "…", "license_number": "LIC-2026-00001", "license_version": 1,
    "supersedes_id": null, "tenant_id": "…", "site_id": "…", "subscription_id": "…",
    "payment_id": "…", "plan": "STANDARD", "billing_period": "annual",
    "issued_at": "2026-09-26T10:00:00Z", "timezone": "Africa/Ouagadougou",
    "valid_from": "2026-10-01", "valid_until": "2027-09-30", "max_activations": 3,
    "modules": ["catalog", "sales", "…"], "features": [], "limits": {"max_users": 5},
    "compatibility": {"products": ["stockmanager-desktop", "stockmanager-web"]}
  },
  "signature": "<Ed25519, base64>"
}
```

Octets signés : forme canonique du document **sans** `signature` — JSON, clés triées,
séparateurs `,` et `:` sans espace, UTF-8, aucun flottant (`canonical_bytes`, identique dans
`backend/app/platform/licensing/canonical.py` et `signing-service/signing_service/canonical.py`).
Vérification : `app.platform.licensing.keyring.verify_document` avec le trousseau public.

## 5. Signing Service

- `POST /v1/sign` : corps `{"payload": {…}}`, en-têtes `X-Signing-Timestamp`,
  `X-Signing-Nonce`, `X-Signing-Signature` = HMAC-SHA256 de
  `POST\n/v1/sign\n<timestamp>\n<nonce>\n<sha256 hex du corps>` ; `GET /health`.
- Configuration du service : `SIGNING_KEY_ID`, `SIGNING_PRIVATE_KEY_FILE`,
  `SIGNING_CLIENT_SECRET_FILE`, `SIGNING_MAX_SKEW_SECONDS`.
- Configuration de la console : `SM_SIGNING_SERVICE_URL`, `SM_SIGNING_CLIENT_SECRET`,
  `SM_SIGNING_TIMEOUT_SECONDS`, `SM_LICENSE_PUBLIC_KEYS_FILE` (trousseau public ; défaut : le
  fichier versionné).
- Erreurs vues par la console : `503 signing_service_unavailable` (injoignable ou non
  configuré), `502 signing_failed` (refus), `502 license_signature_invalid` /
  `license_key_unknown` / `license_key_retired` (réponse non vérifiable). Dans tous les cas,
  **rien** n'est enregistré.

## 6. API

| Surface | Route | Rôle |
| --- | --- | --- |
| Entreprise | `GET /subscriptions` → `license` | Licence en vigueur du site (sinon la plus récente), lecture seule |
| Console | `GET /licenses` | Liste (filtres `tenant_id`, `site_id`, `plan_code`, `state`, `search`) |
| Console | `GET /licenses/{id}` · `GET /licenses/{id}/file` | Fiche · fichier `.lic` (refusé si révoquée) |
| Console | `GET /payments/{id}/license-proposal` | Période, postes proposés, blocage éventuel |
| Console | `POST /payments/{id}/license` | Génération (`reason`, `max_activations`) |
| Console | `POST /licenses/{id}/revoke` · `POST /licenses/{id}/reissue` | Révocation · réémission (`reason`, `max_activations` facultatif) |
| Entreprise | `GET /license-activations` | Postes des sites visibles (`site_id`, `status`) |
| Installation | `POST /license-activations` · `POST /license-activations/check-in` | Activation (site sélectionné) · contrôle de présence |
| Entreprise | `POST /license-activations/{id}/release` | Libération (`reason`) |
| Console | `GET /activations` · `POST /activations/{id}/release` | Postes (filtres entreprise, abonnement, site, statut) · libération par TechNova |
| Entreprise | `GET /subscriptions` → `renewal`, `effective_plan`, `next_plan` · `GET /subscriptions/{id}/renewal-quote` | Devis de la prochaine période (3.3-B4) |
| Entreprise | `GET /notifications` · `GET /notifications/unread-count` · `POST /notifications/{id}/read` · `POST /notifications/read-all` | Rappels d'échéance (3.3-B4) |

## 7. Postes (activations, Phase 3.3-B3, ADR-0035)

```text
Installation (Desktop) ── POST /license-activations (X-Site-Id) ──▶ poste ACTIVE
      │                     quota = max_activations de la licence en vigueur du site
      └── POST /license-activations/check-in ──▶ présence, licence en vigueur, hors ligne toléré
Entreprise / TechNova ── libération (raison) ──▶ RELEASED : une place, rien d'autre
```

- `installation_id` : UUID aléatoire de l'installation (jamais MAC, processeur, IP) ; active
  sur un seul site à la fois (`409 installation_active_elsewhere`).
- Quota par abonnement de site, sous verrou de l'abonnement ; idempotence (même installation,
  même site : `200`, même poste).
- Refus distincts et journalisés : `license_missing`, `license_expired`, `license_revoked`,
  `license_not_yet_valid`, `license_invalid`, `license_wrong_site`, `license_superseded`,
  `activation_quota_reached` (« Le nombre maximal de postes autorisés pour ce site est
  atteint. »), `installation_active_elsewhere`.
- Libérer ne suspend pas la licence, n'ajoute ni ne retire de jours ; réactiver réutilise la
  licence et la période. Quota réduit par réémission : postes existants tolérés, nouveaux
  refusés.
- Poste non vu depuis plus de `SM_ACTIVATION_OFFLINE_GRACE_DAYS` jours (défaut 7) : signalé,
  jamais libéré automatiquement.
- Le Web n'active jamais de navigateur : la page Abonnement affiche « 3 postes autorisés · 2
  utilisés · 1 disponible », les postes et la libération.

## 8. Renouvellement et rappels d'échéance (Phase 3.3-B4, ADR-0036)

```text
Entreprise ── « Renouveler » (site) ── devis serveur : période, postes, montant
      │         (GET /subscriptions/{id}/renewal-quote ; seule demande possible : autre nombre
      │          de postes, explicite)
      └── POST /subscription/payments (sans période ; montant seulement sans tarif)
TechNova ── confirme ── génère la licence suivante (lendemain de la licence en cours)
Job quotidien ── stockmanager notifications run ── rappels J-30 … J+7 (idempotents)
```

- **R1** postes reconduits (licence de référence) ; changement seulement explicite, confirmé
  ou ajusté par TechNova. Postes actifs jamais libérés par un renouvellement ni par
  l'expiration ; quota réduit toléré (nouvelles activations refusées).
- **R2** renouvellement pendant la grâce : la nouvelle période suit la licence échue ; au-delà,
  elle commence le jour de la génération.
- **R3** période, postes et montant calculés par le serveur (`amount_computed_by_server`,
  `amount_required`) ; le client ne fournit jamais la période.
- **R4** page Abonnement : « Offre en vigueur » (licence en vigueur) et « Au prochain
  renouvellement » ; les droits restent ceux de la licence en vigueur, sans job de bascule.
- **Tarif** : prix du premier poste + (postes − 1) × prix d'un poste supplémentaire (mensuel /
  annuel) ; formule fixe, paramètres commerciaux de TechNova figés sur l'abonnement du site.
- **Fichier `.lic`** : jamais téléchargé par l'entreprise dans le Web (la plateforme applique
  la licence) ; destiné au Desktop, traité séparément ; seule la console le télécharge.
- **Rappels** : étapes `SM_RENEWAL_NOTICE_DAYS` (défaut `30,15,10,5,1,0,-1,-7`), essais J-5 /
  J-1 / J0 ; `active`, `past_due`, `expired`, `trial` ; jamais `pending_activation`,
  `suspended`, `cancelled` ni entreprise suspendue ; unicité (abonnement, étape, échéance) ;
  job manqué : seule l'étape la plus récente est envoyée (les autres `SKIPPED`) ; verrou
  consultatif (pas d'exécutions simultanées). Cron d'exemple :
  `15 6 * * * cd /srv/stockmanager/backend && uv run stockmanager notifications run`.
- **Révocation** : aucune substitution automatique par une licence déjà payée (ADR-0034) ;
  réémission explicite par TechNova.

## 9. Sécurité et tests

- RLS `ENABLE` + `FORCE` ; rôle applicatif `SELECT` seulement ; rôle de la console : lecture,
  `INSERT` `ISSUED`, révocation seulement ; déclencheur d'immutabilité (même pour le
  propriétaire du schéma) ; FK composites (site, abonnement, paiement du même tenant).
- Tests : `backend/tests/test_licensing_crypto.py` (canonique, vérification, falsification,
  clés inconnues / retirées, trousseau, client HMAC, aucune clé privée versionnée),
  `backend/tests/test_licenses.py` (génération, concurrence, échecs de signature, renouvellement,
  conditions figées, révocation, réémission, filtres, RLS et droits SQL, immutabilité),
  `signing-service/tests` (signature, payloads refusés, HMAC, rejeu, horodatage, démarrage,
  journaux sans secret, génération de clés), `backend/tests/test_license_activations.py`
  (quota, concurrence, idempotence, refus distincts, libération, contrôle, permissions, RLS,
  droits SQL, finalité, console), `backend/tests/test_renewal.py` (devis, R1–R4, grâce, tarif
  figé, postes conservés / augmentés / réduits, expiration, révocation sans repli, réémission),
  `backend/tests/test_notifications.py` (chaque étape, essais, statuts exclus, idempotence,
  échéance modifiée, job manqué, concurrence, CLI, visibilité par site et par droit, lu / non
  lu, isolation, RLS). Clés **éphémères** uniquement.
