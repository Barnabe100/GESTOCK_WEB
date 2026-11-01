# Mise à jour vers R7 — commandes par QR Code (Ubuntu)

Guide de mise à jour d'une installation existante (développement ou recette sur Ubuntu) vers le
palier R7 ([ADR-0052](adr/0052-commandes-par-qr-code.md)). **Aucune donnée n'est supprimée** :
trois migrations additives (0056 à 0058), aucune réinitialisation.

## Règles de sécurité de la mise à jour

- Ne **jamais** exécuter `docker compose down -v`, `dropdb`, ni supprimer le volume PostgreSQL.
- Le fichier `docker-compose.yml` n'est **pas modifié** par R7 (dernière modification : R4) : une
  modification locale (port PostgreSQL `5433`, par exemple) est conservée par `git pull`.
- Les fichiers `.env` ne sont jamais versionnés : les vôtres restent en place.

## 1. Sauvegarde (recommandée)

```bash
# Adapter le port (5433) et le conteneur à votre installation.
pg_dump -h localhost -p 5433 -U stockmanager -Fc stockmanager > ~/stockmanager-avant-r7.dump
```

## 2. Récupérer le code sans écraser les modifications locales

```bash
cd ~/GESTOCK_WEB
git status                         # vos modifications locales (docker-compose.yml…) apparaissent
git fetch origin claude/nifty-ramanujan-12j5pp
git pull origin claude/nifty-ramanujan-12j5pp
git diff docker-compose.yml        # le port 5433 est toujours là
```

En cas de conflit inattendu : `git stash`, `git pull`, `git stash pop`, puis résoudre en gardant
vos valeurs locales.

## 3. Dépendances, migrations, catalogue

```bash
docker compose up -d db            # base existante, volume conservé
cd backend
uv sync                            # nouvelle dépendance : segno (QR Code SVG)
uv run alembic current             # attendu avant : 0055
uv run alembic upgrade head        # 0056 points QR, 0057 commandes QR, 0058 livraison
uv run alembic current             # attendu : 0058 (head)
uv run stockmanager catalog sync   # STANDARD reçoit restaurant.qr ; ENTREPRISE inchangée
cd ../frontend && npm install
```

La migration 0057 suppose le rôle `stockmanager_purge` (créé depuis R4 ; sinon
`docker/postgres/init/03-purge-role.sh`, à exécuter à la main sur un volume existant).

## 4. Configuration (`backend/.env`)

| Variable | Développement | Production |
|---|---|---|
| `SM_PUBLIC_APP_URL` | facultative (défaut `http://localhost:5173`) | **obligatoire**, `https://…` |
| `SM_PUBLIC_ALLOWED_HOSTS` | domaine / IP de `SM_PUBLIC_APP_URL` si elle est définie | **obligatoire** |
| `SM_RATE_LIMIT_HMAC_KEY` | facultative | **obligatoire** (≥ 32 caractères, secrète) |
| `SM_TRUSTED_PROXIES` | vide | adresse du reverse proxy (ex. `["127.0.0.1"]`) |

Tester depuis un **téléphone** du réseau local : `localhost` désigne alors le téléphone. Définir
l'adresse du poste et exposer Vite sur le réseau :

```bash
# backend/.env
SM_PUBLIC_APP_URL=http://192.168.1.20:5173
SM_PUBLIC_ALLOWED_HOSTS=["192.168.1.20"]
# frontend
npm run dev -- --host 0.0.0.0
```

Une adresse non autorisée est refusée **au démarrage** (message explicite) ; hors
développement, sans adresse configurée, la génération des QR est refusée
(`503 public_url_not_configured`). Les QR émis contiennent l'adresse en vigueur à leur émission :
après un changement d'adresse, régénérer les points.

## 5. Démarrage et activation

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd backend && uv run uvicorn app.console.main:app --reload --port 8001
cd frontend && npm run dev
```

Le module est livré **désactivé sur tous les sites existants** (migration 0058) : l'activer
explicitement, site par site (page « Modules » du site, ou
`PUT /api/v1/sites/{site_id}/modules/restaurant.qr`), sur un site dont l'offre l'inclut
(STANDARD ou ENTREPRISE). Un site STANDARD déjà licencié avant R7 n'obtient le module qu'à sa
licence suivante (modules figés par la licence en vigueur).

## 6. Tâche quotidienne de conservation (obligatoire en production)

`stockmanager notifications purge` supprime les notifications de plus de 30 jours **et** efface
le téléphone des clients des commandes QR finies depuis plus de 30 jours. Elle doit être
**planifiée, exécutée et supervisée** (voir `SUIVI_TECHNIQUE.md`, R7-1).

Minuterie systemd (exemple) :

```ini
# /etc/systemd/system/stockmanager-purge.service
[Unit]
Description=StockManager — purge de conservation (notifications, téléphones QR)
[Service]
Type=oneshot
WorkingDirectory=/opt/stockmanager/backend
EnvironmentFile=/opt/stockmanager/backend/.env
ExecStart=/usr/local/bin/uv run stockmanager notifications purge

# /etc/systemd/system/stockmanager-purge.timer
[Unit]
Description=StockManager — purge quotidienne
[Timer]
OnCalendar=*-*-* 03:17:00
Persistent=true
[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now stockmanager-purge.timer
systemctl list-timers stockmanager-purge.timer       # planifiée ?
sudo systemctl start stockmanager-purge.service      # exécution manuelle
journalctl -u stockmanager-purge.service -n 20       # compteurs affichés, code de sortie
systemctl is-failed stockmanager-purge.service       # à surveiller (alerte si « failed »)
```

Équivalent cron : `17 3 * * * cd /opt/stockmanager/backend && uv run stockmanager notifications
purge >> /var/log/stockmanager-purge.log 2>&1`, avec alerte sur un code de sortie non nul.

## 7. Production : reverse proxy

- Servir `/q` et `/q/track` avec l'en-tête `Content-Security-Policy` de la page publique et
  `frame-ancestors 'none'` (non applicable par balise), `Referrer-Policy: no-referrer`.
- Transmettre l'adresse du client dans `X-Forwarded-For` et déclarer le proxy dans
  `SM_TRUSTED_PROXIES` (jamais d'autre source pour l'adresse du client).
- Voir `ARCHITECTURE.md` §11 et `SUIVI_TECHNIQUE.md` (R7-2).

## 8. Scénarios de validation fonctionnelle

1. Activer `restaurant.tables` puis `restaurant.qr` sur un site de restauration ; créer une table.
2. Page « QR Codes » : créer un QR de table et un QR de comptoir ; vérifier l'avertissement
   « ne pourra plus être affiché », imprimer (carte A6 / affiche A4), télécharger le SVG.
3. Scanner le QR avec un téléphone : le jeton disparaît de la barre d'adresse ; menu (désignation,
   description, prix, disponibilité) ; commander ; lien de suivi « En attente de confirmation ».
4. Personnel : onglet « À confirmer » des commandes ; confirmer (la commande rejoint la cuisine)
   ou refuser avec un motif (visible par le client sur le suivi).
5. Régénérer un point : confirmation explicite ; l'ancien QR imprimé affiche « QR Code n'est pas
   disponible », le nouveau fonctionne ; le suivi des commandes déjà passées reste valable.
6. Désactiver le point, la table ou le module : refus générique côté client.
7. Réglages QR du site : plafonds (lignes, quantité par ligne, commandes en attente), nom et
   téléphone (masqué / facultatif / obligatoire) ; acceptation automatique dans les réglages des
   commandes.
8. Règlement, cuisine, prise en charge, libération : inchangés pour une commande confirmée.

## Retour arrière

Les descentes 0058 → 0055 sont refusées dès qu'un point QR, une commande QR ou un réglage QR
existe (protection des données). En cas de problème : restaurer la sauvegarde de l'étape 1
(`pg_restore`) **dans une nouvelle base**, jamais en supprimant l'existante sans sauvegarde.
