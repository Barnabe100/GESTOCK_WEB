#!/usr/bin/env bash
# Vérification quotidienne de l'intégrité des sauvegardes — capacité réelle de restauration.
#
#   SM_BACKUP_DIR=/backups sm-backup-verify.sh [--wait SECONDES] [--full] [--latest-only]
#
# Contrôles (tout écart = statut « failed », code 1) :
#   1. pg_verifybackup de CHAQUE sauvegarde conservée (manifeste SHA-256 de chaque fichier, WAL
#      inclus analysés par pg_waldump) ; le résultat est mémorisé (state/backups/<horodatage>.verify)
#      et une sauvegarde en échec n'est plus « vérifiée » (rétention, restauration) ;
#   2. continuité des WAL archivés depuis le début de la plus ancienne sauvegarde vérifiée
#      jusqu'au dernier segment archivé, en suivant les timelines (fichiers .history : chaque
#      segment est cherché sur la timeline que PostgreSQL lirait) ; tout segment manquant = trou ;
#   3. cohérence des segments archivés (nouveaux depuis la dernière vérification, ou tous avec
#      --full) : décompression complète (CRC gzip), taille exacte, en-tête de page longue :
#      nombre magique de la version, adresse du segment conforme à son nom, timeline, et
#      identifiant système identique à celui de la sauvegarde (aucun WAL d'un autre cluster) ;
#   4. présence des éléments nécessaires à une restauration (sauvegarde vérifiée, historiques,
#      script de restitution).
# LIMITE : ces contrôles ne remplacent PAS les sommes de contrôle des pages de données
# (data_checksums) ; voir docs/exploitation/SAUVEGARDE_RESTAURATION.md §7.
set -euo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"
HERE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

WAIT=0 FULL=0 LATEST_ONLY=0
while (( $# )); do
  case "$1" in
    --wait) WAIT="$2"; shift 2 ;;
    --full) FULL=1; shift ;;
    --latest-only) LATEST_ONLY=1; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
sm_need pg_verifybackup gzip od cmp
SM_ROOT="$(sm_root)"
sm_log_init verify
sm_lock backup "$WAIT"
sm_op_begin verify

ERRORS=()
err() { ERRORS+=("$*"); sm_log "ANOMALIE : $*"; }

# --- 1. Sauvegardes de base ------------------------------------------------------------------
mapfile -t BACKUPS < <(sm_backups)
(( ${#BACKUPS[@]} > 0 )) || sm_op_fail "aucune sauvegarde de base : restauration impossible"
targets=("${BACKUPS[@]}")
(( LATEST_ONLY )) && targets=("${BACKUPS[-1]}")
ok_count=0
for s in "${targets[@]}"; do
  now="$(sm_now)"
  if [[ "$(sm_json_get "$SM_ROOT/base/$s/backup_info.json" verified)" != true ]]; then
    err "sauvegarde $s : jamais vérifiée à sa création"; echo "failed $now creation" >"$SM_ROOT/state/backups/$s.verify"; continue
  fi
  if pg_verifybackup --quiet --ignore=backup_info.json "$SM_ROOT/base/$s" 2>>"$SM_LOG_FILE"; then
    echo "ok $now daily" >"$SM_ROOT/state/backups/$s.verify"; ok_count=$(( ok_count + 1 ))
    sm_log "sauvegarde $s : pg_verifybackup OK"
  else
    echo "failed $now daily" >"$SM_ROOT/state/backups/$s.verify"
    err "sauvegarde $s : pg_verifybackup en ÉCHEC (fichier altéré ou manquant)"
  fi
done

OLDEST=""
for s in "${BACKUPS[@]}"; do if sm_backup_verified "$s"; then OLDEST="$s"; break; fi; done
LATEST="$(sm_latest_verified_backup)"
[[ -n "$LATEST" ]] || err "aucune sauvegarde vérifiée : restauration impossible"

seg_checked=0 seg_missing=0 timelines=1 window_from="" window_to=""
if [[ -n "$OLDEST" ]]; then
  INFO="$SM_ROOT/base/$OLDEST/backup_info.json"
  SM_WAL_SEGMENT_SIZE="$(sm_json_get "$INFO" wal_segment_size)"
  REF_SYSID="$(sm_json_get "$SM_ROOT/base/$LATEST/backup_info.json" system_identifier)"
  read -r B_TLI B_START < <(sm_seg_parse "$(sm_json_get "$INFO" start_wal)")
  window_from="$(sm_json_get "$INFO" finished_at_epoch)"
  # Nombre magique des pages WAL de cette version : lu dans un segment de la sauvegarde.
  ref_seg="$(find "$SM_ROOT/base/$LATEST/pg_wal" -maxdepth 1 -type f -name '????????????????????????' | sort | head -n 1)"
  REF_MAGIC="$(od -An -tx1 -N2 "$ref_seg" | tr -d ' \n')"

  # --- 2 et 3. Chaîne WAL (timelines) et cohérence des segments ------------------------------
  CURSOR_FILE="$SM_ROOT/state/wal-verified-upto"
  cursor=-1
  if (( FULL == 0 )) && [[ -f "$CURSOR_FILE" ]]; then cursor="$(cut -d' ' -f2 "$CURSOR_FILE")"; fi
  check_segment() { # fichier tli num → 0 si cohérent
    local f="$1" tli="$2" num="$3" size hdr b magic ptli addr sysid
    size="$(sm_wal_cat "$f" | wc -c)" || { err "segment ${f##*/} : décompression en échec (CRC)"; return 1; }
    (( size == SM_WAL_SEGMENT_SIZE )) || { err "segment ${f##*/} : taille $size ≠ $SM_WAL_SEGMENT_SIZE"; return 1; }
    hdr="$( (sm_wal_cat "$f" 2>/dev/null || true) | od -An -tx1 -N40 | tr -s ' \n' ' ')"
    read -r -a b <<<"$hdr"
    (( ${#b[@]} == 40 )) || { err "segment ${f##*/} : en-tête illisible"; return 1; }
    magic="${b[0]}${b[1]}"
    ptli=$(( 16#${b[7]}${b[6]}${b[5]}${b[4]} ))
    addr=$(( 16#${b[15]}${b[14]}${b[13]}${b[12]}${b[11]}${b[10]}${b[9]}${b[8]} ))
    sysid="$(printf '%u' "$(( 16#${b[31]}${b[30]}${b[29]}${b[28]}${b[27]}${b[26]}${b[25]}${b[24]} ))")"
    [[ "$magic" == "$REF_MAGIC" ]] || { err "segment ${f##*/} : nombre magique $magic ≠ $REF_MAGIC"; return 1; }
    (( addr == num * SM_WAL_SEGMENT_SIZE )) || { err "segment ${f##*/} : adresse de page non conforme au nom"; return 1; }
    (( ptli <= tli )) || { err "segment ${f##*/} : timeline de page $ptli > $tli"; return 1; }
    [[ "$sysid" == "$REF_SYSID" ]] || { err "segment ${f##*/} : identifiant système $sysid ≠ $REF_SYSID (autre cluster)"; return 1; }
    return 0
  }
  new_cursor="" contiguous=1 missing_list=() last_file=""
  while read -r kind a b c; do
    case "$kind" in
      T) timelines="$a" ;;
      E) err "WAL : $a $b $c" ;;
      H) sm_wal_cat "$a" >/dev/null 2>&1 || err "historique ${a##*/} corrompu" ;;
      M)
        seg_missing=$(( seg_missing + 1 )); contiguous=0
        (( ${#missing_list[@]} < 20 )) && missing_list+=("$(sm_seg_name "$a" "$b")") ;;
      S)
        last_file="$c"
        if (( b > cursor )); then
          if check_segment "$c" "$a" "$b"; then
            seg_checked=$(( seg_checked + 1 ))
            (( contiguous )) && new_cursor="$a $b"
          else
            contiguous=0
          fi
        fi ;;
    esac
  done < <(sm_wal_chain "$B_TLI" "$B_START")
  (( seg_missing == 0 )) || err "$seg_missing segment(s) WAL manquant(s) dans la chaîne (premiers : ${missing_list[*]})"
  [[ -n "$last_file" ]] && window_to="$(stat -c %Y "$last_file")"
  [[ -n "$new_cursor" ]] && echo "$new_cursor" >"$CURSOR_FILE"
fi

# --- 4. Éléments nécessaires à la restauration -----------------------------------------------
[[ -x "$HERE/sm-wal-restore.sh" ]] || err "script de restitution sm-wal-restore.sh absent"
sm_inventory_write

list_json() { local out="" x; for x in "$@"; do out+="${out:+, }$(sm_js "$x")"; done; printf '[%s]' "$out"; }
sm_extra backups_checked "${#targets[@]}"
sm_extra backups_ok "$ok_count"
sm_extra latest_verified_backup "$(sm_js "$LATEST")"
sm_extra oldest_verified_backup "$(sm_js "$OLDEST")"
sm_extra wal_segments_checked "$seg_checked"
sm_extra wal_segments_missing "$seg_missing"
sm_extra timelines "$timelines"
sm_extra full "$(sm_jb "$FULL")"
sm_extra restorable_from_epoch "$(sm_jn "$window_from")"
sm_extra restorable_to_epoch "$(sm_jn "$window_to")"
sm_extra errors "$(list_json "${ERRORS[@]}")"
if (( ${#ERRORS[@]} > 0 )); then
  sm_op_fail "${#ERRORS[@]} anomalie(s) : ${ERRORS[0]}"
fi
sm_op_success "$ok_count sauvegarde(s) vérifiée(s), $seg_checked segment(s) contrôlé(s), chaîne WAL continue"
