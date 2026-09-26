# StockManager — Signing Service

Service de signature des licences StockManager (Phase 3.3-B2,
[ADR-0034](../docs/adr/0034-licences-et-signing-service.md),
[`LICENSING.md`](../docs/architecture/LICENSING.md)).

**Déployé à part** de StockManager Web : ni dans le `docker-compose.yml` de l'application, ni
dans ses images, ni sur le même hôte de préférence. Seule la console TechNova l'appelle, sur un
réseau privé.

## Ce qu'il fait — et ce qu'il ne fait pas

| Fait | Ne fait jamais |
| --- | --- |
| Signe (Ed25519) un payload de licence **bien formé** soumis par la console | Lire les données des tenants, accéder à PostgreSQL |
| Vérifie l'authenticité de la demande (HMAC, horodatage, nonce) | Décider du plan, du prix, du quota ou de la période |
| Refuse toute clé inconnue, tout flottant, toute structure inattendue | Confirmer un paiement, modifier un abonnement |
| Journalise `license_id`, `key_id`, empreinte SHA-256 du payload | Journaliser la clé, le secret, le payload ou la signature |

## La clé privée

La clé privée Ed25519 appartient **exclusivement** à ce service. Elle n'est **jamais** dans :
le dépôt GESTOCK_WEB, Git, GitHub, GitHub Actions, l'API FastAPI principale, la console
TechNova, React, PostgreSQL, le Docker Compose ou les images de l'application, un fichier de
configuration versionné, un test permanent (les tests utilisent des **clés éphémères**).

Elle est lue au démarrage depuis un **fichier monté** (`SIGNING_PRIVATE_KEY_FILE`, PEM PKCS#8),
jamais depuis une variable d'environnement. Le `.gitignore` du dépôt refuse `*.pem`, `*.key`,
`*.p8` et `signing-service/secrets/`.

### Générer une paire de clés (sur l'hôte du service)

```bash
uv run signing-service-keygen --key-id technova-ed25519-2026-01 --out /srv/signing/private.pem
```

- refuse d'écrire dans un dépôt Git et n'écrase jamais un fichier existant ;
- écrit la clé privée en `0600` et n'affiche que l'**entrée publique** à ajouter au trousseau
  versionné du backend (`backend/app/platform/licensing/data/public_keys.toml`).

### Rotation

1. Générer une nouvelle paire avec un nouveau `key_id` (ex. `technova-ed25519-2027-01`).
2. Ajouter la clé publique au trousseau (`status = "active"`), passer l'ancienne en
   `status = "retired"` : les licences déjà signées restent vérifiables, les nouvelles ne
   peuvent plus l'être avec l'ancienne clé.
3. Redémarrer le service avec la nouvelle clé et le nouveau `SIGNING_KEY_ID`.

## Configuration

| Variable | Rôle |
| --- | --- |
| `SIGNING_KEY_ID` | Identifiant public de la clé, repris dans chaque licence |
| `SIGNING_PRIVATE_KEY_FILE` | Fichier de la clé privée (monté, lecture seule) |
| `SIGNING_CLIENT_SECRET_FILE` | Secret HMAC partagé avec la console (≥ 32 octets) |
| `SIGNING_MAX_SKEW_SECONDS` | Écart d'horloge toléré (défaut 300 s) |

Côté console : `SM_SIGNING_SERVICE_URL` et `SM_SIGNING_CLIENT_SECRET` (le même secret HMAC).

## API

- `GET /health` → `{"status": "ok", "key_id": "…"}`.
- `POST /v1/sign` — en-têtes `X-Signing-Timestamp` (Unix, s), `X-Signing-Nonce` (16–64
  caractères `[A-Za-z0-9_-]`), `X-Signing-Signature` = HMAC-SHA256 hexadécimal de
  `POST\n/v1/sign\n<timestamp>\n<nonce>\n<sha256 hex du corps>` ; corps
  `{"payload": {…}}` → document `.lic` v1 signé. `401` (authentification), `413`, `422`
  (`invalid_payload`, champs en cause).

Le cache anti-rejeu des nonces est en mémoire : **une instance** du service.

## Développement

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy signing_service
# Démarrage local avec une clé éphémère (hors dépôt) :
uv run signing-service-keygen --key-id dev-ed25519-local --out /tmp/sm-signing/private.pem
head -c 48 /dev/urandom | base64 > /tmp/sm-signing/client-secret && chmod 600 /tmp/sm-signing/*
SIGNING_KEY_ID=dev-ed25519-local SIGNING_PRIVATE_KEY_FILE=/tmp/sm-signing/private.pem \
SIGNING_CLIENT_SECRET_FILE=/tmp/sm-signing/client-secret \
  uv run uvicorn --factory signing_service.app:create_app --port 8100
```
