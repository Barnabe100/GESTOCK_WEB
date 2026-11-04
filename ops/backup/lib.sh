# shellcheck shell=bash
# Bibliothèque commune des scripts de sauvegarde StockManager (R11-0, ADR-0057).
#
# Compatible avec l'image officielle postgres:16-alpine (bash + busybox) et Debian (coreutils) :
# aucune option GNU seule (pas de find -printf, de date -d relatif, de stat --printf, de
# readlink -e…). Toute date manipulée est en UTC ; les statuts portent des époques entières.
#
# Arborescence de ${SM_BACKUP_DIR} (créée par sm-backup-init.sh) :
#   .sm-backup-root    marqueur : sans lui, aucun script n'écrit (montage absent = refus)
#   wal/               segments WAL archivés (compressés .gz), 0700
#   base/<horodatage>/ sauvegardes physiques vérifiées (lecture seule), 0700
#   restore-test/      environnements temporaires des tests de restauration, 0700
#   state/             état interne (vérifications, curseur WAL), 0700
#   locks/             verrous (exécutions concurrentes), 0700
#   logs/              journaux des opérations (un fichier par opération et par mois), 0700
#   status/            statuts JSON lus par la console et la supervision, 0755 / 0644
#   logical/<entreprise>/<horodatage>-<exécution>/  sauvegardes LOGIQUES d'une entreprise (R11,
#                      ADR-0058) : CSV compressés, manifeste, SHA256SUMS, marqueur .complete, 0700
# Racine : 0711 (traversable pour lire status/, jamais listable par un autre compte).

SM_SCHEMA_VERSION=1
SM_ROOT_MARKER=".sm-backup-root"
SM_TEST_MARKER=".sm-restore-test"
SM_STAMP_RE='^[0-9]{8}T[0-9]{6}Z$'
SM_RUN_ID_RE='^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$'
SM_SEGMENT_RE='^[0-9A-F]{24}$'

# --- Temps -----------------------------------------------------------------------------------
# SM_BACKUP_NOW_EPOCH : instant de référence imposé (tests de rétention et de supervision).
sm_now() { if [[ -n "${SM_BACKUP_NOW_EPOCH:-}" ]]; then echo "$SM_BACKUP_NOW_EPOCH"; else date -u +%s; fi; }
sm_iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
sm_stamp() { date -u -d "@$1" +%Y%m%dT%H%M%SZ; }
# AAAAMMJJTHHMMSSZ → époque (busybox : format « AAAA-MM-JJ HH:MM:SS » accepté).
sm_stamp_epoch() {
  local s="$1"
  [[ "$s" =~ $SM_STAMP_RE ]] || return 1
  date -u -d "${s:0:4}-${s:4:2}-${s:6:2} ${s:9:2}:${s:11:2}:${s:13:2}" +%s
}

# --- Journal ---------------------------------------------------------------------------------
SM_OP="${SM_OP:-backup}"
SM_LOG_FILE="${SM_LOG_FILE:-}"
sm_log() {
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) [$SM_OP] $*"
  echo "$line" >&2
  if [[ -n "$SM_LOG_FILE" ]]; then echo "$line" >>"$SM_LOG_FILE" 2>/dev/null || true; fi
}
SM_FAIL_MSG=""
sm_die() { SM_FAIL_MSG="$*"; sm_log "ERREUR : $*"; exit "${SM_DIE_CODE:-1}"; }
sm_need() {
  local c
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || sm_die "commande requise absente : $c"; done
}

# --- JSON (écriture seulement ; une clé par ligne, lisible par sm_json_get) ------------------
sm_js() { # chaîne JSON
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/ }"
  s="${s//$'\t'/ }"
  printf '"%s"' "$s"
}
sm_jn() { if [[ -n "${1:-}" && "$1" =~ ^-?[0-9]+$ ]]; then printf '%s' "$1"; else printf 'null'; fi; }
sm_jb() { if [[ "${1:-}" == true || "${1:-}" == 1 ]]; then printf 'true'; else printf 'false'; fi; }
# Valeur d'une clé de premier niveau (fichiers écrits par ces scripts : une clé par ligne).
sm_json_get() {
  [[ -f "$1" ]] || return 0
  sed -n -e "s/^  \"$2\": \"\(.*\)\",\{0,1\}\$/\1/p" -e t -e "s/^  \"$2\": \([^\",]*\),\{0,1\}\$/\1/p" "$1" | head -n 1
}

# --- Racine et arborescence ------------------------------------------------------------------
# Racine RÉELLE des sauvegardes. Refus si absente, lien symbolique, ou sans marqueur (montage
# manquant : écrire dans le système de fichiers du conteneur serait une perte silencieuse).
sm_root() {
  local root="${SM_BACKUP_DIR:-}"
  [[ -n "$root" ]] || sm_die "SM_BACKUP_DIR doit être défini"
  [[ "$root" == /* ]] || sm_die "SM_BACKUP_DIR doit être un chemin absolu : $root"
  [[ -d "$root" && ! -L "$root" ]] || sm_die "racine des sauvegardes absente ou lien symbolique : $root"
  [[ -f "$root/$SM_ROOT_MARKER" ]] || sm_die "marqueur $SM_ROOT_MARKER absent dans $root (lancer sm-backup-init.sh ; montage manquant ?)"
  realpath "$root"
}
sm_subdirs() { echo "wal base restore-test state state/backups locks logs status logical"; }

# Initialise la journalisation d'une opération : logs/<op>-AAAAMM.log.
sm_log_init() {
  SM_OP="$1"
  SM_LOG_FILE="$SM_ROOT/logs/$1-$(date -u +%Y%m).log"
}

# --- Verrou global (aucune opération lourde concurrente) -------------------------------------
# flock si disponible (busybox Alpine, util-linux), sinon répertoire + PID (repli vérifié).
SM_LOCK_DIR=""
sm_lock() { # nom attente_secondes
  local name="$1" wait="${2:-0}" file="$SM_ROOT/locks/$1.lock"
  if command -v flock >/dev/null 2>&1; then
    exec 9>>"$file"
    # flock -n en boucle : le flock de busybox (Alpine) n'a pas d'option -w.
    local until_ts=$(( $(date -u +%s) + wait ))
    until flock -x -n 9; do
      if (( $(date -u +%s) >= until_ts )); then
        SM_DIE_CODE=75 sm_die "verrou « $name » détenu par une autre opération (attente ${wait} s dépassée)"
      fi
      sleep 2
    done
    return 0
  fi
  local dir="$file.d" deadline=$(( $(date -u +%s) + wait )) pid
  while ! mkdir "$dir" 2>/dev/null; do
    pid="$(cat "$dir/pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      sm_log "verrou orphelin (PID $pid absent) : repris"
      rm -f "$dir/pid"; rmdir "$dir" 2>/dev/null || true
      continue
    fi
    (( $(date -u +%s) < deadline )) || SM_DIE_CODE=75 sm_die "verrou « $name » détenu par une autre opération (PID ${pid:-?})"
    sleep 2
  done
  echo $$ >"$dir/pid"
  SM_LOCK_DIR="$dir"
}
sm_unlock() {
  if [[ -n "$SM_LOCK_DIR" ]]; then rm -f "$SM_LOCK_DIR/pid"; rmdir "$SM_LOCK_DIR" 2>/dev/null || true; SM_LOCK_DIR=""; fi
}

# --- Statuts d'opération ---------------------------------------------------------------------
# status/<op>.json : dernière exécution (running | success | failed | interrupted | refused) ;
# status/<op>.success.json : dernière réussite. Écriture atomique (temporaire + renommage).
SM_STATUS_EXTRA=""   # lignes « "clé": valeur » propres à l'opération, séparées par des virgules
SM_RUN_ID=""
SM_START_EPOCH=""
SM_OP_DONE=0
SM_OP_CLEANUP=""     # fonction appelée à la sortie (succès ou échec)

sm_status_write() { # opération statut message
  local op="$1" st="$2" msg="$3" now end dur dest tmp
  now="$(sm_now)"
  end=""; dur=""
  if [[ "$st" != running ]]; then end="$now"; dur=$(( now - SM_START_EPOCH )); fi
  dest="$SM_ROOT/status/$op.json"
  tmp="$SM_ROOT/status/.$op.json.tmp.$$"
  {
    echo "{"
    echo "  \"schema\": $SM_SCHEMA_VERSION,"
    echo "  \"operation\": $(sm_js "$op"),"
    echo "  \"status\": $(sm_js "$st"),"
    echo "  \"run_id\": $(sm_js "$SM_RUN_ID"),"
    echo "  \"started_at\": $(sm_js "$(sm_iso "$SM_START_EPOCH")"),"
    echo "  \"started_at_epoch\": $SM_START_EPOCH,"
    if [[ -n "$end" ]]; then
      echo "  \"finished_at\": $(sm_js "$(sm_iso "$end")"),"
    else
      echo "  \"finished_at\": null,"
    fi
    echo "  \"finished_at_epoch\": $(sm_jn "$end"),"
    echo "  \"duration_seconds\": $(sm_jn "$dur"),"
    if [[ -n "$SM_STATUS_EXTRA" ]]; then printf '%s,\n' "$SM_STATUS_EXTRA"; fi
    echo "  \"message\": $(sm_js "$msg")"
    echo "}"
  } >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
  if [[ "$st" == success ]]; then
    cp "$dest" "$tmp" && chmod 0644 "$tmp" && mv -f "$tmp" "$SM_ROOT/status/$op.success.json"
  fi
}
# Ajoute « "clé": valeur » (valeur déjà au format JSON) aux champs du statut.
sm_extra() {
  if [[ -n "$SM_STATUS_EXTRA" ]]; then SM_STATUS_EXTRA+=$',\n'; fi
  SM_STATUS_EXTRA+="  \"$1\": $2"
}

sm__on_exit() {
  local code="$1"
  trap - EXIT INT TERM
  if [[ -n "$SM_OP_CLEANUP" ]]; then "$SM_OP_CLEANUP" "$code" || true; fi
  if (( SM_OP_DONE == 0 )) && [[ -n "$SM_START_EPOCH" ]]; then
    local st=failed
    (( code == 130 || code == 143 )) && st=interrupted
    (( code == 75 )) && st=refused
    sm_status_write "$SM_OP" "$st" "${SM_FAIL_MSG:-échec (code $code)}" 2>/dev/null || true
    sm_log "fin : $st (code $code)"
  fi
  sm_unlock
  exit "$code"
}
# Échec inattendu (set -e) : la commande et la ligne sont conservées pour le statut et le journal.
sm__on_err() { if [[ -z "$SM_FAIL_MSG" ]]; then SM_FAIL_MSG="échec inattendu ligne $1 : ${2:0:200}"; fi; }
sm_on_signal() { SM_FAIL_MSG="interrompu par le signal $1"; sm_log "$SM_FAIL_MSG"; exit $(( 128 + $2 )); }

# Début d'opération : identifiant, statut « running », pièges de sortie et de signaux.
sm_op_begin() { # opération
  SM_OP="$1"
  SM_START_EPOCH="$(sm_now)"
  SM_RUN_ID="$(sm_stamp "$SM_START_EPOCH")-$(sm_random_hex)"
  trap 'sm__on_exit $?' EXIT
  set -o errtrace
  trap 'sm__on_err "$LINENO" "$BASH_COMMAND"' ERR
  trap 'sm_on_signal INT 2' INT
  trap 'sm_on_signal TERM 15' TERM
  sm_status_write "$SM_OP" running "en cours"
  sm_log "début (exécution $SM_RUN_ID)"
}
sm_op_success() { # message
  sm_status_write "$SM_OP" success "$1"
  SM_OP_DONE=1
  sm_log "fin : réussite — $1"
}
# Échec « contrôlé » : statut failed avec un message, code de sortie donné (défaut 1).
sm_op_fail() { # message [code]
  sm_status_write "$SM_OP" failed "$1"
  SM_OP_DONE=1
  sm_log "fin : ÉCHEC — $1"
  exit "${2:-1}"
}

# Arrêt d'un processus ET de ses descendants (pg_basebackup crée un processus de flux WAL qui
# survivrait à son parent tué par un signal). /proc seulement : busybox et coreutils.
sm_children() {
  local p st
  for p in /proc/[0-9]*; do
    st="$(cat "$p/stat" 2>/dev/null)" || continue
    st="${st##*) }"
    read -r _ ppid _ <<<"$st"
    [[ "$ppid" == "$1" ]] && echo "${p#/proc/}"
  done
  return 0
}
sm_kill_tree() { # pid [signal]
  local c
  for c in $(sm_children "$1"); do sm_kill_tree "$c" "${2:-TERM}"; done
  kill "-${2:-TERM}" "$1" 2>/dev/null || true
}

sm_random_hex() { od -An -N8 -tx1 /dev/urandom | tr -d ' \n'; }

# --- Espace disque ---------------------------------------------------------------------------
sm_df_kb() { df -Pk "$1" | awk 'NR==2 {print $2, $4}'; }  # « total disponible » en Kio
sm_free_kb() { sm_df_kb "$1" | awk '{print $2}'; }
sm_free_percent() { sm_df_kb "$1" | awk '{ if ($1 > 0) printf "%d", ($2 * 100) / $1; else print 0 }'; }
# Réserve à conserver libre : max(SM_BACKUP_RESERVE_MB, SM_BACKUP_RESERVE_PERCENT % du disque).
sm_reserve_kb() {
  local total pct mb
  total="$(sm_df_kb "$1" | awk '{print $1}')"
  pct="${SM_BACKUP_RESERVE_PERCENT:-5}"; mb="${SM_BACKUP_RESERVE_MB:-1024}"
  local a=$(( mb * 1024 )) b=$(( total * pct / 100 ))
  if (( a > b )); then echo "$a"; else echo "$b"; fi
}
sm_size_bytes() { du -sk "$1" 2>/dev/null | awk '{print $1 * 1024}'; }

# --- Segments WAL ----------------------------------------------------------------------------
SM_WAL_SEGMENT_SIZE="${SM_WAL_SEGMENT_SIZE:-16777216}"
sm_segs_per_id() { echo $(( 0x100000000 / SM_WAL_SEGMENT_SIZE )); }
# Nom (24 hex) → « tli numéro » (numéro absolu du segment).
sm_seg_parse() {
  local n="${1:0:24}"
  [[ "$n" =~ $SM_SEGMENT_RE ]] || return 1
  echo "$(( 16#${n:0:8} )) $(( 16#${n:8:8} * $(sm_segs_per_id) + 16#${n:16:8} ))"
}
sm_seg_name() { # tli numéro
  local per; per="$(sm_segs_per_id)"
  printf '%08X%08X%08X' "$1" $(( $2 / per )) $(( $2 % per ))
}
# LSN « X/Y » → numéro du segment qui contient l'octet.
sm_lsn_seg() {
  local hi="${1%%/*}" lo="${1##*/}"
  echo $(( ( (16#$hi << 32) + 16#$lo ) / SM_WAL_SEGMENT_SIZE ))
}
sm_lsn_bytes() { local hi="${1%%/*}" lo="${1##*/}"; echo $(( (16#$hi << 32) + 16#$lo )); }

# Fichier archivé d'un segment ou d'un historique (compressé, ou ancien format non compressé).
sm_wal_file() { # racine nom
  if [[ -f "$1/wal/$2.gz" ]]; then echo "$1/wal/$2.gz"
  elif [[ -f "$1/wal/$2" ]]; then echo "$1/wal/$2"
  else return 1; fi
}
sm_wal_cat() { case "$1" in *.gz) gzip -dc "$1" ;; *) cat "$1" ;; esac; }

# --- Sauvegardes de base ---------------------------------------------------------------------
# Horodatages des sauvegardes complètes (répertoires conformes, avec backup_info.json), triés.
sm_backups() {
  local d n
  for d in "$SM_ROOT"/base/*; do
    [[ -d "$d" && ! -L "$d" ]] || continue
    n="${d##*/}"
    [[ "$n" =~ $SM_STAMP_RE ]] || continue
    [[ -f "$d/backup_info.json" ]] || continue
    echo "$n"
  done | sort
}
# Une sauvegarde est vérifiée si pg_verifybackup a réussi à sa création ET si la dernière
# vérification quotidienne n'a pas échoué (state/backups/<horodatage>.verify).
sm_backup_verified() {
  local s="$1" v
  [[ "$(sm_json_get "$SM_ROOT/base/$s/backup_info.json" verified)" == true ]] || return 1
  v="$(cut -d' ' -f1 "$SM_ROOT/state/backups/$s.verify" 2>/dev/null || true)"
  [[ "$v" != failed ]]
}
sm_latest_verified_backup() {
  local s last=""
  for s in $(sm_backups); do if sm_backup_verified "$s"; then last="$s"; fi; done
  echo "$last"
}

# --- Gardes de chemins -----------------------------------------------------------------------
# Vrai si $1 et $2 désignent le même répertoire ou si l'un contient l'autre.
sm_paths_overlap() {
  local a="${1%/}/" b="${2%/}/"
  [[ "$a" == "$b"* || "$b" == "$a"* ]]
}
# Répertoire de données de l'instance de PRODUCTION de ce conteneur / hôte (PGDATA), s'il existe.
sm_production_pgdata() {
  if [[ -n "${PGDATA:-}" && -d "$PGDATA" ]]; then realpath "$PGDATA"; fi
}

# --- Inventaire (statut agrégé pour la console : aucun contenu, des compteurs) ---------------
sm_inventory_write() {
  local s n=0 nv=0 total=0 sz first="" last="" lastv="" oldest_end="" wal_n=0 wal_b=0 f free pct samefs=false
  for s in $(sm_backups); do
    n=$(( n + 1 ))
    sz="$(sm_json_get "$SM_ROOT/base/$s/backup_info.json" size_bytes)"
    total=$(( total + ${sz:-0} ))
    [[ -z "$first" ]] && first="$s"
    last="$s"
    if sm_backup_verified "$s"; then
      nv=$(( nv + 1 )); lastv="$s"
      [[ -z "$oldest_end" ]] && oldest_end="$(sm_json_get "$SM_ROOT/base/$s/backup_info.json" finished_at_epoch)"
    fi
  done
  for f in "$SM_ROOT"/wal/*; do
    [[ -f "$f" ]] || continue
    wal_n=$(( wal_n + 1 ))
    wal_b=$(( wal_b + $(stat -c %s "$f") ))
  done
  free="$(sm_free_kb "$SM_ROOT")"; pct="$(sm_free_percent "$SM_ROOT")"
  local pgd; pgd="$(sm_production_pgdata)"
  if [[ -n "$pgd" ]] && [[ "$(stat -c %d "$pgd")" == "$(stat -c %d "$SM_ROOT")" ]]; then samefs=true; fi
  local stale=0 d
  for d in "$SM_ROOT"/restore-test/*; do [[ -e "$d" ]] && stale=$(( stale + 1 )); done
  local dest="$SM_ROOT/status/inventory.json" tmp="$SM_ROOT/status/.inventory.json.tmp.$$" now
  now="$(sm_now)"
  {
    echo "{"
    echo "  \"schema\": $SM_SCHEMA_VERSION,"
    echo "  \"generated_at\": $(sm_js "$(sm_iso "$now")"),"
    echo "  \"generated_at_epoch\": $now,"
    echo "  \"base_backups\": $n,"
    echo "  \"base_backups_verified\": $nv,"
    echo "  \"base_backups_bytes\": $total,"
    echo "  \"oldest_backup\": $(sm_js "$first"),"
    echo "  \"latest_backup\": $(sm_js "$last"),"
    echo "  \"latest_verified_backup\": $(sm_js "$lastv"),"
    echo "  \"restorable_from_epoch\": $(sm_jn "$oldest_end"),"
    echo "  \"wal_files\": $wal_n,"
    echo "  \"wal_bytes\": $wal_b,"
    echo "  \"retention_days\": $(sm_jn "${SM_BACKUP_RETENTION_DAYS:-30}"),"
    echo "  \"disk_free_bytes\": $(( free * 1024 )),"
    echo "  \"disk_free_percent\": $pct,"
    echo "  \"same_filesystem_as_data\": $samefs,"
    echo "  \"restore_test_dirs\": $stale"
    echo "}"
  } >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
}

# --- Connexion à l'instance de production (rôle de réplication dédié) -------------------------
# Par défaut : socket locale du conteneur, rôle stockmanager_backup (REPLICATION, sans
# superutilisateur). Mot de passe éventuel : PGPASSFILE (0600), jamais en argument.
sm_pg_env() {
  export PGHOST="${PGHOST:-/var/run/postgresql}"
  export PGPORT="${PGPORT:-5432}"
  export PGUSER="${SM_BACKUP_PGUSER:-stockmanager_backup}"
  export PGDATABASE="${PGDATABASE:-${SM_BACKUP_PGDATABASE:-stockmanager}}"
  export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"
  export PGAPPNAME="stockmanager-$SM_OP"
}
sm_psql() { psql -X -w -v ON_ERROR_STOP=1 -Atq "$@"; }

# --- Chaîne WAL d'une sauvegarde -------------------------------------------------------------
# Liste les segments nécessaires pour restaurer depuis (tli, segment de départ) jusqu'au dernier
# segment archivé, sur le chemin de timelines que suit PostgreSQL (recovery_target_timeline =
# latest : chaque segment est lu sur la timeline la plus récente commencée avant lui).
# Sortie, une ligne par élément :
#   T <nombre de timelines du chemin>
#   S <tli> <numéro> <fichier>     segment présent
#   M <tli> <numéro>               segment MANQUANT
#   H <fichier historique>         historique de timeline requis (présent)
#   E <message>                    anomalie (historique absent, timeline hors chemin…)
sm_wal_chain() { # tli_départ segment_départ
  local b_tli="$1" b_start="$2" f n t num latest="$1" begin parent lsn i idx=-1 end=-1 s tli
  local -A present=()
  for f in "$SM_ROOT"/wal/*; do
    [[ -f "$f" ]] || continue
    n="${f##*/}"; n="${n%.gz}"
    if [[ "$n" =~ ^[0-9A-F]{24}$ ]]; then
      read -r t num < <(sm_seg_parse "$n")
      present["$t:$num"]="$f"
      (( t > latest )) && latest=$t
    elif [[ "$n" =~ ^([0-9A-F]{8})\.history$ ]]; then
      t=$(( 16#${BASH_REMATCH[1]} )); (( t > latest )) && latest=$t
    fi
  done
  local -a ptli=() pbegin=()
  if (( latest > b_tli )); then
    local hname hfile
    hname="$(printf '%08X.history' "$latest")"
    if hfile="$(sm_wal_file "$SM_ROOT" "$hname")"; then
      begin=0
      while IFS=$'\t ' read -r parent lsn _; do
        [[ "$parent" =~ ^[0-9]+$ ]] || continue
        ptli+=("$parent"); pbegin+=("$begin")
        begin="$(sm_lsn_seg "$lsn")"
      done < <(sm_wal_cat "$hfile" 2>/dev/null || echo "illisible")
      ptli+=("$latest"); pbegin+=("$begin")
      echo "H $hfile"
    else
      echo "E historique de timeline absent : $hname"
      ptli=("$b_tli"); pbegin=(0)
    fi
  else
    ptli=("$b_tli"); pbegin=(0)
  fi
  for i in "${!ptli[@]}"; do
    (( ptli[i] == b_tli )) && idx=$i
    if (( i > 0 && ptli[i] > 1 )); then
      local h; h="$(printf '%08X.history' "${ptli[$i]}")"
      if f="$(sm_wal_file "$SM_ROOT" "$h")"; then echo "H $f"; else echo "E historique de timeline absent : $h"; fi
    fi
  done
  echo "T ${#ptli[@]}"
  if (( idx < 0 )); then echo "E timeline $b_tli de la sauvegarde absente de l'historique de la timeline $latest"; return 0; fi
  local key
  for key in "${!present[@]}"; do
    t="${key%%:*}"; num="${key##*:}"
    for (( i = idx; i < ${#ptli[@]}; i++ )); do
      if (( ptli[i] == t && num > end )); then end=$num; fi
    done
  done
  if (( end < b_start )); then echo "E aucun segment archivé depuis le début de la sauvegarde"; return 0; fi
  for (( s = b_start; s <= end; s++ )); do
    tli="${ptli[$idx]}"
    for (( i = idx; i < ${#ptli[@]}; i++ )); do (( pbegin[i] <= s )) && tli="${ptli[$i]}"; done
    f="${present["$tli:$s"]:-}"
    if [[ -n "$f" ]]; then echo "S $tli $s $f"; else echo "M $tli $s"; fi
  done
}

# --- Préparation d'une restauration (copie d'une sauvegarde, configuration de récupération) --
# Variables lues : R_BACKUP R_DATA R_PORT R_SOCKET R_LISTEN R_ACTION R_TARGET_KIND R_TARGET
#                  R_HBA_LOCAL_ONLY (1 : pg_hba.conf réduit à la socket locale)
sm_restore_prepare() {
  local here x
  here="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
  for x in "$R_DATA" "$R_SOCKET" "$SM_ROOT" "$here"; do
    [[ "$x" != *"'"* && "$x" != *'"'* ]] || sm_die "chemin refusé (guillemet) : $x"
  done
  mkdir "$R_DATA"
  chmod 0700 "$R_DATA"
  cp -a "$SM_ROOT/base/$R_BACKUP/." "$R_DATA/"
  chmod -R u+w "$R_DATA"
  chmod 0700 "$R_DATA"
  rm -f "$R_DATA/backup_info.json" "$R_DATA/postmaster.pid" "$R_DATA/postmaster.opts"
  [[ -f "$R_DATA/postgresql.conf" ]] || : >"$R_DATA/postgresql.conf"
  if [[ "${R_HBA_LOCAL_ONLY:-0}" == 1 ]]; then
    printf '# Copie de restauration StockManager : socket locale seulement (répertoire 0700).\nlocal all all trust\n' >"$R_DATA/pg_hba.conf"
  fi
  {
    echo ""
    echo "# --- Restauration StockManager ($(date -u +%Y-%m-%dT%H:%M:%SZ), sauvegarde $R_BACKUP) ---"
    echo "restore_command = 'SM_BACKUP_DIR=$SM_ROOT $here/sm-wal-restore.sh %f %p'"
    echo "recovery_target_timeline = 'latest'"
    case "$R_TARGET_KIND" in
      time) echo "recovery_target_time = '$R_TARGET'" ;;
      lsn) echo "recovery_target_lsn = '$R_TARGET'" ;;
      name) echo "recovery_target_name = '$R_TARGET'" ;;
      immediate) echo "recovery_target = 'immediate'" ;;
      end) ;;
    esac
    echo "recovery_target_action = '$R_ACTION'"
    echo "archive_mode = off"
    echo "archive_command = ''"
    echo "primary_conninfo = ''"
    echo "primary_slot_name = ''"
    echo "port = $R_PORT"
    echo "listen_addresses = '$R_LISTEN'"
    echo "unix_socket_directories = '$R_SOCKET'"
    echo "hba_file = '$R_DATA/pg_hba.conf'"
    echo "ident_file = '$R_DATA/pg_ident.conf'"
    echo "cluster_name = 'sm-restore'"
    echo "max_parallel_workers_per_gather = 0"
  } >>"$R_DATA/postgresql.auto.conf"
  [[ -f "$R_DATA/pg_ident.conf" ]] || : >"$R_DATA/pg_ident.conf"
  touch "$R_DATA/recovery.signal"
}
