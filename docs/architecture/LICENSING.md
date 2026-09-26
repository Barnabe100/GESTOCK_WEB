# Licences des sites (Phase 3.3-B)

Référence : [ADR-0033](../adr/0033-abonnement-par-site.md) (1 site = 1 abonnement),
[ADR-0034](../adr/0034-licences-et-signing-service.md) (licences et Signing Service),
[ADR-0032](../adr/0032-paiements-abonnement.md) (paiements d'abonnement).

```text
TENANT ── SITE A ── SUBSCRIPTION A ── PAYMENT (CONFIRMED) ── LICENCE A (.lic signé)
       └─ SITE B ── SUBSCRIPTION B ── PAYMENT (CONFIRMED) ── LICENCE B
```

État de la phase : **B1** ✅ abonnement par site · **B2** ✅ licences, Signing Service, console
· B3 postes (activations) · B4 renouvellement et notifications.

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
  cours (renouvellement contigu), sinon aujourd'hui ; fin = début + période de facturation − 1
  jour (01/10/2026 annuel → 30/09/2027). Une période manuelle transitoire (3.2-G) en cours est
  remplacée par la première licence.
- **Postes** (`max_activations`) : proposé = `requested_activations` de l'abonnement (défaut 1),
  confirmé ou ajusté par TechNova, figé. Consommé par les activations de postes (3.3-B3).
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

## 7. Sécurité et tests

- RLS `ENABLE` + `FORCE` ; rôle applicatif `SELECT` seulement ; rôle de la console : lecture,
  `INSERT` `ISSUED`, révocation seulement ; déclencheur d'immutabilité (même pour le
  propriétaire du schéma) ; FK composites (site, abonnement, paiement du même tenant).
- Tests : `backend/tests/test_licensing_crypto.py` (canonique, vérification, falsification,
  clés inconnues / retirées, trousseau, client HMAC, aucune clé privée versionnée),
  `backend/tests/test_licenses.py` (génération, concurrence, échecs de signature, renouvellement,
  conditions figées, révocation, réémission, filtres, RLS et droits SQL, immutabilité),
  `signing-service/tests` (signature, payloads refusés, HMAC, rejeu, horodatage, démarrage,
  journaux sans secret, génération de clés). Clés **éphémères** uniquement.
