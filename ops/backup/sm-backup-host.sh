#!/usr/bin/env bash
# Lanceur côté HÔTE des tâches de sauvegarde (unités systemd ou cron) — Docker Compose.
#
#   sm-backup-host.sh agent|weekly|verify|restore-test|health|prune-dry-run|offsite
#
#   agent : chaque minute (stockmanager-backup-agent.timer) — exécute ce que la console a planifié
#           ou demandé (R11, ADR-0058) ; les autres tâches restent disponibles à la main.
#
# Lit /etc/stockmanager/backup-host.env (SM_HOST_ENV_FILE) :
#   SM_COMPOSE_DIR=/chemin/du/dépôt          répertoire contenant docker-compose.yml
#   SM_COMPOSE_FILES="-f docker-compose.yml -f docker-compose.backup.yml"
#   SM_ALERT_COMMAND=…                        facultatif : appelé (niveau, tâche) si échec
# Exécute la tâche DANS le conteneur db (utilisateur postgres, socket locale, rôle de
# réplication) : aucun port, aucun mot de passe sur la ligne de commande de l'hôte.
set -euo pipefail
OP="${1:-}"
case "$OP" in agent|weekly|verify|restore-test|health|prune-dry-run|offsite) ;; *) echo "tâche inconnue : $OP" >&2; exit 2 ;; esac
ENV_FILE="${SM_HOST_ENV_FILE:-/etc/stockmanager/backup-host.env}"
if [[ -f "$ENV_FILE" ]]; then set -a; . "$ENV_FILE"; set +a; fi
: "${SM_COMPOSE_DIR:?SM_COMPOSE_DIR non défini ($ENV_FILE)}"
FILES="${SM_COMPOSE_FILES:--f docker-compose.yml -f docker-compose.backup.yml}"
cd "$SM_COMPOSE_DIR"
code=0
# shellcheck disable=SC2086
if [[ "$OP" == offsite ]]; then
  docker compose $FILES --profile offsite run --rm -T offsite || code=$?
else
  docker compose $FILES exec -T -u postgres db /opt/stockmanager/backup/sm-backup-run.sh "$OP" || code=$?
fi
if (( code != 0 )) && [[ -n "${SM_ALERT_COMMAND:-}" ]]; then
  level=CRITIQUE; [[ "$OP" == health && $code == 1 ]] && level=AVERTISSEMENT
  "$SM_ALERT_COMMAND" "$level" "stockmanager-backup $OP (code $code)" || true
fi
exit "$code"
