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
# Variables : SM_API_URL ; SM_BACKUP_DIR ; SM_BACKUP_MAX_AGE_HOURS (26) ;
# SM_WAL_MAX_AGE_SECONDS (900 = RPO) ; SM_DISK_MIN_FREE_PERCENT (15) ;
# SM_CHECK_SIGNING=1 (stockmanager signing check, depuis le répertoire backend/ ou l'image).
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

# 2. Base et archivage WAL (RPO)
if command -v psql >/dev/null && [[ -n "${PGHOST:-}" ]]; then
  row="$(PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-5}" timeout 20 psql -w -Atqc "SELECT failed_count, coalesce(extract(epoch FROM now() - last_archived_time)::int, -1), coalesce(extract(epoch FROM now() - last_failed_time)::int, -1) FROM pg_stat_archiver" 2>/dev/null)"
  if [[ -z "$row" ]]; then
    report CRITIQUE "base : injoignable"
  else
    IFS='|' read -r failed age last_failed <<<"$row"
    max="${SM_WAL_MAX_AGE_SECONDS:-900}"
    if (( age < 0 )); then report CRITIQUE "archivage : aucun WAL archivé"
    elif (( age > max )); then report CRITIQUE "archivage : dernier WAL il y a ${age} s (> ${max} s, RPO)"
    else report OK "archivage : dernier WAL il y a ${age} s"; fi
    if (( last_failed >= 0 && last_failed < 3600 )); then
      report AVERTISSEMENT "archivage : échec il y a ${last_failed} s (total ${failed})"
    fi
  fi
else
  report IGNORÉ "base : PGHOST non défini ou psql absent"
fi

# 3. Fraîcheur et vérification de la dernière sauvegarde de base
if [[ -n "${SM_BACKUP_DIR:-}" ]]; then
  last="$(find "$SM_BACKUP_DIR/base" -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\n' 2>/dev/null | sort | tail -n 1)"
  if [[ -z "$last" ]]; then
    report CRITIQUE "sauvegarde : aucune sauvegarde de base"
  else
    hours=$(( ( $(date -u +%s) - $(date -u -d "$(echo "$last" | sed -E 's/(....)(..)(..)T(..)(..)(..)Z/\1-\2-\3 \4:\5:\6/')" +%s) ) / 3600 ))
    if ! grep -q '"verified": true' "$SM_BACKUP_DIR/base/$last/backup_info.json" 2>/dev/null; then
      report CRITIQUE "sauvegarde : $last non vérifiée"
    elif (( hours > ${SM_BACKUP_MAX_AGE_HOURS:-26} )); then
      report CRITIQUE "sauvegarde : dernière il y a ${hours} h ($last)"
    else
      report OK "sauvegarde : $last (${hours} h)"
    fi
  fi
  free="$(df -P "$SM_BACKUP_DIR" | awk 'NR==2 {print 100 - $5}')"
  (( free < ${SM_DISK_MIN_FREE_PERCENT:-15} )) && report AVERTISSEMENT "disque des sauvegardes : ${free} % libre" || report OK "disque des sauvegardes : ${free} % libre"
else
  report IGNORÉ "sauvegarde : SM_BACKUP_DIR non défini"
fi

# 4. Signing Service (signature réelle d'une sonde, ADR-0034)
if [[ "${SM_CHECK_SIGNING:-0}" == 1 ]]; then
  if out="$(timeout 60 stockmanager signing check 2>&1)"; then report OK "signature : ${out%%(*}"
  else report CRITIQUE "signature : ${out##*Erreur : }"; fi
else
  report IGNORÉ "signature : SM_CHECK_SIGNING≠1"
fi

exit "$STATUS"
