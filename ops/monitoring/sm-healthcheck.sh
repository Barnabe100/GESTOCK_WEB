#!/usr/bin/env bash
# Supervision de StockManager Web — à lancer toutes les 5 minutes (cron, sonde de supervision).
#
#   SM_API_URL=https://app.<domaine>  SM_BACKUP_DIR=/srv/backups/stockmanager \
#   PGHOST=… PGUSER=<rôle de supervision> PGDATABASE=stockmanager  sm-healthcheck.sh
#
# Une ligne par contrôle (OK / AVERTISSEMENT / CRITIQUE) ; code de sortie : 0 tout est OK,
# 1 avertissement, 2 critique (convention des outils de supervision). Aucun secret affiché.
# Contrôle non configuré : « IGNORÉ », compté comme un avertissement (jamais comme OK).
#
# Variables : SM_API_URL ; SM_BACKUP_HEALTH_CMD ou SM_BACKUP_DIR (sauvegardes : seuils
# SM_BACKUP_* de ops/backup/sm-backup-health.sh) ; SM_CHECK_SIGNING=1 (stockmanager signing
# check, depuis le répertoire backend/ ou l'image).
set -uo pipefail

STATUS=0
report() { # niveau message
  echo "$1 $2"
  case "$1" in
    CRITIQUE) STATUS=2 ;;
    AVERTISSEMENT | IGNORÉ) (( STATUS < 1 )) && STATUS=1 ;;
  esac
}

# 1. API (prête : base joignable)
if [[ -n "${SM_API_URL:-}" ]]; then
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SM_API_URL/api/v1/health/ready")"
  [[ "$code" == 200 ]] && report OK "api : prête" || report CRITIQUE "api : /health/ready → $code"
else
  report IGNORÉ "api : SM_API_URL non défini"
fi

# 2. Sauvegardes : archivage WAL (inactif ≠ retard ≠ échec), sauvegarde complète, vérification,
#    test de restauration, stockage, copie externe — ops/backup/sm-backup-health.sh, exécuté là
#    où se trouvent la base et l'archive (Docker : SM_BACKUP_HEALTH_CMD, voir l'exemple).
#    SM_BACKUP_HEALTH_CMD="docker compose -f … -f docker-compose.backup.yml exec -T -u postgres db
#                          /opt/stockmanager/backup/sm-backup-health.sh"
backup_health() {
  local out code=0
  if [[ -n "${SM_BACKUP_HEALTH_CMD:-}" ]]; then
    read -r -a cmd <<<"$SM_BACKUP_HEALTH_CMD"
    out="$(timeout 120 "${cmd[@]}" 2>&1)" || code=$?
  elif [[ -n "${SM_BACKUP_DIR:-}" ]]; then
    out="$(timeout 120 "$(dirname "$(realpath "$0")")/../backup/sm-backup-health.sh" 2>&1)" || code=$?
  else
    report IGNORÉ "sauvegardes : SM_BACKUP_HEALTH_CMD et SM_BACKUP_DIR non définis"
    return
  fi
  while IFS= read -r line; do [[ -n "$line" ]] && echo "  $line"; done <<<"$out"
  case "$code" in
    0) report OK "sauvegardes : conformes" ;;
    1) report AVERTISSEMENT "sauvegardes : point(s) à surveiller (détail ci-dessus)" ;;
    *) report CRITIQUE "sauvegardes : alerte (code $code, détail ci-dessus)" ;;
  esac
}
backup_health

# 4. Signing Service (signature réelle d'une sonde, ADR-0034)
if [[ "${SM_CHECK_SIGNING:-0}" == 1 ]]; then
  if out="$(timeout 60 stockmanager signing check 2>&1)"; then report OK "signature : ${out%%(*}"
  else report CRITIQUE "signature : ${out##*Erreur : }"; fi
else
  report IGNORÉ "signature : SM_CHECK_SIGNING≠1"
fi

exit "$STATUS"
