#!/usr/bin/env bash
# Rétention des sauvegardes (défaut 30 jours) — SIMULATION par défaut.
#
#   SM_BACKUP_DIR=/backups sm-backup-prune.sh [--apply] [--wait SECONDES]
#
# Sans --apply : affiche et journalise ce qui SERAIT supprimé, ne supprime rien.
# Règles (toutes appliquées ; la plus protectrice l'emporte) :
#   1. toute sauvegarde terminée dans la fenêtre (SM_BACKUP_RETENTION_DAYS) est conservée ;
#   2. la plus récente sauvegarde VÉRIFIÉE antérieure à la fenêtre est conservée (sans elle, le
#      début de la fenêtre ne serait pas restaurable) ;
#   3. la dernière sauvegarde vérifiée n'est JAMAIS supprimée, ni rien de plus récent qu'elle ;
#   4. au moins SM_BACKUP_KEEP_MIN sauvegardes (défaut 2) sont toujours conservées ;
#   5. seuls les WAL antérieurs au début de la plus ancienne sauvegarde conservée sont supprimés
#      (pg_archivecleanup) : la chaîne PITR de chaque sauvegarde conservée reste complète ; les
#      historiques de timeline ne sont jamais supprimés.
# Chaque suppression est contrôlée (nom conforme, enfant direct de base/, pas de lien
# symbolique, manifeste présent) ; au moindre doute : refus et échec, rien n'est supprimé.
set -euo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"

APPLY=0 WAIT=0
while (( $# )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --wait) WAIT="$2"; shift 2 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
sm_need pg_archivecleanup
SM_ROOT="$(sm_root)"
sm_log_init prune
sm_lock backup "$WAIT"
sm_op_begin prune
MODE=$([[ $APPLY == 1 ]] && echo apply || echo dry-run)
sm_extra mode "$(sm_js "$MODE")"
sm_log "mode : $MODE"

DAYS="${SM_BACKUP_RETENTION_DAYS:-30}"
KEEP_MIN="${SM_BACKUP_KEEP_MIN:-2}"
[[ "$DAYS" =~ ^[0-9]+$ && "$KEEP_MIN" =~ ^[0-9]+$ ]] || sm_op_fail "paramètres de rétention invalides"
(( KEEP_MIN >= 1 )) || sm_op_fail "SM_BACKUP_KEEP_MIN doit être ≥ 1"
NOW="$(sm_now)"
CUTOFF=$(( NOW - DAYS * 86400 ))

mapfile -t B < <(sm_backups)
(( ${#B[@]} > 0 )) || sm_op_fail "aucune sauvegarde de base : rien à faire, rien supprimé"
LATEST_V=-1
for i in "${!B[@]}"; do sm_backup_verified "${B[$i]}" && LATEST_V=$i; done
(( LATEST_V >= 0 )) || sm_op_fail "aucune sauvegarde vérifiée : suppression refusée"

# Point de départ conservé : plus récente sauvegarde vérifiée terminée avant la limite.
KEEP_FROM=-1
for i in "${!B[@]}"; do
  end="$(sm_json_get "$SM_ROOT/base/${B[$i]}/backup_info.json" finished_at_epoch)"
  if [[ "$end" =~ ^[0-9]+$ ]] && (( end <= CUTOFF )) && sm_backup_verified "${B[$i]}"; then KEEP_FROM=$i; fi
done
(( KEEP_FROM < 0 )) && KEEP_FROM=0           # rien d'assez ancien : tout est conservé
(( KEEP_FROM > LATEST_V )) && KEEP_FROM=$LATEST_V
max_from=$(( ${#B[@]} - KEEP_MIN )); (( max_from < 0 )) && max_from=0
(( KEEP_FROM > max_from )) && KEEP_FROM=$max_from

safe_backup_dir() { # horodatage → 0 si suppression sûre
  local s="$1" d="$SM_ROOT/base/$1"
  [[ "$s" =~ $SM_STAMP_RE ]] || { sm_log "refus : nom non conforme $s"; return 1; }
  [[ -d "$d" && ! -L "$d" ]] || { sm_log "refus : $s n'est pas un répertoire réel"; return 1; }
  [[ "$(dirname "$(realpath "$d")")" == "$SM_ROOT/base" ]] || { sm_log "refus : $s hors de base/"; return 1; }
  [[ -f "$d/backup_manifest" && -f "$d/backup_info.json" ]] || { sm_log "refus : $s sans manifeste"; return 1; }
  [[ -z "$(find "$d" -type l | head -n 1)" ]] || { sm_log "refus : $s contient un lien symbolique"; return 1; }
  return 0
}

# Tous les candidats sont contrôlés AVANT toute suppression : un seul doute = rien supprimé.
refused=0
for (( i = 0; i < KEEP_FROM; i++ )); do safe_backup_dir "${B[$i]}" || refused=$(( refused + 1 )); done
(( refused == 0 )) || sm_op_fail "$refused suppression(s) refusée(s) par les contrôles de chemin : rien n'a été supprimé"
deleted=()
for (( i = 0; i < KEEP_FROM; i++ )); do
  s="${B[$i]}"
  if (( APPLY )); then
    chmod -R u+w "$SM_ROOT/base/$s"
    rm -rf -- "${SM_ROOT:?}/base/$s"
    rm -f "$SM_ROOT/state/backups/$s.verify"
    sm_log "SUPPRIMÉE : base/$s"
  else
    sm_log "serait supprimée : base/$s"
  fi
  deleted+=("$s")
done
for (( i = KEEP_FROM; i < ${#B[@]}; i++ )); do sm_log "conservée : base/${B[$i]}"; done

OLDEST="${B[$KEEP_FROM]}"
START_WAL="$(sm_json_get "$SM_ROOT/base/$OLDEST/backup_info.json" start_wal)"
[[ "$START_WAL" =~ $SM_SEGMENT_RE ]] || sm_op_fail "WAL de départ illisible pour $OLDEST : aucun WAL supprimé"
sm_log "plus ancienne sauvegarde conservée : $OLDEST (WAL $START_WAL)"
wal_list="$( { pg_archivecleanup -n -x .gz "$SM_ROOT/wal" "$START_WAL"; pg_archivecleanup -n "$SM_ROOT/wal" "$START_WAL"; } 2>>"$SM_LOG_FILE" | sort -u)"
wal_count=0
if [[ -n "$wal_list" ]]; then wal_count="$(echo "$wal_list" | wc -l | tr -d ' ')"; fi
if (( APPLY && wal_count > 0 )); then
  pg_archivecleanup -x .gz "$SM_ROOT/wal" "$START_WAL" 2>>"$SM_LOG_FILE"
  pg_archivecleanup "$SM_ROOT/wal" "$START_WAL" 2>>"$SM_LOG_FILE"
  sm_log "SUPPRIMÉS : $wal_count fichier(s) WAL antérieurs à $START_WAL"
else
  sm_log "$([[ $APPLY == 1 ]] && echo "WAL supprimés" || echo "WAL qui seraient supprimés") : $wal_count"
fi
sm_inventory_write

list_json() { local out="" x; for x in "$@"; do out+="${out:+, }$(sm_js "$x")"; done; printf '[%s]' "$out"; }
sm_extra retention_days "$DAYS"
sm_extra backups_deleted "$(list_json "${deleted[@]}")"
sm_extra backups_kept "$(( ${#B[@]} - KEEP_FROM ))"
sm_extra oldest_kept "$(sm_js "$OLDEST")"
sm_extra latest_verified "$(sm_js "${B[$LATEST_V]}")"
sm_extra wal_deleted "$wal_count"
sm_op_success "$MODE : ${#deleted[@]} sauvegarde(s) et $wal_count WAL $([[ $APPLY == 1 ]] && echo supprimés || echo "à supprimer (simulation)")"
