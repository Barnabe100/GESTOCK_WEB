#!/usr/bin/env bash
# Restauration à un instant donné (PITR) dans un répertoire de données NEUF.
#
#   SM_BACKUP_DIR=/backups sm-restore-pitr.sh --data-dir /backups/pitr/enquete-0211 \
#       [--target-time "2026-11-02 10:15:00Z" | --target-lsn 0/5000060 | --target-name nom
#        | --target-immediate] [--backup AAAAMMJJTHHMMSSZ] [--port 5544] [--socket-dir DIR]
#       [--listen ADRESSES] [--action promote|pause|shutdown] [--start]
#
# Ne touche JAMAIS une base existante : refuse un répertoire non vide, un lien symbolique, le
# répertoire de données de l'instance de production (PGDATA) et les répertoires internes des
# sauvegardes. Sans cible : jusqu'au dernier WAL archivé. Heure : UTC obligatoire (suffixe Z,
# +00 ou +00:00 ; jamais l'heure locale implicite). Contrôles préalables (refus au moindre
# doute) : sauvegarde VÉRIFIÉE antérieure à la cible, chaîne WAL continue et lisible depuis le
# début de cette sauvegarde, cible antérieure au dernier WAL archivé. La copie : archivage
# désactivé (l'archive n'est jamais modifiée), écoute réseau désactivée par défaut (socket
# locale seulement), port distinct. Basculer la production sur une copie reste une décision
# humaine documentée (docs/exploitation/SAUVEGARDE_RESTAURATION.md §9) — jamais ce script.
set -euo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"

DATA="" BACKUP="" PORT=5544 SOCKET="" LISTEN="" ACTION=promote START=0 KIND=end TARGET=""
while (( $# )); do
  case "$1" in
    --target-time) KIND="time"; TARGET="$2"; shift 2 ;;
    --target-lsn) KIND=lsn; TARGET="$2"; shift 2 ;;
    --target-name) KIND=name; TARGET="$2"; shift 2 ;;
    --target-immediate) KIND=immediate; shift ;;
    --data-dir) DATA="$2"; shift 2 ;;
    --backup) BACKUP="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --socket-dir) SOCKET="$2"; shift 2 ;;
    --listen) LISTEN="$2"; shift 2 ;;
    --action) ACTION="$2"; shift 2 ;;
    --start) START=1; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
SM_ROOT="$(sm_root)"
sm_log_init pitr
sm_need pg_ctl psql gzip
[[ -n "$DATA" ]] || sm_die "--data-dir requis"
[[ "$DATA" == /* ]] || sm_die "--data-dir doit être absolu"
[[ "$PORT" =~ ^[0-9]+$ ]] || sm_die "--port invalide"
[[ "$ACTION" =~ ^(promote|pause|shutdown)$ ]] || sm_die "--action : promote, pause ou shutdown"
[[ -z "$LISTEN" || "$LISTEN" =~ ^[0-9a-fA-F.:,*\ ]+$ ]] || sm_die "--listen invalide"
[[ "$TARGET" != *"'"* ]] || sm_die "cible invalide (guillemet)"

# --- Répertoire cible ------------------------------------------------------------------------
[[ ! -L "$DATA" ]] || sm_die "refus : $DATA est un lien symbolique"
if [[ -e "$DATA" ]]; then
  [[ -d "$DATA" ]] || sm_die "refus : $DATA existe et n'est pas un répertoire"
  [[ -z "$(ls -A "$DATA")" ]] || sm_die "refus : $DATA n'est pas vide (jamais de restauration sur une base existante)"
  rmdir "$DATA"
fi
PARENT="$(dirname "$DATA")"
[[ -d "$PARENT" && ! -L "$PARENT" ]] || sm_die "répertoire parent absent : $PARENT"
DATA="$(realpath "$PARENT")/${DATA##*/}"
PROD="$(sm_production_pgdata)"
if [[ -n "$PROD" ]] && sm_paths_overlap "$DATA" "$PROD"; then
  sm_die "refus : $DATA recouvre le répertoire de données de PRODUCTION ($PROD)"
fi
for d in "" wal base state locks logs status restore-test; do
  if [[ "$DATA" == "$SM_ROOT${d:+/$d}" ]] || { [[ -n "$d" ]] && sm_paths_overlap "$DATA" "$SM_ROOT/$d"; }; then
    sm_die "refus : $DATA est un répertoire interne des sauvegardes"
  fi
done
SOCKET="${SOCKET:-$(realpath "$PARENT")}"
[[ -d "$SOCKET" ]] || sm_die "--socket-dir absent : $SOCKET"

# --- Cible -----------------------------------------------------------------------------------
TARGET_EPOCH=""
if [[ "$KIND" == time ]]; then
  re='^([0-9]{4}-[0-9]{2}-[0-9]{2})[T ]([0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]{1,6})?( ?(Z|\+00|\+00:00|UTC))$'
  [[ "$TARGET" =~ $re ]] || sm_die "--target-time : format « AAAA-MM-JJ HH:MM:SS[.ffffff]Z » (UTC explicite)"
  TARGET_EPOCH="$(date -u -d "${BASH_REMATCH[1]} ${BASH_REMATCH[2]}" +%s)" || sm_die "--target-time : date invalide"
  TARGET="${BASH_REMATCH[1]} ${BASH_REMATCH[2]}${BASH_REMATCH[3]}+00"
elif [[ "$KIND" == lsn ]]; then
  [[ "$TARGET" =~ ^[0-9A-Fa-f]{1,8}/[0-9A-Fa-f]{1,8}$ ]] || sm_die "--target-lsn : format X/Y"
  TARGET="${TARGET^^}"
elif [[ "$KIND" == name ]]; then
  [[ "$TARGET" =~ ^[A-Za-z0-9_.:-]{1,63}$ ]] || sm_die "--target-name invalide"
fi

# --- Sauvegarde : vérifiée, terminée avant la cible -------------------------------------------
if [[ -n "$BACKUP" ]]; then
  [[ "$BACKUP" =~ $SM_STAMP_RE && -d "$SM_ROOT/base/$BACKUP" ]] || sm_die "sauvegarde introuvable : $BACKUP"
  sm_backup_verified "$BACKUP" || sm_die "sauvegarde $BACKUP non vérifiée (ou en échec de vérification) : refus"
else
  for s in $(sm_backups); do
    sm_backup_verified "$s" || continue
    info="$SM_ROOT/base/$s/backup_info.json"
    case "$KIND" in
      time) (( $(sm_json_get "$info" finished_at_epoch) <= TARGET_EPOCH )) || continue ;;
      lsn) (( $(sm_lsn_bytes "$(sm_json_get "$info" end_lsn)") <= $(sm_lsn_bytes "$TARGET") )) || continue ;;
    esac
    BACKUP="$s"
  done
  [[ -n "$BACKUP" ]] || sm_die "aucune sauvegarde vérifiée terminée avant la cible"
fi
INFO="$SM_ROOT/base/$BACKUP/backup_info.json"
SM_WAL_SEGMENT_SIZE="$(sm_json_get "$INFO" wal_segment_size)"
if [[ "$KIND" == time ]] && (( $(sm_json_get "$INFO" finished_at_epoch) > TARGET_EPOCH )); then
  sm_die "la sauvegarde $BACKUP se termine après la cible : choisir une sauvegarde antérieure"
fi

# --- WAL : continuité et lisibilité depuis le début de la sauvegarde --------------------------
read -r B_TLI B_START < <(sm_seg_parse "$(sm_json_get "$INFO" start_wal)")
missing=0 bad=0 last="" problems=()
while read -r kind a b c; do
  case "$kind" in
    E) problems+=("$a $b $c") ;;
    M) missing=$(( missing + 1 )) ;;
    H) sm_wal_cat "$a" >/dev/null 2>&1 || problems+=("historique ${a##*/} illisible") ;;
    S) last="$c"; if [[ "$c" == *.gz ]] && ! gzip -t "$c" 2>/dev/null; then bad=$(( bad + 1 )); fi ;;
  esac
done < <(sm_wal_chain "$B_TLI" "$B_START")
(( ${#problems[@]} == 0 )) || sm_die "WAL : ${problems[0]}"
(( missing == 0 )) || sm_die "WAL : $missing segment(s) manquant(s) après la sauvegarde $BACKUP (sm-backup-verify.sh pour le détail)"
(( bad == 0 )) || sm_die "WAL : $bad segment(s) corrompu(s) : restauration refusée"
[[ -n "$last" ]] || sm_die "WAL : aucun segment archivé après la sauvegarde"
if [[ "$KIND" == time ]] && (( TARGET_EPOCH > $(stat -c %Y "$last") )); then
  sm_die "la cible est postérieure au dernier WAL archivé ($(sm_iso "$(stat -c %Y "$last")")) : données indisponibles"
fi

R_BACKUP="$BACKUP" R_DATA="$DATA" R_PORT="$PORT" R_SOCKET="$SOCKET" R_LISTEN="$LISTEN"
R_ACTION="$ACTION" R_TARGET_KIND="$KIND" R_TARGET="$TARGET" R_HBA_LOCAL_ONLY=0
sm_restore_prepare
sm_log "préparée : sauvegarde $BACKUP, cible ${KIND}${TARGET:+ $TARGET}, données $DATA, port $PORT, socket $SOCKET"
echo "base : $BACKUP ; cible : $KIND ${TARGET:-(dernier WAL archivé)} ; données : $DATA"

if (( START )); then
  pg_ctl -D "$DATA" -l "$DATA/restore.log" -w -t "${SM_RESTORE_START_TIMEOUT:-86400}" start >/dev/null \
    || sm_die "démarrage de la copie impossible (voir $DATA/restore.log)"
  if [[ "$ACTION" == promote ]]; then
    # Interrogation de la COPIE (sa socket, son port) avec le rôle de réplication.
    deadline=$(( $(date -u +%s) + ${SM_RESTORE_START_TIMEOUT:-86400} ))
    until [[ "$(psql -X -w -h "$SOCKET" -p "$PORT" -U "${SM_BACKUP_PGUSER:-stockmanager_backup}" -d postgres -Atqc 'SELECT pg_is_in_recovery()' 2>/dev/null)" == f ]]; do
      pg_ctl -D "$DATA" status >/dev/null 2>&1 || sm_die "la copie s'est arrêtée pendant la récupération (voir $DATA/restore.log)"
      (( $(date -u +%s) < deadline )) || sm_die "récupération trop longue"
      sleep 1
    done
    sm_log "restauration terminée, copie promue (socket $SOCKET, port $PORT)"
  fi
  echo "copie démarrée : psql -h $SOCKET -p $PORT -d stockmanager"
fi
