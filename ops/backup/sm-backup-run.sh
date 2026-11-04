#!/usr/bin/env bash
# Point d'entrée des tâches planifiées (dans le conteneur db, utilisateur postgres).
#
#   sm-backup-run.sh agent | weekly | verify | restore-test | health | prune-dry-run
#
#   agent         agent des sauvegardes (R11) : planification et demandes de la console
#   weekly        sauvegarde complète vérifiée PUIS rétention appliquée (--apply) si elle a réussi
#   verify        vérification quotidienne (complète des WAL le jour SM_BACKUP_VERIFY_FULL_WEEKDAY,
#                 1 = lundi … 7 = dimanche, défaut 7)
#   restore-test  test réel de restauration (mensuel)
#   health        supervision (écrit status/health.json)
#   prune-dry-run simulation de la rétention
# Les opérations lourdes partagent un verrou : une tâche qui en trouve une autre en cours
# l'ATTEND (SM_BACKUP_LOCK_WAIT_SECONDS, défaut 6 h) puis échoue explicitement.
set -euo pipefail
HERE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
WAIT="${SM_BACKUP_LOCK_WAIT_SECONDS:-21600}"
case "${1:-}" in
  weekly)
    "$HERE/sm-base-backup.sh" --wait "$WAIT"
    exec "$HERE/sm-backup-prune.sh" --apply --wait "$WAIT" ;;
  verify)
    if [[ "$(date -u +%u)" == "${SM_BACKUP_VERIFY_FULL_WEEKDAY:-7}" ]]; then
      exec "$HERE/sm-backup-verify.sh" --wait "$WAIT" --full
    fi
    exec "$HERE/sm-backup-verify.sh" --wait "$WAIT" ;;
  restore-test) exec "$HERE/sm-restore-test.sh" --wait "$WAIT" ;;
  agent) exec "$HERE/sm-backup-agent.sh" ;;
  health) exec "$HERE/sm-backup-health.sh" --write ;;
  prune-dry-run) exec "$HERE/sm-backup-prune.sh" --wait "$WAIT" ;;
  *) echo "usage : sm-backup-run.sh agent|weekly|verify|restore-test|health|prune-dry-run" >&2; exit 2 ;;
esac
