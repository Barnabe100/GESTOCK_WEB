#!/usr/bin/env bash
# Test RÉEL de restauration (mensuel) — preuve que les sauvegardes se restaurent.
#
#   SM_BACKUP_DIR=/backups sm-restore-test.sh [--wait SECONDES] [--backup HORODATAGE]
#       [--keep] [--no-switch] [--port 55432]
#   SM_BACKUP_DIR=/backups sm-restore-test.sh --cleanup /backups/restore-test/<exécution>
#
# Vérifications des restaurations demandées dans la console (R11, ADR-0058), sur la MÊME copie
# isolée et avec les MÊMES contrôles (statut status/restore_verify.json, --op restore_verify) :
#   --target-time "AAAA-MM-JJ HH:MM:SSZ"   copie à un instant (PITR) : sauvegarde vérifiée
#                                         terminée avant, chaîne WAL contrôlée, cible atteinte ;
#   --tenant-export UUID --export-run-id HEX   puis export LOGIQUE de l'entreprise depuis la copie
#                                         (sauvegarde « pitr » matérialisée, jamais appliquée
#                                         sans vérification) ;
#   --tenant-apply UUID --artifact DIR --operation UUID   épreuve de la restauration d'UNE
#                                         entreprise sur la copie : empreinte de TOUTES les données
#                                         avant / après (autres entreprises et plateforme
#                                         identiques, tables conservées de l'entreprise
#                                         identiques), contrôles de sm-tenant-apply.sh, relecture
#                                         exacte de chaque table restaurée.
#
# 1. sauvegarde : la plus récente VÉRIFIÉE (ou --backup) ;
# 2. production, en LECTURE avec le rôle de réplication : révision Alembic, bascule du segment
#    WAL courant (pg_switch_wal, aucune donnée modifiée ; --no-switch pour s'en passer) et
#    attente de son archivage — le point attendu de la restauration ;
# 3. copie dans restore-test/<exécution>/data (marqueur .sm-restore-test créé AVANT la copie),
#    instance temporaire : socket dans ce répertoire seulement (aucune écoute réseau), port
#    distinct, archivage DÉSACTIVÉ, aucune connexion de réplication, pg_hba réduit à la socket ;
#    rejeu des WAL archivés jusqu'au dernier, promotion ;
# 4. contrôles sur la COPIE : point de restauration atteint (perte de données mesurée), révision
#    Alembic identique, pg_amcheck (tables et index, unicité), clés étrangères (aucune ligne
#    orpheline), contraintes CHECK, invariants métier (restore-checks.sql), RLS activée ET forcée
#    sur toute table d'entreprise, rôle applicatif sans BYPASSRLS, rien de visible sans contexte,
#    rien d'une autre entreprise avec le contexte d'une entreprise (chaque entreprise), écriture
#    dans une autre entreprise refusée par la RLS, isolement de la copie ;
# 5. RTO mesuré (copie → base contrôlée) ; rapport JSON (logs/ et status/restore_test.json) ;
# 6. arrêt de la copie, puis suppression SOUS GARDES (sauf --keep) : identifiant d'exécution
#    conforme, chemin réel EXACT restore-test/<exécution>, marqueur présent et identique, pas de
#    lien symbolique ni de point de montage, même système de fichiers, instance arrêtée, aucun
#    recouvrement avec la production — au moindre doute : suppression REFUSÉE (code 3).
# La production n'est jamais écrite ; la copie ne s'y connecte jamais.
set -euo pipefail
HERE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
. "$HERE/lib.sh"

WAIT=0 BACKUP="" KEEP=0 SWITCH=1 PORT="${SM_RESTORE_TEST_PORT:-55432}" CLEANUP=""
OPNAME=restore_test TARGET_TIME="" EXPORT_TENANT="" EXPORT_RUN="" APPLY_TENANT="" ARTIFACT="" OPERATION=""
while (( $# )); do
  case "$1" in
    --wait) WAIT="$2"; shift 2 ;;
    --backup) BACKUP="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --no-switch) SWITCH=0; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --cleanup) CLEANUP="$2"; shift 2 ;;
    --op) OPNAME="$2"; shift 2 ;;
    --target-time) TARGET_TIME="$2"; shift 2 ;;
    --tenant-export) EXPORT_TENANT="$2"; shift 2 ;;
    --export-run-id) EXPORT_RUN="$2"; shift 2 ;;
    --tenant-apply) APPLY_TENANT="$2"; shift 2 ;;
    --artifact) ARTIFACT="$2"; shift 2 ;;
    --operation) OPERATION="$2"; shift 2 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
sm_need pg_ctl psql pg_amcheck gzip
SM_ROOT="$(sm_root)"
[[ "$OPNAME" =~ ^(restore_test|restore_verify)$ ]] || sm_die "--op : restore_test ou restore_verify"
sm_log_init "$OPNAME"
UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
[[ -z "$EXPORT_TENANT" || ( "$EXPORT_TENANT" =~ $UUID_RE && "$EXPORT_RUN" =~ ^[0-9a-f]{8}$ ) ]] || sm_die "--tenant-export / --export-run-id invalides"
[[ -z "$APPLY_TENANT" || ( "$APPLY_TENANT" =~ $UUID_RE && "$OPERATION" =~ $UUID_RE && -n "$ARTIFACT" ) ]] || sm_die "--tenant-apply / --artifact / --operation invalides"
[[ -z "$EXPORT_TENANT" || -z "$APPLY_TENANT" ]] || sm_die "--tenant-export et --tenant-apply : une seule épreuve par copie"
TARGET_EPOCH=""
if [[ -n "$TARGET_TIME" ]]; then
  re='^([0-9]{4}-[0-9]{2}-[0-9]{2})[T ]([0-9]{2}:[0-9]{2}:[0-9]{2})( ?(Z|\+00|\+00:00|UTC))$'
  [[ "$TARGET_TIME" =~ $re ]] || sm_die "--target-time : « AAAA-MM-JJ HH:MM:SSZ » (UTC explicite)"
  TARGET_EPOCH="$(date -u -d "${BASH_REMATCH[1]} ${BASH_REMATCH[2]}" +%s)" || sm_die "--target-time : date invalide"
  TARGET_TIME="${BASH_REMATCH[1]} ${BASH_REMATCH[2]}+00"
fi
TESTROOT="$SM_ROOT/restore-test"
[[ -d "$TESTROOT" && ! -L "$TESTROOT" ]] || sm_die "répertoire restore-test absent ou lien symbolique"
TESTROOT="$(realpath "$TESTROOT")"
[[ "$TESTROOT" == "$SM_ROOT/restore-test" ]] || sm_die "restore-test ne se trouve pas sous la racine réelle"
APP_ROLE="${SM_DB_APP_ROLE:-stockmanager_app}"
DBNAME="${SM_BACKUP_PGDATABASE:-stockmanager}"

# --- Suppression sécurisée -------------------------------------------------------------------
# Renvoie 0 seulement si TOUTES les conditions sont réunies ; sinon journalise la raison.
REFUSAL=""
refuse() { REFUSAL="$*"; sm_log "SUPPRESSION REFUSÉE : $*"; return 1; }
safe_remove() { # répertoire identifiant_attendu
  local dir="$1" run="$2" real marker data prod mp
  [[ "$run" =~ $SM_RUN_ID_RE ]] || { refuse "identifiant d'exécution non conforme : $run"; return 1; }
  [[ "${dir##*/}" == "$run" ]] || { refuse "le nom du répertoire ne correspond pas à l'exécution"; return 1; }
  [[ -e "$dir" ]] || { refuse "répertoire absent : $dir"; return 1; }
  [[ ! -L "$dir" && -d "$dir" ]] || { refuse "$dir n'est pas un répertoire réel"; return 1; }
  real="$(realpath "$dir")"
  [[ "$real" == "$TESTROOT/$run" ]] || { refuse "chemin réel inattendu : $real"; return 1; }
  [[ "$(stat -c %d "$real")" == "$(stat -c %d "$TESTROOT")" ]] || { refuse "autre système de fichiers"; return 1; }
  marker="$real/$SM_TEST_MARKER"
  [[ -f "$marker" && ! -L "$marker" ]] || { refuse "marqueur absent"; return 1; }
  grep -qx "run_id=$run" "$marker" && grep -qx "path=$real" "$marker" || { refuse "marqueur non conforme"; return 1; }
  [[ "$(stat -c %u "$marker")" == "$(id -u)" ]] || { refuse "marqueur d'un autre propriétaire"; return 1; }
  data="$real/data"
  if [[ -e "$data/postmaster.pid" ]]; then
    if pg_ctl -D "$data" status >/dev/null 2>&1; then refuse "instance de test encore démarrée"; return 1; fi
    refuse "postmaster.pid présent (arrêt non confirmé)"; return 1
  fi
  prod="$(sm_production_pgdata)"
  if [[ -n "$prod" ]] && sm_paths_overlap "$real" "$prod"; then refuse "recouvre la production ($prod)"; return 1; fi
  if [[ -n "$prod" && -d "$data" ]] && [[ "$(stat -c '%d:%i' "$data")" == "$(stat -c '%d:%i' "$prod")" ]]; then
    refuse "même répertoire que la production"; return 1
  fi
  for x in "$SM_ROOT" "$SM_ROOT/base" "$SM_ROOT/wal" "/" "$TESTROOT"; do
    [[ "$real" != "$x" ]] || { refuse "chemin protégé : $x"; return 1; }
  done
  if [[ -n "$(find "$real" -type l | head -n 1)" ]]; then refuse "lien symbolique dans le répertoire"; return 1; fi
  while read -r _ _ _ _ mp _; do
    if [[ "$mp" == "$real" || "$mp" == "$real/"* ]]; then refuse "point de montage dans le répertoire : $mp"; return 1; fi
  done </proc/self/mountinfo
  chmod -R u+w "$real"
  rm -rf -- "$real"
  [[ ! -e "$real" ]] || { refuse "suppression incomplète"; return 1; }
  sm_log "environnement de test supprimé : $real"
  return 0
}

if [[ -n "$CLEANUP" ]]; then
  sm_lock backup "$WAIT"
  if safe_remove "$CLEANUP" "${CLEANUP##*/}"; then exit 0; else exit 3; fi
fi

# --- Préparation -----------------------------------------------------------------------------
sm_lock backup "$WAIT"
sm_op_begin "$OPNAME"
sm_pg_env
[[ "$PORT" =~ ^[0-9]+$ ]] || sm_op_fail "--port invalide"
(( PORT != PGPORT )) || sm_op_fail "le port de test doit différer de celui de la production"

if [[ -z "$BACKUP" && -n "$TARGET_EPOCH" ]]; then
  # PITR : la plus récente sauvegarde VÉRIFIÉE terminée avant la cible.
  for s in $(sm_backups); do
    sm_backup_verified "$s" || continue
    (( $(sm_json_get "$SM_ROOT/base/$s/backup_info.json" finished_at_epoch) <= TARGET_EPOCH )) || continue
    BACKUP="$s"
  done
  [[ -n "$BACKUP" ]] || sm_op_fail "aucune sauvegarde vérifiée terminée avant la cible $TARGET_TIME"
fi
if [[ -z "$BACKUP" ]]; then BACKUP="$(sm_latest_verified_backup)"; fi
[[ -n "$BACKUP" ]] || sm_op_fail "aucune sauvegarde vérifiée : test impossible"
[[ "$BACKUP" =~ $SM_STAMP_RE ]] && sm_backup_verified "$BACKUP" || sm_op_fail "sauvegarde $BACKUP absente ou non vérifiée"
INFO="$SM_ROOT/base/$BACKUP/backup_info.json"
SM_WAL_SEGMENT_SIZE="$(sm_json_get "$INFO" wal_segment_size)"
sm_extra backup "$(sm_js "$BACKUP")"
sm_extra backup_finished_at "$(sm_js "$(sm_json_get "$INFO" finished_at)")"

need_kb=$(( $(sm_json_get "$INFO" size_bytes) / 1024 * 12 / 10 ))
free_kb="$(sm_free_kb "$TESTROOT")"; reserve_kb="$(sm_reserve_kb "$TESTROOT")"
(( free_kb - need_kb >= reserve_kb )) || sm_op_fail "espace insuffisant pour la copie (${free_kb} Kio libres, ${need_kb} Kio requis + réserve ${reserve_kb} Kio)"

# Production, lecture seule (rôle de réplication) : point attendu de la restauration.
PROD_REVISION="$(sm_psql -c "SELECT version_num FROM alembic_version")" || sm_op_fail "production injoignable avec le rôle $PGUSER"
lag_row="$(sm_psql -c "SELECT coalesce(extract(epoch FROM now() - last_archived_time)::bigint, -1), coalesce(last_archived_wal, '') FROM pg_stat_archiver")"
ARCHIVE_LAG="${lag_row%%|*}"
if (( SWITCH )); then
  EXPECTED_LSN="$(sm_psql -c "SELECT pg_switch_wal()")" || sm_op_fail "pg_switch_wal refusé (droit du rôle de réplication ? sm-backup-role.sh)"
else
  EXPECTED_LSN=""
fi
if [[ -n "$EXPECTED_LSN" ]]; then
  EXPECTED_WAL="$(sm_psql -c "SELECT pg_walfile_name('$EXPECTED_LSN'::pg_lsn)")"
else
  EXPECTED_WAL="${lag_row#*|}"
fi
deadline=$(( $(date -u +%s) + ${SM_RESTORE_TEST_ARCHIVE_WAIT:-900} ))
if (( SWITCH )); then
  until [[ "$(sm_psql -c "SELECT coalesce(last_archived_wal, '') >= '$EXPECTED_WAL' FROM pg_stat_archiver")" == t ]]; do
    (( $(date -u +%s) < deadline )) || sm_op_fail "le segment $EXPECTED_WAL n'a pas été archivé à temps : archivage en défaut"
    sleep 2
  done
fi
sm_log "point attendu : ${EXPECTED_LSN:-dernier segment archivé} ($EXPECTED_WAL), révision $PROD_REVISION"

DIR="$TESTROOT/$SM_RUN_ID"
DATA="$DIR/data"
[[ ! -e "$DIR" ]] || sm_op_fail "$DIR existe déjà"
mkdir "$DIR"
chmod 0700 "$DIR"
printf 'stockmanager-restore-test\nrun_id=%s\npath=%s\ncreated_at=%s\nbackup=%s\n' \
  "$SM_RUN_ID" "$(realpath "$DIR")" "$(sm_iso "$(sm_now)")" "$BACKUP" >"$DIR/$SM_TEST_MARKER"
chmod 0400 "$DIR/$SM_TEST_MARKER"
PROD="$(sm_production_pgdata)"
if [[ -n "$PROD" ]] && sm_paths_overlap "$DATA" "$PROD"; then sm_op_fail "la copie recouvrirait la production"; fi

STARTED=0
cleanup() {
  local code="$1"
  if (( STARTED )); then pg_ctl -D "$DATA" -m fast -w -t 120 stop >/dev/null 2>&1 || pg_ctl -D "$DATA" -m immediate stop >/dev/null 2>&1 || true; fi
  if (( KEEP )); then sm_log "environnement conservé (--keep) : $DIR"; return 0; fi
  if [[ -d "$DIR" ]] && ! safe_remove "$DIR" "$SM_RUN_ID"; then
    sm_log "l'environnement $DIR doit être examiné puis supprimé à la main (sm-restore-test.sh --cleanup)"
  fi
  return "$code"
}
SM_OP_CLEANUP=cleanup

T0="$(date -u +%s)"
R_BACKUP="$BACKUP" R_DATA="$DATA" R_PORT="$PORT" R_SOCKET="$DIR" R_LISTEN="" R_ACTION=promote
R_TARGET_KIND=end R_TARGET="" R_HBA_LOCAL_ONLY=1
if [[ -n "$TARGET_TIME" ]]; then
  (( TARGET_EPOCH <= $(date -u +%s) )) || sm_op_fail "la cible $TARGET_TIME est dans le futur"
  R_TARGET_KIND=time R_TARGET="$TARGET_TIME"
fi
sm_restore_prepare
{
  echo "shared_buffers = '${SM_RESTORE_TEST_SHARED_BUFFERS:-128MB}'"
  echo "logging_collector = off"
  echo "hot_standby = off"
} >>"$DATA/postgresql.auto.conf"
sm_log "copie de $BACKUP → $DATA ; instance temporaire (port $PORT, socket seule)"
pg_ctl -D "$DATA" -l "$DIR/restore.log" -w -t "${SM_RESTORE_TEST_TIMEOUT:-14400}" start >/dev/null 9>&- \
  || sm_op_fail "démarrage de la copie impossible (voir $DIR/restore.log, conserver avec --keep)"
STARTED=1
cq() { psql -X -w -h "$DIR" -p "$PORT" -v ON_ERROR_STOP=1 -Atq "$@"; }
until [[ "$(cq -U "$PGUSER" -d postgres -c 'SELECT pg_is_in_recovery()' 2>/dev/null)" == f ]]; do
  pg_ctl -D "$DATA" status >/dev/null 2>&1 || sm_op_fail "la copie s'est arrêtée pendant le rejeu des WAL (archive corrompue ? voir $DIR/restore.log)"
  (( $(date -u +%s) - T0 < ${SM_RESTORE_TEST_TIMEOUT:-14400} )) || sm_op_fail "rejeu des WAL trop long"
  sleep 1
done
T_PROMOTED="$(date -u +%s)"
SUPER="$(cq -U "$PGUSER" -d postgres -c "SELECT rolname FROM pg_roles WHERE oid = 10")"
sq() { cq -U "$SUPER" -d "$DBNAME" "$@"; }
aq() { cq -U "$APP_ROLE" -d "$DBNAME" "$@"; }

# --- Contrôles -------------------------------------------------------------------------------
CHECKS="" FAILED=0
check() { # nom réussi(0/1) détail
  local ok=true
  if [[ "$2" != 0 ]]; then ok=false; FAILED=$(( FAILED + 1 )); sm_log "CONTRÔLE ÉCHOUÉ : $1 — $3"; else sm_log "contrôle réussi : $1 — $3"; fi
  CHECKS+="${CHECKS:+, }{\"name\": $(sm_js "$1"), \"ok\": $ok, \"detail\": $(sm_js "$3")}"
}

REPLAY="$(cq -U "$SUPER" -d postgres -c "SELECT pg_last_wal_replay_lsn()")"
if [[ -n "$TARGET_TIME" ]]; then
  # PostgreSQL refuse de promouvoir si la cible n'est pas atteinte (FATAL « recovery ended
  # before configured recovery target was reached ») : la promotion EST la preuve ; le point
  # d'arrêt exact est relevé dans le journal de la copie.
  stop="$(grep -m1 -o 'recovery stopping [^"]*' "$DIR/restore.log" 2>/dev/null | head -c 200 || true)"
  LOST=""
  check recovery_point_reached "$([[ -n "$stop" ]] && echo 0 || echo 1)" "cible $TARGET_TIME : ${stop:-arrêt de la récupération non relevé dans le journal}"
elif [[ -n "$EXPECTED_LSN" ]]; then
  LOST="$(cq -U "$SUPER" -d postgres -c "SELECT greatest(0, pg_wal_lsn_diff('$EXPECTED_LSN', '$REPLAY'))::bigint")"
  check recovery_point_reached "$([[ "$LOST" == 0 ]] && echo 0 || echo 1)" "rejoué jusqu'à $REPLAY (attendu $EXPECTED_LSN, octets non restaurés : $LOST)"
else
  LOST=""
  check recovery_point_reached 0 "rejoué jusqu'à $REPLAY (dernier segment archivé)"
fi
REV="$(sq -c "SELECT version_num FROM alembic_version" 2>/dev/null || echo '?')"
check alembic_revision "$([[ "$REV" == "$PROD_REVISION" ]] && echo 0 || echo 1)" "copie $REV, production $PROD_REVISION"

iso="$(cq -U "$SUPER" -d postgres -c "SELECT current_setting('archive_mode') || '|' || current_setting('listen_addresses') || '|' || current_setting('primary_conninfo')")"
check copy_isolated "$([[ "$iso" == "off||" ]] && echo 0 || echo 1)" "archive_mode, listen_addresses, primary_conninfo = « $iso »"

if amout="$(pg_amcheck -h "$DIR" -p "$PORT" -U "$SUPER" --all --install-missing --heapallindexed --parent-check 2>&1)"; then
  check pg_amcheck 0 "tables et index B-tree de toutes les bases cohérents (heapallindexed, parent-check)"
else
  check pg_amcheck 1 "$(echo "$amout" | head -n 5 | tr '\n' ' ')"
fi

fk_sql="SELECT format('SELECT %L, count(*) FROM %s c WHERE %s AND NOT EXISTS (SELECT 1 FROM %s p WHERE %s);',
    con.conname, con.conrelid::regclass,
    (SELECT string_agg(format('c.%I IS NOT NULL', a.attname), ' AND ') FROM unnest(con.conkey) k(n)
       JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n),
    con.confrelid::regclass,
    (SELECT string_agg(format('p.%I = c.%I', pa.attname, ca.attname), ' AND ')
       FROM unnest(con.conkey, con.confkey) k(c_att, p_att)
       JOIN pg_attribute ca ON ca.attrelid = con.conrelid AND ca.attnum = k.c_att
       JOIN pg_attribute pa ON pa.attrelid = con.confrelid AND pa.attnum = k.p_att))
  FROM pg_constraint con WHERE con.contype = 'f' AND con.connamespace = 'public'::regnamespace ORDER BY 1"
# Exécute des requêtes générées « libellé|écarts » ; échec si une requête manque ou échoue.
run_generated() { # sql_générateur [préambule] → « attendu|obtenu|écarts|erreur »
  local gen out expected got bad
  gen="$(sq -c "$1")" || { echo "0|0||génération impossible"; return; }
  expected="$(grep -c . <<<"$gen" || true)"
  out="$(sq -F '|' <<<"${2:-}$gen" 2>&1)" || true
  got="$(grep -c '^[^|]*|[0-9]*$' <<<"$out" || true)"
  bad="$(awk -F'|' 'NF == 2 && $2 != 0 {print $1 "=" $2}' <<<"$out" | tr '\n' ' ')"
  echo "$expected|$got|$bad|$(grep -v '^[^|]*|[0-9]*$' <<<"$out" | head -n 2 | tr '\n|' '  ')"
}
IFS='|' read -r fk_exp fk_got fk_bad fk_err <<<"$(run_generated "$fk_sql")"
check foreign_keys "$([[ -z "$fk_bad" && "$fk_exp" -gt 0 && "$fk_exp" == "$fk_got" ]] && echo 0 || echo 1)" "$fk_got/$fk_exp clé(s) étrangère(s) contrôlée(s) ; orphelins : ${fk_bad:-aucun}${fk_err:+ ; erreur : $fk_err}"

ck_sql="SELECT format('SELECT %L, count(*) FROM %s WHERE NOT (%s);', conname, conrelid::regclass,
    regexp_replace(regexp_replace(pg_get_constraintdef(oid), '^CHECK ', ''), ' NOT VALID\$', ''))
  FROM pg_constraint WHERE contype = 'c' AND conrelid <> 0 AND connamespace = 'public'::regnamespace ORDER BY 1"
IFS='|' read -r ck_exp ck_got ck_bad ck_err <<<"$(run_generated "$ck_sql")"
check check_constraints "$([[ -z "$ck_bad" && "$ck_exp" -gt 0 && "$ck_exp" == "$ck_got" ]] && echo 0 || echo 1)" "$ck_got/$ck_exp contrainte(s) CHECK revérifiée(s) ; écarts : ${ck_bad:-aucun}${ck_err:+ ; erreur : $ck_err}"
# Unicité revérifiée SANS les index (lecture séquentielle de la table) : un index altéré ne
# masque pas un doublon. Index d'expression exclus (comptés), index partiels avec leur prédicat.
uq_sql="SELECT format('SELECT %L, count(*) FROM (SELECT 1 FROM %s WHERE %s GROUP BY %s HAVING count(*) > 1) d;',
    i.indexrelid::regclass, i.indrelid::regclass,
    concat_ws(' AND ', CASE WHEN i.indnullsnotdistinct THEN NULL ELSE
        (SELECT string_agg(format('%I IS NOT NULL', a.attname), ' AND ')
           FROM unnest(i.indkey::int2[]) WITH ORDINALITY k(n, o)
           JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.n WHERE k.o <= i.indnkeyatts) END,
      '(' || pg_get_expr(i.indpred, i.indrelid) || ')', 'true'),
    (SELECT string_agg(format('%I', a.attname), ', ')
       FROM unnest(i.indkey::int2[]) WITH ORDINALITY k(n, o)
       JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.n WHERE k.o <= i.indnkeyatts))
  FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid
  WHERE i.indisunique AND i.indexprs IS NULL AND c.relnamespace = 'public'::regnamespace ORDER BY 1"
noscan=$'SET enable_indexscan = off;\nSET enable_indexonlyscan = off;\nSET enable_bitmapscan = off;\n'
IFS='|' read -r uq_exp uq_got uq_bad uq_err <<<"$(run_generated "$uq_sql" "$noscan")"
uq_expr="$(sq -c "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid WHERE i.indisunique AND i.indexprs IS NOT NULL AND c.relnamespace = 'public'::regnamespace")"
check unique_constraints "$([[ -z "$uq_bad" && "$uq_exp" -gt 0 && "$uq_exp" == "$uq_got" ]] && echo 0 || echo 1)" "$uq_got/$uq_exp index unique(s) revérifié(s) par lecture de la table ; doublons : ${uq_bad:-aucun} ; index d'expression non revérifiés : $uq_expr${uq_err:+ ; erreur : $uq_err}"
nv="$(sq -c "SELECT count(*) FROM pg_constraint WHERE NOT convalidated AND connamespace = 'public'::regnamespace")"
check constraints_validated "$([[ "$nv" == 0 ]] && echo 0 || echo 1)" "$nv contrainte(s) NOT VALID"

inv_exp="$(grep -c '^SELECT ' "$HERE/restore-checks.sql")"
inv_out="$(sq -F '|' -f "$HERE/restore-checks.sql" 2>&1)" || true
inv_got="$(grep -c '^[^|]*|[0-9]*$' <<<"$inv_out" || true)"
inv_bad="$(awk -F'|' 'NF == 2 && $2 != 0 {print $1 "=" $2}' <<<"$inv_out" | tr '\n' ' ')"
inv_err="$(grep -v '^[^|]*|[0-9]*$' <<<"$inv_out" | head -n 2 | tr '\n' ' ' || true)"
check business_invariants "$([[ -z "$inv_bad" && "$inv_exp" == "$inv_got" ]] && echo 0 || echo 1)" "$inv_got/$inv_exp invariant(s) ; écarts : ${inv_bad:-aucun}${inv_err:+ ; erreur : $inv_err}"

# RLS : toute table portant tenant_id — RLS activée ET forcée, ou inaccessible au rôle applicatif.
TABLES_SQL="SELECT c.oid, c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
  WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
unsafe="$(sq -c "SELECT string_agg(relname, ' ') FROM ($TABLES_SQL) t
  WHERE NOT (relrowsecurity AND relforcerowsecurity) AND has_table_privilege('$APP_ROLE', oid, 'SELECT')")"
check rls_forced "$([[ -z "$unsafe" ]] && echo 0 || echo 1)" "tables d'entreprise lisibles sans RLS forcée : ${unsafe:-aucune}"
attrs="$(sq -c "SELECT rolsuper::text || rolbypassrls::text FROM pg_roles WHERE rolname = '$APP_ROLE'")"
check app_role_no_bypass "$([[ "$attrs" == falsefalse ]] && echo 0 || echo 1)" "rôle $APP_ROLE : superutilisateur/BYPASSRLS = $attrs"

readable="$(sq -c "SELECT string_agg(relname, ' ' ORDER BY relname) FROM ($TABLES_SQL) t
  WHERE has_table_privilege('$APP_ROLE', oid, 'SELECT')")"
union_all() { # filtre SQL
  local t out=""
  for t in $readable; do out+="${out:+ UNION ALL }SELECT '$t', count(*) FROM \"$t\" WHERE $1"; done
  echo "$out"
}
if [[ -n "$readable" ]]; then
  leak="$(aq -F '|' -c "$(union_all true)" 2>&1 | awk -F'|' '$2 != 0' | tr '\n' ' ')" || true
  check rls_no_context "$([[ -z "$leak" ]] && echo 0 || echo 1)" "sans contexte d'entreprise, lignes visibles : ${leak:-aucune} ($(wc -w <<<"$readable") tables)"
  mapfile -t TENANTS < <(sq -c "SELECT id FROM tenants ORDER BY created_at LIMIT ${SM_RESTORE_TEST_MAX_TENANTS:-20}")
  ntenants="$(sq -c "SELECT count(*) FROM tenants")"
  cross="" seen=0
  for t in "${TENANTS[@]}"; do
    out="$(aq -F '|' -c "SET app.tenant_id = '$t'" -c "$(union_all "tenant_id IS DISTINCT FROM '$t'")" 2>&1)" || { cross+="$t:erreur "; continue; }
    bad="$(echo "$out" | awk -F'|' '$2 != 0' | tr '\n' ' ')"
    [[ -z "$bad" ]] || cross+="$t:$bad "
    seen=$(( seen + 1 ))
  done
  check rls_tenant_isolation "$([[ -z "$cross" && "$seen" -gt 0 ]] && echo 0 || echo 1)" "$seen entreprise(s) sur $ntenants contrôlée(s) : lignes d'une autre entreprise visibles : ${cross:-aucune}"
  # Écriture dans une autre entreprise (copie seulement) : refus attendu de la RLS.
  if [[ "$(sq -c "SELECT coalesce(has_table_privilege('$APP_ROLE', to_regclass('public.catalog_categories'), 'INSERT'), false)")" == t && ${#TENANTS[@]} -gt 0 ]]; then
    other="$(sq -c "SELECT id FROM tenants WHERE id <> '${TENANTS[0]}' LIMIT 1")"
    other="${other:-$(sq -c "SELECT gen_random_uuid()")}"
    werr="$(aq -c "SET app.tenant_id = '${TENANTS[0]}'" -c "INSERT INTO catalog_categories (id, tenant_id, name, is_active) VALUES (gen_random_uuid(), '$other', 'sm-restore-test', true)" 2>&1 || true)"
    upd="$(aq -c "SET app.tenant_id = '${TENANTS[0]}'" -c "WITH u AS (UPDATE catalog_categories SET name = name WHERE tenant_id = '$other' RETURNING 1) SELECT count(*) FROM u" 2>&1 || true)"
    check rls_cross_tenant_write "$([[ "$werr" == *"row-level security"* && "$upd" == 0 ]] && echo 0 || echo 1)" "insertion refusée : $([[ "$werr" == *"row-level security"* ]] && echo oui || echo "NON ($werr)") ; mise à jour d'une autre entreprise : $upd ligne(s)"
  else
    check rls_cross_tenant_write 1 "contrôle impossible (aucune entreprise ou droit INSERT absent)"
  fi
else
  check rls_no_context 1 "aucune table d'entreprise lisible par $APP_ROLE : rôle absent de la copie ?"
fi

# --- Épreuves des restaurations demandées dans la console (R11) -------------------------------
copy_env() { env PGHOST="$DIR" PGPORT="$PORT" PGDATABASE="$DBNAME" "$@"; }
if [[ -n "$EXPORT_TENANT" ]]; then
  if (( FAILED == 0 )); then
    if out="$(copy_env SM_BACKUP_DIR="$SM_ROOT" "$HERE/sm-tenant-export.sh" --tenant "$EXPORT_TENANT" --run-id "$EXPORT_RUN" \
          --source pitr --source-point "${TARGET_TIME:-}" --host "$DIR" --port "$PORT" 2>&1)"; then
      art="$(grep '^ARTIFACT ' <<<"$out" | tail -n 1)"
      check tenant_export 0 "${art#ARTIFACT }"
      sm_extra artifact "$(sm_js "${art#ARTIFACT }")"
    else
      check tenant_export 1 "$(tail -n 2 <<<"$out" | tr '\n' ' ')"
    fi
  else
    check tenant_export 1 "copie non conforme : aucun export"
  fi
fi
if [[ -n "$APPLY_TENANT" ]]; then
  if (( FAILED == 0 )); then
    FP="$DIR/fingerprint"
    mkdir "$FP"
    copy_env PGUSER="$SUPER" "$HERE/sm-fingerprint.sh" >"$FP/before" || check fingerprint 1 "empreinte initiale impossible"
    report="$DIR/apply-report.json"
    if copy_env SM_BACKUP_DIR="$SM_ROOT" "$HERE/sm-tenant-apply.sh" --tenant "$APPLY_TENANT" --artifact "$ARTIFACT" \
         --operation "$OPERATION" --host "$DIR" --port "$PORT" --report "$report" >"$FP/apply.out" 2>&1; then
      check tenant_apply 0 "archive appliquée à la copie (transaction validée)"
    else
      check tenant_apply 1 "$(grep -o '"error": "[^"]*"' "$report" 2>/dev/null || tail -n 2 "$FP/apply.out" | tr '\n' ' ')"
    fi
    [[ -f "$report" ]] && sm_extra apply_report "$(cat "$report")"
    copy_env PGUSER="$SUPER" "$HERE/sm-fingerprint.sh" >"$FP/after" || check fingerprint 1 "empreinte finale impossible"
    # Tables que l'application peut modifier pour CETTE entreprise : remplacées (droit DELETE du
    # rôle de restauration), compteurs fusionnés, journal (une entrée ajoutée).
    restorer="${SM_TENANT_RESTORE_PGUSER:-stockmanager_tenant_restore}"
    mapfile -t writable < <(sq -c "SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
       AND (has_table_privilege('$restorer', c.oid, 'DELETE') OR has_table_privilege('$restorer', c.oid, 'UPDATE') OR has_table_privilege('$restorer', c.oid, 'INSERT'))")
    others="$(diff <(grep -v "|$APPLY_TENANT|" "$FP/before") <(grep -v "|$APPLY_TENANT|" "$FP/after") | grep -c '^[<>]' || true)"
    check other_tenants_unchanged "$([[ "$others" == 0 && -s "$FP/after" ]] && echo 0 || echo 1)" \
      "$(grep -vc "|$APPLY_TENANT|" "$FP/after") empreinte(s) (autres entreprises et tables de la plateforme) : $others différence(s)"
    kept_bad=""
    while IFS='|' read -r t _ _ _; do
      w=0; for x in "${writable[@]}"; do [[ "$x" == "$t" ]] && w=1; done
      (( w )) || kept_bad+="$t "
    done < <(diff <(grep "|$APPLY_TENANT|" "$FP/before") <(grep "|$APPLY_TENANT|" "$FP/after") | sed -n 's/^> //p')
    check kept_tables_unchanged "$([[ -z "$kept_bad" && -s "$FP/before" && -s "$FP/after" ]] && echo 0 || echo 1)" \
      "tables conservées de l'entreprise (abonnements, licences, postes, notifications d'échéance…) modifiées : ${kept_bad:-aucune}"
    if [[ -f "$ARTIFACT/manifest.tsv" ]] && sha="$(copy_env SM_BACKUP_DIR="$SM_ROOT" "$HERE/sm-tenant-export.sh" --tenant "$APPLY_TENANT" --sha-only --host "$DIR" --port "$PORT" 2>/dev/null)"; then
      purged="$(grep -o '"purged": "[^"]*"' "$report" 2>/dev/null || true)"
      diffs="$(diff <(awk '{print $2"\t"$3"\t"$4}' <<<"$sha") <(grep -v '^#' "$ARTIFACT/manifest.tsv" | cut -f1-3) | awk -F'\t' '/^< / {sub(/^< /, "", $1); print $1}' | tr '\n' ' ')"
      # Écarts admis : compteurs (jamais en arrière), effacements à 30 jours réappliqués.
      unexpected=""
      for t in $diffs; do
        case "$t" in
          document_sequences) ;;
          member_notifications|restaurant_orders) [[ "$purged" == *"=0"*"=0"* ]] && unexpected+="$t " ;;
          *) unexpected+="$t " ;;
        esac
      done
      check restored_exact "$([[ -z "$unexpected" ]] && echo 0 || echo 1)" \
        "relecture de chaque table restaurée : identique à la sauvegarde, sauf ${diffs:-rien} (admis : compteurs au maximum, effacements à 30 jours) ; écarts inattendus : ${unexpected:-aucun}"
    else
      check restored_exact 1 "relecture de l'entreprise restaurée impossible"
    fi
  else
    check tenant_apply 1 "copie non conforme : épreuve non jouée"
  fi
fi

T_END="$(date -u +%s)"
RTO=$(( T_END - T0 ))
RTO_TARGET="${SM_RTO_TARGET_SECONDS:-14400}"
check rto "$([[ $RTO -le $RTO_TARGET ]] && echo 0 || echo 1)" "${RTO} s (objectif ${RTO_TARGET} s ; rejeu ${T_PROMOTED}-${T0} = $(( T_PROMOTED - T0 )) s)"
stats="$(sq -c "SELECT (SELECT count(*) FROM tenants) || '|' || (SELECT count(*) FROM sites) || '|' || pg_database_size(current_database())")"
IFS='|' read -r n_tenants n_sites db_bytes <<<"$stats"

sm_extra run_dir "$(sm_js "$DIR")"
sm_extra kept "$(sm_jb "$KEEP")"
sm_extra rto_seconds "$RTO"
sm_extra replay_seconds "$(( T_PROMOTED - T0 ))"
sm_extra archive_lag_seconds_at_start "$(sm_jn "$ARCHIVE_LAG")"
sm_extra wal_bytes_not_recovered "$(sm_jn "$LOST")"
sm_extra alembic_revision "$(sm_js "$REV")"
sm_extra tenants "$(sm_jn "$n_tenants")"
sm_extra sites "$(sm_jn "$n_sites")"
sm_extra database_bytes "$(sm_jn "$db_bytes")"
sm_extra checks_failed "$FAILED"
sm_extra checks "[$CHECKS]"
cp_report() { cp "$SM_ROOT/status/$OPNAME.json" "$SM_ROOT/logs/${OPNAME//_/-}-$SM_RUN_ID.json" 2>/dev/null || true; }

# Arrêt et suppression sous gardes AVANT le statut final (un refus de suppression est un échec).
pg_ctl -D "$DATA" -m fast -w -t 120 stop >/dev/null || sm_op_fail "arrêt de la copie impossible"
STARTED=0
SM_OP_CLEANUP=""
if (( KEEP )); then
  sm_extra cleanup "\"kept\""
  sm_log "environnement conservé (--keep) : $DIR — supprimer ensuite avec --cleanup $DIR"
elif safe_remove "$DIR" "$SM_RUN_ID"; then
  sm_extra cleanup "\"removed\""
else
  sm_extra cleanup "\"refused\""
  sm_extra cleanup_refusal "$(sm_js "$REFUSAL")"
  sm_status_write "$OPNAME" failed "suppression de l'environnement refusée : $REFUSAL"; SM_OP_DONE=1; cp_report
  exit 3
fi
if (( FAILED > 0 )); then
  sm_status_write "$OPNAME" failed "$FAILED contrôle(s) en échec sur la copie restaurée"; SM_OP_DONE=1; cp_report
  sm_log "fin : ÉCHEC — $FAILED contrôle(s)"
  exit 1
fi
sm_op_success "restauration de $BACKUP vérifiée en ${RTO} s ($(grep -o '"ok": true' <<<"$CHECKS" | wc -l | tr -d ' ') contrôles réussis)"
cp_report
