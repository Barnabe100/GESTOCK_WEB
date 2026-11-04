#!/usr/bin/env bash
# Sauvegarde physique complète du cluster — point de départ de toute restauration (hebdomadaire).
#
#   SM_BACKUP_DIR=/backups sm-base-backup.sh [--wait SECONDES] [--max-rate DÉBIT]
#
# 1. verrou global (aucune autre opération lourde ; --wait : attente maximale, défaut 0 = refus) ;
# 2. contrôle de l'espace : taille de la sauvegarde précédente + réserve (SM_BACKUP_RESERVE_MB,
#    SM_BACKUP_RESERVE_PERCENT) ; refus explicite plutôt qu'un disque saturé ;
# 3. pg_basebackup (format plain, WAL de la sauvegarde inclus en flux, point de contrôle
#    immédiat, manifeste SHA-256) avec le rôle de RÉPLICATION dédié stockmanager_backup
#    (SM_BACKUP_PGUSER ; jamais un superutilisateur ; mot de passe éventuel : PGPASSFILE 0600) ;
# 4. pg_verifybackup (fichiers ET WAL inclus) : une sauvegarde non vérifiée n'est JAMAIS
#    conservée comme sauvegarde ; le répertoire partiel est supprimé (chemin contrôlé) ;
# 5. métadonnées backup_info.json (dates, durée, taille, LSN, timeline, identifiant système,
#    état de vérification), répertoire en lecture seule, statut status/base_backup.json.
# Interruption (signal, arrêt du conteneur) : statut « interrupted », répertoire partiel
# supprimé ; un partiel orphelin (arrêt brutal) est supprimé à l'exécution suivante, sous verrou.
set -euo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"

WAIT=0 RATE=""
while (( $# )); do
  case "$1" in
    --wait) WAIT="$2"; shift 2 ;;
    --max-rate) RATE="$2"; shift 2 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
sm_need pg_basebackup pg_verifybackup pg_controldata psql gzip du df
SM_ROOT="$(sm_root)"
sm_log_init base_backup
umask 077
sm_lock backup "$WAIT"   # avant le statut : un refus n'écrase jamais l'opération en cours
sm_op_begin base_backup
sm_pg_env

PARTIAL="" BB_PID=""
cleanup() {
  # Interruption : pg_basebackup est arrêté AVANT toute suppression du répertoire partiel.
  if [[ -n "$BB_PID" ]] && kill -0 "$BB_PID" 2>/dev/null; then
    sm_kill_tree "$BB_PID" TERM
    wait "$BB_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -z "$(sm_children "$BB_PID")" ]] && break; sleep 0.5; done
  fi
  if [[ -n "$PARTIAL" && -d "$PARTIAL" ]]; then
    # Garde : uniquement base/.partial-<horodatage>, jamais autre chose.
    if [[ "$(dirname "$(realpath "$PARTIAL")")" == "$SM_ROOT/base" && "${PARTIAL##*/}" =~ ^\.partial-[0-9]{8}T[0-9]{6}Z$ ]]; then
      chmod -R u+w "$PARTIAL" 2>/dev/null || true
      rm -rf -- "$PARTIAL"
      sm_log "répertoire partiel supprimé : ${PARTIAL##*/}"
    else
      sm_log "suppression refusée (chemin inattendu) : $PARTIAL"
    fi
  fi
}
SM_OP_CLEANUP=cleanup

# Partiels orphelins d'une exécution brutalement arrêtée (le verrou garantit qu'aucune autre
# sauvegarde n'est en cours).
for orphan in "$SM_ROOT"/base/.partial-*; do
  [[ -d "$orphan" ]] || continue
  PARTIAL="$orphan"; sm_log "partiel orphelin trouvé : ${orphan##*/}"; cleanup
done
PARTIAL=""

STAMP="$(sm_stamp "$SM_START_EPOCH")"
DEST="$SM_ROOT/base/$STAMP"
[[ ! -e "$DEST" ]] || sm_die "la sauvegarde $STAMP existe déjà"

# Espace : estimation = taille de la sauvegarde précédente (+10 %).
prev="$(sm_backups | tail -n 1)"
estimate_kb=0
if [[ -n "$prev" ]]; then
  estimate_kb=$(( $(sm_json_get "$SM_ROOT/base/$prev/backup_info.json" size_bytes) / 1024 * 11 / 10 ))
fi
free_kb="$(sm_free_kb "$SM_ROOT")"; reserve_kb="$(sm_reserve_kb "$SM_ROOT")"
sm_extra disk_free_bytes_before "$(( free_kb * 1024 ))"
sm_extra estimated_bytes "$(( estimate_kb * 1024 ))"
if (( free_kb - estimate_kb < reserve_kb )); then
  sm_op_fail "espace insuffisant : ${free_kb} Kio libres, estimation ${estimate_kb} Kio, réserve ${reserve_kb} Kio"
fi

PARTIAL="$SM_ROOT/base/.partial-$STAMP"
sm_log "pg_basebackup (rôle $PGUSER) → ${PARTIAL##*/}"
args=(--pgdata="$PARTIAL" --format=plain --wal-method=stream --checkpoint=fast
  --manifest-checksums=SHA256 --label="stockmanager-$STAMP" --no-password)
[[ -n "$RATE" ]] && args+=(--max-rate="$RATE")
# En arrière-plan + wait : un signal (TERM, INT) interrompt l'attente immédiatement.
# 9>&- : le descripteur du verrou n'est jamais hérité (le verrou se libère avec ce script).
pg_basebackup "${args[@]}" 2>>"$SM_LOG_FILE" 9>&- &
BB_PID=$!
if ! wait "$BB_PID"; then
  BB_PID=""
  sm_op_fail "pg_basebackup a échoué (détail : journal logs/base_backup-*.log)"
fi
BB_PID=""
sm_log "pg_verifybackup"
if ! pg_verifybackup --quiet "$PARTIAL" 2>>"$SM_LOG_FILE"; then
  sm_op_fail "pg_verifybackup : sauvegarde invalide, non conservée"
fi
END="$(sm_now)"
START_WAL="$(sed -n 's/^START WAL LOCATION: .* (file \([0-9A-F]\{24\}\))$/\1/p' "$PARTIAL/backup_label")"
START_LSN="$(sed -n 's/^START WAL LOCATION: \([0-9A-F]*\/[0-9A-F]*\) .*/\1/p' "$PARTIAL/backup_label")"
TLI="$(sed -n 's/^START TIMELINE: \([0-9]*\)$/\1/p' "$PARTIAL/backup_label")"
END_LSN="$(sed -n 's/.*"End-LSN": "\([0-9A-F]*\/[0-9A-F]*\)".*/\1/p' "$PARTIAL/backup_manifest" | tail -n 1)"
controldata="$(pg_controldata "$PARTIAL")"
SYSID="$(echo "$controldata" | sed -n 's/^Database system identifier: *//p')"
SEGSIZE="$(echo "$controldata" | sed -n 's/^Bytes per WAL segment: *//p')"
CHECKSUMS="$(echo "$controldata" | sed -n 's/^Data page checksum version: *//p')"
PGVER="$(cat "$PARTIAL/PG_VERSION")"
[[ -n "$START_WAL" && -n "$START_LSN" && -n "$END_LSN" && -n "$TLI" && -n "$SYSID" ]] \
  || sm_op_fail "métadonnées de la sauvegarde illisibles (backup_label / backup_manifest)"
SIZE="$(sm_size_bytes "$PARTIAL")"
cat >"$PARTIAL/backup_info.json" <<JSON
{
  "schema": $SM_SCHEMA_VERSION,
  "label": "stockmanager-$STAMP",
  "stamp": "$STAMP",
  "started_at": "$(sm_iso "$SM_START_EPOCH")",
  "started_at_epoch": $SM_START_EPOCH,
  "finished_at": "$(sm_iso "$END")",
  "finished_at_epoch": $END,
  "duration_seconds": $(( END - SM_START_EPOCH )),
  "size_bytes": $SIZE,
  "start_wal": "$START_WAL",
  "start_lsn": "$START_LSN",
  "end_lsn": "$END_LSN",
  "timeline": $TLI,
  "system_identifier": "$SYSID",
  "wal_segment_size": ${SEGSIZE:-16777216},
  "pg_version": "$PGVER",
  "data_checksums": $([[ "${CHECKSUMS:-0}" != 0 ]] && echo true || echo false),
  "role": "$PGUSER",
  "verified": true,
  "verified_at_epoch": $END
}
JSON
mv "$PARTIAL" "$DEST"
PARTIAL=""
chmod -R a-w "$DEST"
echo "ok $END creation" >"$SM_ROOT/state/backups/$STAMP.verify"
sm_inventory_write

sm_extra label "$(sm_js "$STAMP")"
sm_extra size_bytes "$SIZE"
sm_extra start_wal "$(sm_js "$START_WAL")"
sm_extra timeline "$TLI"
sm_extra verified true
sm_extra data_checksums "$([[ "${CHECKSUMS:-0}" != 0 ]] && echo true || echo false)"
sm_op_success "sauvegarde $STAMP vérifiée ($(( SIZE / 1048576 )) Mio)"
echo "$DEST"
