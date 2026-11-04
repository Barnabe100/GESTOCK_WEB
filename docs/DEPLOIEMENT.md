# Déploiement en production — StockManager Web

Guide d'exploitation de TechNova. **Aucun domaine, mot de passe, secret ni clé n'est fourni par
ce dépôt** : chaque valeur `<…>` est à décider et à fournir par l'exploitant. Les fichiers cités
existent dans le dépôt ; tout ce qui n'y est pas (hôte, TLS, gestionnaire de secrets, copie hors
site) relève d'une décision d'exploitation, signalée comme telle.

Références : [`ARCHITECTURE.md`](architecture/ARCHITECTURE.md) §11,
[`LICENSING.md`](architecture/LICENSING.md), [`TECHNOVA_CONSOLE.md`](architecture/TECHNOVA_CONSOLE.md),
[`exploitation/SAUVEGARDE_RESTAURATION.md`](exploitation/SAUVEGARDE_RESTAURATION.md),
[`exploitation/SECURITE.md`](exploitation/SECURITE.md).

## 1. Architecture de production

```text
Internet ──TLS──► mandataire / équilibreur (HTTPS, certificats) ──► web:8080 (nginx, statique)
                                                                     └─ /api/ ──► api:8000
Réseau d'administration / VPN ──TLS──► web:8081 (console /tech-admin) └─ /platform-api/ ──► console:8001
Réseau privé : api, console ──► PostgreSQL 16 (aucun port public)
               console ──► Signing Service (hôte séparé, ADR-0034)
Sauvegardes : archive WAL + sauvegardes de base sur un AUTRE disque, copie hors site
```

| Élément | Fichier du dépôt |
|---|---|
| Image backend (API, console, CLI ; non root, sans outils de dév.) | `docker/backend/Dockerfile.prod` |
| Image frontend (nginx non root, en-têtes de sécurité, ports 8080 / 8081) | `docker/frontend/Dockerfile.prod`, `docker/nginx/stockmanager.conf.template` |
| Exemple d'orchestration | `docker-compose.prod.example.yml` (ne remplace pas `docker-compose.yml`, réservé au développement) |
| Rôles SQL (propriétaire, applicatif, console, purge ; aucun `BYPASSRLS`) | `docker/postgres/init/*.sh` |
| Sauvegarde / restauration | `ops/backup/` |
| Supervision | `ops/monitoring/sm-healthcheck.sh` |
| Tâches planifiées | `ops/cron/stockmanager.cron` |

Les images sont construites et contrôlées par la CI (job « Images de production » : utilisateur
non root, refus de démarrer sans secrets, configuration nginx valide, aucune clé privée).

## 2. Configuration et secrets

Fichier d'environnement **hors dépôt** (ex. `/etc/stockmanager/.env.production`, `0600`,
propriétaire root) ou gestionnaire de secrets. Le démarrage en `SM_ENVIRONMENT=production`
**échoue explicitement** si un secret obligatoire manque (contrôle de `app/core/config.py`).

| Variable | Obligatoire | Rôle |
|---|---|---|
| `SM_ENVIRONMENT=production` | oui (défaut de l'image) | active les contrôles de production |
| `SM_DATABASE_URL` | oui | rôle applicatif `stockmanager_app` (RLS) |
| `SM_MIGRATION_DATABASE_URL` | oui (migrations, CLI) | rôle propriétaire — jamais utilisé par l'API |
| `SM_PLATFORM_DATABASE_URL` | oui (console, rappels) | rôle `stockmanager_platform` |
| `SM_PURGE_DATABASE_URL` | oui (purge) | rôle `stockmanager_purge` |
| `SM_JWT_SECRET` | oui, ≥ 32 caractères | signature des jetons |
| `SM_RATE_LIMIT_HMAC_KEY` | oui, ≥ 32 caractères | empreintes de limitation de fréquence |
| `SM_CORS_ORIGINS` | oui | origine(s) HTTPS de l'application |
| `SM_REFRESH_COOKIE_SECURE`, `SM_PLATFORM_COOKIE_SECURE` | `true` | cookies `Secure` (HTTPS) |
| `SM_PUBLIC_APP_URL`, `SM_PUBLIC_ALLOWED_HOSTS` | oui (QR) | adresse publique construite par le serveur, contrôlée |
| `SM_TRUSTED_PROXIES`, `FORWARDED_ALLOW_IPS` | oui | adresses du mandataire (IP du client fiable) |
| `SM_SIGNING_SERVICE_URL`, `SM_SIGNING_CLIENT_SECRET` (≥ 32) | console | Signing Service (§4) |
| `SM_LICENSE_PUBLIC_KEYS_FILE` | facultatif | trousseau public (défaut : fichier versionné) |
| `SM_EXPORT_MAX_ROWS`, `SM_RENEWAL_NOTICE_DAYS`, `SM_QR_*`, `WEB_CONCURRENCY` | facultatifs | réglages |

Générer chaque secret sur l'hôte (ex. `openssl rand -base64 48`), jamais par copie d'un exemple.
Mots de passe des rôles SQL : distincts, aléatoires, fournis à `docker/postgres/init` au premier
démarrage puis changés par `ALTER ROLE` si besoin.

## 3. Première installation

1. Hôte : Docker (ou équivalent), disque des données et **disque distinct** pour les
   sauvegardes ; horloge synchronisée (NTP : les demandes de signature sont horodatées).
2. Mandataire TLS (décision d'exploitation : Caddy, nginx, équilibreur du fournisseur) :
   certificats, redirection HTTP → HTTPS, transmission de `X-Forwarded-Proto` et
   `X-Forwarded-For` ; la console (port 8081) n'est publiée que sur le réseau d'administration.
3. Images : construites par la CI ou `docker build -f docker/backend/Dockerfile.prod …` et
   `docker build -f docker/frontend/Dockerfile.prod …`, étiquetées par version (jamais `latest`).
4. `docker compose -f docker-compose.prod.example.yml --env-file … up -d` : la base démarre
   (rôles créés, archivage WAL actif), `migrate` applique `alembic upgrade head` et
   `stockmanager catalog sync`, puis l'API, la console et le frontal démarrent.
5. Compte TechNova : `stockmanager platform-admin create --email <…> --name "<…>"` (CLI seule).
6. Signing Service (§4), puis `stockmanager signing check` → doit réussir.
7. Sauvegardes (§5) : première sauvegarde de base, `sm-healthcheck.sh` vert, **exercice de
   restauration** réussi.
8. Contrôles finaux : [`exploitation/CHECKLISTS.md`](exploitation/CHECKLISTS.md) « Avant le
   premier client ».

## 4. Clés de signature des licences (production) et rotation

Règles (ADR-0034, mission §3.3) : la clé privée sert **uniquement** à signer et ne quitte jamais
l'hôte du Signing Service — jamais dans ce dépôt, une image, le frontend, les journaux, la base,
la CI. **Aucune clé n'est générée au démarrage** : sans clé montée, le service refuse de démarrer.

### 4.1 Mise en place

1. Hôte dédié (ou conteneur isolé) du Signing Service, joignable par la console seule (réseau
   privé ; pare-feu limitant l'origine à la console).
2. Sur cet hôte, par une personne habilitée de TechNova :
   ```bash
   cd signing-service && uv sync --frozen
   install -d -m 0700 /srv/signing
   uv run signing-service-keygen --key-id technova-ed25519-<AAAA>-<NN> --out /srv/signing/private.pem
   ```
   La commande écrit la clé privée (`0600`) et affiche le bloc **public** du trousseau.
3. Secret HMAC partagé avec la console : `openssl rand -hex 48 > /srv/signing/client-secret`
   (`0600`) ; la même valeur dans `SM_SIGNING_CLIENT_SECRET` (gestionnaire de secrets).
4. Clé publique : ajoutée au trousseau versionné
   `backend/app/platform/licensing/data/public_keys.toml` avec `status = "active"`, par une
   modification relue (pull request) ; nouvelle version déployée.
5. Service : `SIGNING_KEY_ID`, `SIGNING_PRIVATE_KEY_FILE` (montage lecture seule),
   `SIGNING_CLIENT_SECRET_FILE`, `SIGNING_MAX_SKEW_SECONDS` ; démarrage
   (`signing-service/README.md`).
6. `stockmanager signing check` (configuration de la console) : signature réelle d'une sonde et
   vérification par le trousseau. Échec = ne pas ouvrir la génération de licences.
7. Sauvegarde de la clé privée : copie chiffrée hors ligne (coffre), accès nominatif ; sa perte
   n'invalide pas les licences émises (vérifiées par la clé publique) mais impose une rotation.

### 4.2 Rotation (périodique ou sur incident)

1. Générer une nouvelle paire (nouvel identifiant `key_id`) sur l'hôte du service.
2. Ajouter la clé publique au trousseau (`active`) ; passer l'ancienne en `retired` (les licences
   déjà émises restent vérifiables) — en cas de **compromission**, décider avec TechNova du
   retrait et de la réémission des licences concernées (ADR-0034).
3. Déployer le trousseau, redémarrer le service avec la nouvelle clé et le nouveau
   `SIGNING_KEY_ID`, puis `stockmanager signing check`.
4. Détruire l'ancienne clé privée selon la procédure du coffre ; tracer l'opération.

## 5. Sauvegardes, tâches planifiées, supervision

- Sauvegardes (R11-0, ADR-0057) : archivage WAL continu vérifié, sauvegarde complète
  hebdomadaire, vérification quotidienne, test réel de restauration mensuel, rétention 30 jours,
  copie externe Restic préparée — [`exploitation/SAUVEGARDE_RESTAURATION.md`](exploitation/SAUVEGARDE_RESTAURATION.md) ;
  activation pas à pas : [`exploitation/ACTIVATION_SAUVEGARDES.md`](exploitation/ACTIVATION_SAUVEGARDES.md).
- Tâches : sauvegardes en UTC par `ops/systemd/` (ou `ops/cron/stockmanager-backup.cron`) ;
  rappels d'échéance et purge : `ops/cron/stockmanager.cron`.
- Supervision : `ops/backup/sm-backup-health.sh` (sauvegardes : base inactive ≠ retard ≠ échec,
  seuils 8 j / 26 h / 35 j / 15 min) et `ops/monitoring/sm-healthcheck.sh` (API prête, Signing
  Service, sauvegardes via `SM_BACKUP_HEALTH_CMD`) ; codes 0 / 1 / 2 ; page « Sauvegardes » de
  la console.
- Journaux : sortie standard des conteneurs (aucun secret journalisé) ; rotation par le moteur
  de conteneurs ; conservation selon la politique de TechNova (décision d'exploitation).

## 6. Mise à jour d'une version

1. Lire les notes de version (migrations, variables nouvelles, guide `MISE_A_JOUR_*.md`).
2. Sauvegarde complète immédiate (`sm-base-backup.sh`), vérification (`sm-backup-verify.sh`) et
   archivage sans échec (`sm-backup-health.sh`).
3. Déployer la nouvelle étiquette d'image : `migrate` s'exécute d'abord (une migration échouée
   arrête le déploiement : l'API n'est pas redémarrée), puis l'API, la console, le frontal.
4. Contrôles : `/api/v1/health/ready`, `stockmanager signing check`, parcours de recette rapide.

## 7. Retour arrière

- **Code sans migration** : redéployer l'étiquette précédente.
- **Avec migrations** : les descentes sont gardées (refusées dès qu'une donnée nouvelle
  existe) ; ne jamais forcer. Procédure : arrêter l'API, restaurer à l'instant précédant le
  déploiement (PITR) dans un répertoire **neuf**, vérifier, basculer, redéployer l'étiquette
  précédente. Les écritures faites depuis le déploiement sont perdues : décision explicite de
  TechNova, client informé.
- Ne jamais exécuter `docker compose down -v` ni supprimer un volume de données.

## 8. Ce qui n'est pas fourni par le dépôt (décisions d'exploitation)

Nom de domaine et certificats ; hébergeur ; gestionnaire de secrets ; outil de copie hors site
des sauvegardes ; outil de supervision et d'alerte ; politique de conservation des journaux ;
hôte du Signing Service ; paiement en ligne (aucun agrégateur intégré, décision explicite
requise).
