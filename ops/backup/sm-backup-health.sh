#!/usr/bin/env bash
# Supervision des sauvegardes (toutes les 5 minutes) — ligne de commande et status/health.json.
#
#   SM_BACKUP_DIR=/backups sm-backup-health.sh [--no-db] [--write]
#
# Une ligne par contrôle : OK | INFO | AVERTISSEMENT | CRITIQUE. Code de sortie : 0 (OK/INFO),
# 1 (avertissement), 2 (critique). --write : enregistre aussi status/health.json (console).
# Seuils (variables d'environnement, valeurs par défaut) :
#   SM_BACKUP_WAL_MAX_LAG_SECONDS=900         retard d'archivage toléré (RPO 15 min)
#   SM_BACKUP_BASE_MAX_AGE_HOURS=192          sauvegarde complète réussie (8 jours)
#   SM_BACKUP_VERIFY_MAX_AGE_HOURS=26         vérification quotidienne réussie
#   SM_BACKUP_RESTORE_TEST_MAX_AGE_DAYS=35    test de restauration réussi
#   SM_BACKUP_OFFSITE_MAX_AGE_HOURS=48        copie externe réussie (si configurée)
#   SM_BACKUP_DISK_WARN_FREE_PERCENT=15, SM_BACKUP_DISK_CRITICAL_FREE_PERCENT=5
#   SM_BACKUP_RUN_MAX_HOURS=24                opération « en cours » au-delà = bloquée
# Archivage : une base INACTIVE (aucun segment terminé en attente) n'est jamais en alerte, même
# si le dernier archivage est ancien ; le RETARD = des segments terminés non archivés depuis
# plus que le seuil ; un ÉCHEC non suivi d'un archivage réussi est toujours CRITIQUE.
set -uo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"
SM_OP=health
NODB=0 WRITE=0
while (( $# )); do
  case "$1" in
    --no-db) NODB=1; shift ;;
    --write) WRITE=1; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
LAG_MAX="${SM_BACKUP_WAL_MAX_LAG_SECONDS:-900}"
BASE_MAX=$(( ${SM_BACKUP_BASE_MAX_AGE_HOURS:-192} * 3600 ))
VERIFY_MAX=$(( ${SM_BACKUP_VERIFY_MAX_AGE_HOURS:-26} * 3600 ))
RESTORE_MAX=$(( ${SM_BACKUP_RESTORE_TEST_MAX_AGE_DAYS:-35} * 86400 ))
OFFSITE_MAX=$(( ${SM_BACKUP_OFFSITE_MAX_AGE_HOURS:-48} * 3600 ))
RUN_MAX=$(( ${SM_BACKUP_RUN_MAX_HOURS:-24} * 3600 ))
DISK_WARN="${SM_BACKUP_DISK_WARN_FREE_PERCENT:-15}"
DISK_CRIT="${SM_BACKUP_DISK_CRITICAL_FREE_PERCENT:-5}"
NOW="$(sm_now)"
STATUS=0 LINES=()
report() { # niveau composant message
  echo "$1 $2 : $3"
  LINES+=("{\"level\": $(sm_js "$1"), \"check\": $(sm_js "$2"), \"message\": $(sm_js "$3")}")
  case "$1" in
    CRITIQUE) STATUS=2 ;;
    AVERTISSEMENT) (( STATUS < 1 )) && STATUS=1 ;;
  esac
}
human() { local s="$1"; if (( s < 120 )); then echo "${s} s"; elif (( s < 7200 )); then echo "$(( s / 60 )) min"; elif (( s < 172800 )); then echo "$(( s / 3600 )) h"; else echo "$(( s / 86400 )) j"; fi; }

if ! SM_ROOT="$(sm_root 2>&1)"; then
  report CRITIQUE racine "$SM_ROOT"
  exit 2
fi
ST="$SM_ROOT/status"

# --- 1. Archivage WAL (instance de production) -------------------------------------------------
if (( NODB == 0 )); then
  sm_pg_env
  row="$(sm_psql -F '|' -c "SELECT current_setting('archive_mode'), current_setting('archive_command'),
      (SELECT setting FROM pg_settings WHERE name = 'archive_timeout'),
      CASE WHEN pg_is_in_recovery() THEN '' ELSE pg_walfile_name(pg_current_wal_lsn()) END,
      coalesce(last_archived_wal, ''), coalesce(extract(epoch FROM last_archived_time)::bigint, -1),
      coalesce(last_failed_wal, ''), coalesce(extract(epoch FROM last_failed_time)::bigint, -1),
      failed_count, extract(epoch FROM now())::bigint, current_setting('data_checksums'),
      (SELECT setting FROM pg_settings WHERE name = 'wal_segment_size')
    FROM pg_stat_archiver" 2>&1)"
  if [[ "$row" != *"|"* ]]; then
    report CRITIQUE base "instance injoignable avec le rôle ${PGUSER} : ${row:0:200}"
  else
    IFS='|' read -r mode cmd timeout cur last_wal last_ep fail_wal fail_ep failed dbnow checksums segsize <<<"$row"
    SM_WAL_SEGMENT_SIZE="$segsize"
    [[ -n "${SM_BACKUP_NOW_EPOCH:-}" ]] || NOW="$dbnow"
    if [[ "$mode" != on && "$mode" != always ]]; then
      report CRITIQUE archivage "désactivé (archive_mode=$mode) : aucune sauvegarde continue"
    elif [[ -z "$cmd" || "$cmd" == "(disabled)" ]]; then
      report CRITIQUE archivage "archive_command vide"
    else
      if (( timeout <= 0 || timeout > LAG_MAX )); then
        report CRITIQUE archivage "archive_timeout=${timeout} s incompatible avec le retard toléré (${LAG_MAX} s)"
      fi
      if (( fail_ep >= 0 && fail_ep > last_ep )); then
        report CRITIQUE archivage "échec NON RÉSOLU depuis $(human $(( NOW - fail_ep ))) (segment $fail_wal, $failed échec(s) au total)"
      elif (( fail_ep >= 0 )); then
        report INFO archivage "dernier échec résolu ($fail_wal, il y a $(human $(( NOW - fail_ep ))))"
      fi
      pending=""
      if [[ "${last_wal:0:24}" =~ $SM_SEGMENT_RE && "$cur" =~ $SM_SEGMENT_RE && "${last_wal:0:8}" == "${cur:0:8}" ]]; then
        read -r _ a < <(sm_seg_parse "$last_wal"); read -r _ c < <(sm_seg_parse "$cur")
        pending=$(( c - a - 1 )); (( pending < 0 )) && pending=0
      fi
      if (( last_ep < 0 )); then
        if (( fail_ep < 0 )); then report AVERTISSEMENT archivage "aucun WAL encore archivé (activation récente ?)"; fi
      elif [[ -z "$pending" ]]; then
        if (( NOW - last_ep > LAG_MAX )); then report AVERTISSEMENT archivage "dernier archivage il y a $(human $(( NOW - last_ep ))) ; retard non calculable (timeline ou nom $last_wal)"
        else report OK archivage "dernier segment archivé il y a $(human $(( NOW - last_ep )))"; fi
      elif (( pending > 0 )); then
        if (( NOW - last_ep > LAG_MAX )); then
          report CRITIQUE archivage "RETARD : $pending segment(s) terminé(s) non archivé(s), dernier archivage il y a $(human $(( NOW - last_ep ))) (> ${LAG_MAX} s)"
        else
          report OK archivage "en cours : $pending segment(s) en attente, dernier archivage il y a $(human $(( NOW - last_ep )))"
        fi
      elif (( NOW - last_ep > LAG_MAX )); then
        report OK archivage "à jour — base inactive : aucun nouveau segment depuis $(human $(( NOW - last_ep ))) (aucun WAL attendu)"
      else
        report OK archivage "à jour : dernier segment archivé il y a $(human $(( NOW - last_ep )))"
      fi
    fi
    [[ "$checksums" == on ]] || report INFO intégrité "data_checksums=off : une corruption silencieuse d'une page de données n'est pas détectée à la lecture ; pg_amcheck et pg_verifybackup ne remplacent pas ces sommes de contrôle"
  fi
fi

# --- 2. Opérations planifiées ------------------------------------------------------------------
op_check() { # opération libellé âge_max niveau_échec
  local op="$1" label="$2" max="$3" lvl="$4" last="$ST/$1.json" ok="$ST/$1.success.json" st started ok_end
  st="$(sm_json_get "$last" status)"
  started="$(sm_json_get "$last" started_at_epoch)"
  ok_end="$(sm_json_get "$ok" finished_at_epoch)"
  if [[ "$st" == running ]] && [[ "$started" =~ ^[0-9]+$ ]]; then
    if (( NOW - started > RUN_MAX )); then report CRITIQUE "$op" "$label « en cours » depuis $(human $(( NOW - started ))) : bloquée ou interrompue brutalement"
    else report INFO "$op" "$label en cours depuis $(human $(( NOW - started )))"; fi
  elif [[ "$st" == failed || "$st" == interrupted ]]; then
    report "$lvl" "$op" "dernière exécution en ÉCHEC ($st) : $(sm_json_get "$last" message)"
  fi
  if [[ ! "$ok_end" =~ ^[0-9]+$ ]]; then
    report "$lvl" "$op" "$label : aucune réussite enregistrée"
  elif (( NOW - ok_end > max )); then
    report "$lvl" "$op" "$label : dernière réussite il y a $(human $(( NOW - ok_end ))) (seuil $(human "$max"))"
  elif [[ "$st" != failed && "$st" != interrupted ]]; then
    report OK "$op" "$label réussie il y a $(human $(( NOW - ok_end )))"
  fi
}
op_check base_backup "sauvegarde complète" "$BASE_MAX" CRITIQUE
op_check verify "vérification d'intégrité" "$VERIFY_MAX" CRITIQUE
op_check restore_test "test de restauration" "$RESTORE_MAX" CRITIQUE
op_check prune "rétention" "$(( BASE_MAX + 86400 ))" AVERTISSEMENT

# --- 3. Stockage -------------------------------------------------------------------------------
pct="$(sm_free_percent "$SM_ROOT")"
if (( pct < DISK_CRIT )); then report CRITIQUE stockage "disque des sauvegardes : ${pct} % libre (< ${DISK_CRIT} %)"
elif (( pct < DISK_WARN )); then report AVERTISSEMENT stockage "disque des sauvegardes : ${pct} % libre (< ${DISK_WARN} %)"
else report OK stockage "disque des sauvegardes : ${pct} % libre"; fi
inv="$ST/inventory.json"
if [[ "$(sm_json_get "$inv" same_filesystem_as_data)" == true ]]; then
  report AVERTISSEMENT stockage "sauvegardes sur le MÊME système de fichiers que les données : une panne du disque emporte les deux"
fi
dirs="$(sm_json_get "$inv" restore_test_dirs)"
if [[ "$dirs" =~ ^[0-9]+$ ]] && (( dirs > 0 )) && [[ "$(sm_json_get "$ST/restore_test.json" status)" != running ]]; then
  report AVERTISSEMENT stockage "$dirs environnement(s) de test de restauration à examiner (restore-test/)"
fi

# --- 4. Copie externe --------------------------------------------------------------------------
off="$ST/offsite.json"
if [[ "$(sm_json_get "$off" configured)" != true ]]; then
  report AVERTISSEMENT copie_externe "copie externe NON CONFIGURÉE : les sauvegardes n'existent que sur ce serveur"
else
  op_check offsite "copie externe" "$OFFSITE_MAX" CRITIQUE
fi

if (( WRITE )) && [[ -w "$ST" ]]; then
  level=OK; (( STATUS == 1 )) && level=AVERTISSEMENT; (( STATUS == 2 )) && level=CRITIQUE
  tmp="$ST/.health.json.tmp.$$"
  joined=""; for l in "${LINES[@]}"; do joined+="${joined:+, }$l"; done
  printf '{\n  "schema": %s,\n  "checked_at_epoch": %s,\n  "checked_at": %s,\n  "level": %s,\n  "exit_code": %s,\n  "checks": [%s]\n}\n' \
    "$SM_SCHEMA_VERSION" "$NOW" "$(sm_js "$(sm_iso "$NOW")")" "$(sm_js "$level")" "$STATUS" "$joined" >"$tmp"
  chmod 0644 "$tmp"; mv -f "$tmp" "$ST/health.json"
fi
exit "$STATUS"
