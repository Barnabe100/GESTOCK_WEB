#!/usr/bin/env bash
# Exercice complet de sauvegarde / restauration (R11-0) — preuve automatisée, base JETABLE.
#
#   ops/backup/sm-backup-drill.sh [--mode native|docker] [--keep] [--quick]
#
# --quick (mise au point seulement, jamais en CI) : étapes 1, 2, une sauvegarde vérifiée, puis
#          l'étape 13 (console et agent).
#
# native : cluster PostgreSQL 16 temporaire (127.0.0.1, port libre), scripts lancés en
#          utilisateur postgres (CI : image postgres:16-bookworm).
# docker : projet Docker Compose DE TEST (nom unique, port libre, volume propre) construit avec
#          docker-compose.yml + docker-compose.backup.yml (image postgres:16-alpine, busybox) :
#          données créées AVANT l'activation, conteneur recréé avec le complément de sauvegarde,
#          empreinte des données, volume et port vérifiés inchangés. Ne touche JAMAIS le projet
#          « stockmanager » ni son volume.
# Étapes communes : schéma réel (Alembic) et trois entreprises par l'API (magasins, dépôts,
# utilisateur partagé, stock, ventes) ; racine et rôle de réplication ; sauvegarde complète
# PENDANT des écritures concurrentes (et refus d'une seconde exécution) ; vérification ; PITR à
# un instant T (données avant présentes, après absentes, isolement des entreprises) ; test de
# restauration complet ; interruption (signal, arrêt brutal) ; sauvegarde corrompue ; WAL
# manquant ; WAL corrompu ; échec et retard d'archivage ; base inactive ; rétention (simulation
# par défaut, dernière vérifiée protégée) ; gardes de suppression ; statut de la console ;
# console et agent (R11) : planification exécutée, sauvegarde d'une entreprise, restauration
# vérifiée sur copie isolée, double approbation, application pendant des écritures d'autres
# entreprises, retour arrière, archive altérée refusée, PITR d'une entreprise, restauration
# globale non appliquée par l'agent, échec puis nouvelle tentative.
# Rapport : ${SM_DRILL_REPORT:-$TMPDIR/sm-backup-drill-report.json} ; code 0 = tout réussi.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MODE=native KEEP=0 QUICK=0
while (( $# )); do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --quick) QUICK=1; shift ;;
    --keep) KEEP=1; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
[[ "$MODE" == native || "$MODE" == docker ]] || { echo "--mode native|docker" >&2; exit 2; }
TMPROOT="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$TMPROOT/sm-backup-drill.XXXXXX")"
chmod 0755 "$WORK"
REPORT="${SM_DRILL_REPORT:-$TMPROOT/sm-backup-drill-report.json}"
RESULTS=()
METRICS=""
log() { echo "[exercice:$MODE] $*"; }
jstr() { local s="$1"; s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\n'/ }"; printf '"%s"' "$s"; }
write_report() {
  local joined="" r
  for r in "${RESULTS[@]}"; do joined+="${joined:+, }$r"; done
  printf '{\n  "status": "%s",\n  "mode": "%s",\n  "steps": [%s],\n  "metrics": {%s}\n}\n' "$1" "$MODE" "$joined" "$METRICS" >"$REPORT"
}
pass() { RESULTS+=("{\"step\": $(jstr "$1"), \"ok\": true, \"detail\": $(jstr "$2")}"); log "RÉUSSI : $1 — $2"; }
fail() {
  RESULTS+=("{\"step\": $(jstr "$1"), \"ok\": false, \"detail\": $(jstr "$2")}"); log "ÉCHEC : $1 — $2"
  if [[ -s "$WORK/agent.out" ]]; then tail -n 80 "$WORK/agent.out" | sed 's/^/[agent] /'; fi
  write_report failed; exit 1
}
metric() { METRICS+="${METRICS:+, }\"$1\": ${2:-null}"; }
free_port() {
  local code='import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'
  if command -v python3 >/dev/null; then python3 -c "$code"; else (cd "$REPO/backend" && uv run --frozen python -c "$code"); fi
}
PORT="$(free_port)"
XENV=()

# --- Environnement selon le mode -------------------------------------------------------------
if [[ "$MODE" == native ]]; then
  BIN="${SM_PG_BINDIR:-$(pg_config --bindir 2>/dev/null || echo /usr/lib/postgresql/16/bin)}"
  export PATH="$BIN:$PATH"
  PRIMARY="$WORK/primary" BK="$WORK/backups" SCRIPTS="$WORK/bin" SOCK="$WORK"
  as_pg() { if (( EUID == 0 )); then runuser -u postgres -- env PATH="$PATH" "$@"; else "$@"; fi; }
  mkdir -p "$SCRIPTS" "$BK"
  cp "$REPO"/ops/backup/*.sh "$REPO"/ops/backup/*.sql "$SCRIPTS/"
  if (( EUID == 0 )); then chown -R postgres "$WORK"; fi
  ENVV=(SM_BACKUP_DIR="$BK" PGHOST="$SOCK" PGPORT="$PORT" PGDATA="$PRIMARY" SM_SUPERUSER=stockmanager SM_BACKUP_RESERVE_MB=16 SM_BACKUP_ARCHIVE_RESERVE_MB=16)
  bk() { local cmd="$1"; shift; as_pg env "${ENVV[@]}" "${XENV[@]}" "$SCRIPTS/$cmd" "$@"; }
  bk_sh() { as_pg env "${ENVV[@]}" "${XENV[@]}" bash -c "$1"; }
  ROOT_IN="$BK" ROOT_HOST="$BK" SCRIPTS_IN="$SCRIPTS"
  cleanup() {
    local d
    for d in "$BK"/pitr/data "$BK"/restore-test/*/data "$PRIMARY"; do as_pg pg_ctl -D "$d" -m immediate stop >/dev/null 2>&1 || true; done
    if (( KEEP )); then log "conservé : $WORK"; else rm -rf "$WORK"; fi
  }
else
  PROJECT="sm-backup-drill-$(date -u +%s)-$$"
  BKH="$WORK/backups"
  mkdir -p "$BKH"
  export SM_BACKUP_HOST_DIR="$BKH" SM_DRILL_PORT="$PORT"
  DC=(docker compose -p "$PROJECT" --project-directory "$REPO" -f "$REPO/docker-compose.yml" -f "$REPO/ops/backup/tests/docker-compose.drill.yml")
  DCB=(docker compose -p "$PROJECT" --project-directory "$REPO" -f "$REPO/docker-compose.yml" -f "$REPO/docker-compose.backup.yml" -f "$REPO/ops/backup/tests/docker-compose.drill.yml")
  ENVV=(SM_BACKUP_RESERVE_MB=16 SM_BACKUP_ARCHIVE_RESERVE_MB=16)
  bk() {
    local cmd="$1"; shift; local e=() v
    for v in "${ENVV[@]}" "${XENV[@]}"; do e+=(-e "$v"); done
    "${DCB[@]}" exec -T -u postgres "${e[@]}" db "/opt/stockmanager/backup/$cmd" "$@"
  }
  bk_sh() {
    local e=() v
    for v in "${ENVV[@]}" "${XENV[@]}"; do e+=(-e "$v"); done
    "${DCB[@]}" exec -T -u postgres "${e[@]}" db bash -c "$1"
  }
  ROOT_IN=/backups ROOT_HOST="$BKH" SOCK=/var/run/postgresql PRIMARY=/var/lib/postgresql/data
  SCRIPTS_IN=/opt/stockmanager/backup
  cleanup() {
    # Projet de TEST uniquement (nom unique) : son volume jetable est supprimé.
    "${DCB[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    docker run --rm -v "$WORK:/w" postgres:16-alpine rm -rf /w/backups >/dev/null 2>&1 || true
    if (( KEEP )); then log "conservé : $WORK"; else rm -rf "$WORK" 2>/dev/null || true; fi
  }
fi
trap cleanup EXIT
export PGPASSWORD=stockmanager
sqlp() { psql -X -h 127.0.0.1 -p "$PORT" -U stockmanager -d stockmanager -v ON_ERROR_STOP=1 -Atq "$@"; }
status_get() { sed -n -e "s/^  \"$2\": \"\(.*\)\",\{0,1\}\$/\1/p" -e t -e "s/^  \"$2\": \([^\",]*\),\{0,1\}\$/\1/p" "$ROOT_HOST/status/$1.json" | head -n 1; }
# Contrôles DANS la racine (répertoires 0700 de l'uid du serveur : jamais listés depuis l'hôte
# en mode docker, où un contrôle depuis l'hôte réussirait à vide).
exists_in() { bk_sh "[[ -e '$ROOT_IN/$1' ]]"; }
empty_in() { bk_sh "[[ -d '$ROOT_IN/$1' && -z \"\$(ls -A '$ROOT_IN/$1')\" ]]"; }
partials() { bk_sh "ls -d '$ROOT_IN'/base/.partial-* >/dev/null 2>&1"; }
mode_in() { bk_sh "stat -c %a '$ROOT_IN/$1'"; }
LAST_OUT=""
expect_code() { # code_attendu étape commande…
  local want="$1" step="$2"; shift 2
  local got=0 out
  out="$("$@" 2>&1)" || got=$?
  [[ "$got" == "$want" ]] || fail "$step" "code $got (attendu $want) : $(echo "$out" | tail -n 4 | tr '\n' ' ')"
  LAST_OUT="$out"
}
switch_and_wait() { # clôt le segment courant et attend son archivage
  local w; w="$(sqlp -c "SELECT pg_walfile_name(pg_switch_wal())")"
  for _ in $(seq 1 120); do
    [[ "$(sqlp -c "SELECT coalesce(last_archived_wal, '') >= '$w' FROM pg_stat_archiver")" == t ]] && return 0
    sleep 1
  done
  return 1
}

# --- 1. Instance et données ------------------------------------------------------------------
URL="postgresql+psycopg://%s@127.0.0.1:$PORT/stockmanager"
export SM_ENVIRONMENT=development SM_JWT_SECRET="exercice-de-restauration-uniquement-0123456789"
SM_TEST_MIGRATION_DATABASE_URL="$(printf "$URL" stockmanager:stockmanager)"
SM_TEST_DATABASE_URL="$(printf "$URL" stockmanager_app:stockmanager_app)"
SM_TEST_PLATFORM_DATABASE_URL="$(printf "$URL" stockmanager_platform:stockmanager_platform)"
SM_TEST_PURGE_DATABASE_URL="$(printf "$URL" stockmanager_purge:stockmanager_purge)"
export SM_TEST_MIGRATION_DATABASE_URL SM_TEST_DATABASE_URL SM_TEST_PLATFORM_DATABASE_URL SM_TEST_PURGE_DATABASE_URL
export SM_MIGRATION_DATABASE_URL="$SM_TEST_MIGRATION_DATABASE_URL" SM_DATABASE_URL="$SM_TEST_DATABASE_URL"
export SM_PLATFORM_DATABASE_URL="$SM_TEST_PLATFORM_DATABASE_URL" SM_PURGE_DATABASE_URL="$SM_TEST_PURGE_DATABASE_URL"
scenario() { (cd "$REPO/backend" && uv run --frozen python -m tests.backup_scenario "$@"); }
migrate() { (cd "$REPO/backend" && uv run --frozen alembic upgrade head >/dev/null 2>&1 && uv run --frozen stockmanager catalog sync >/dev/null); }
# Python du système, sinon celui du backend (uv) : l'image postgres:16-bookworm n'en a pas.
py() { if command -v python3 >/dev/null; then python3 "$@"; else (cd "$REPO/backend" && uv run --frozen python "$@"); fi; }
sha_of() { py -c 'import json,sys; print(json.load(sys.stdin)["sha256"])'; }

if [[ "$MODE" == native ]]; then
  log "1. cluster temporaire (port $PORT)"
  as_pg initdb -D "$PRIMARY" -U stockmanager --auth=trust --encoding=UTF8 --locale=C.UTF-8 >/dev/null
  as_pg tee -a "$PRIMARY/postgresql.auto.conf" >/dev/null <<CONF
port = $PORT
listen_addresses = '127.0.0.1'
unix_socket_directories = '$SOCK'
wal_level = replica
archive_mode = on
archive_command = 'SM_BACKUP_DIR=$BK $SCRIPTS/sm-wal-archive.sh %p %f'
archive_timeout = 300
CONF
  as_pg pg_ctl -D "$PRIMARY" -l "$WORK/primary.log" -w start >/dev/null
  psql -X -h 127.0.0.1 -p "$PORT" -U stockmanager -d postgres -Atqc "CREATE DATABASE stockmanager"
  for role in stockmanager_app stockmanager_platform stockmanager_purge; do
    sqlp -c "CREATE ROLE $role LOGIN PASSWORD '$role' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
  done
  migrate
  scenario seed >"$WORK/seed.json"
  pass instance "cluster temporaire, schéma $(sqlp -c 'SELECT version_num FROM alembic_version'), $(sqlp -c 'SELECT count(*) FROM tenants') entreprises, $(sqlp -c 'SELECT count(*) FROM sites') sites"
else
  log "1. projet Compose de test $PROJECT (port $PORT) SANS le complément de sauvegarde"
  "${DC[@]}" up -d --wait db >/dev/null
  VOLUME="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")"
  migrate
  scenario seed >"$WORK/seed.json"
  FP_BEFORE="$(scenario fingerprint | sha_of)"
  CID_BEFORE="$("${DC[@]}" ps -q db)"
  log "   activation de docker-compose.backup.yml (conteneur recréé, volume conservé)"
  docker run --rm -v "$BKH:/backups" postgres:16-alpine sh -c 'chown 70:70 /backups && chmod 0700 /backups'
  "${DCB[@]}" up -d --wait db >/dev/null
  CID_AFTER="$("${DCB[@]}" ps -q db)"
  [[ "$CID_AFTER" != "$CID_BEFORE" ]] || fail activation "le conteneur n'a pas été recréé"
  [[ "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")" == "$VOLUME" ]] || fail activation "volume changé"
  [[ "$("${DCB[@]}" port db 5432)" == "127.0.0.1:$PORT" ]] || fail activation "port publié changé : $("${DCB[@]}" port db 5432)"
  FP_AFTER="$(scenario fingerprint | sha_of)"
  [[ "$FP_AFTER" == "$FP_BEFORE" ]] || fail activation "données modifiées par l'activation"
  [[ "$(sqlp -c "SHOW archive_mode")" == on ]] || fail activation "archive_mode inactif"
  pass activation "conteneur recréé, volume $VOLUME conservé, port 127.0.0.1:$PORT conservé, empreinte ${FP_AFTER:0:12} identique, archivage actif"
fi

# --- 2. Racine et rôle de réplication ---------------------------------------------------------
expect_code 1 "archivage sans racine initialisée refusé" bk sm-wal-archive.sh /etc/hostname 000000010000000000000099
bk sm-backup-init.sh >/dev/null
[[ "$(mode_in status)" == 755 && "$(mode_in wal)" == 700 && "$(mode_in .)" == 711 ]] || fail init "droits de l'arborescence"
bk sm-backup-role.sh >/dev/null
bk sm-backup-role.sh >/dev/null   # idempotent
attrs="$(sqlp -c "SELECT rolreplication::text || rolsuper::text || rolbypassrls::text FROM pg_roles WHERE rolname = 'stockmanager_backup'")"
[[ "$attrs" == truefalsefalse ]] || fail role "attributs du rôle : $attrs"
rows="$(bk_sh "psql -X -w -U stockmanager_backup -d stockmanager -Atqc 'SELECT count(*) FROM catalog_categories'" 2>&1 || true)"
[[ "$rows" == 0 || "$rows" == *"permission denied"* ]] || fail role "le rôle de réplication lit des données d'entreprise : $rows"
switch_and_wait || fail archivage "premier segment non archivé"
pass role_et_racine "racine initialisée (0711, sous-répertoires 0700, status 0755), rôle REPLICATION sans superutilisateur ni BYPASSRLS ($rows), archivage compressé actif"

expect_code 2 "supervision sans sauvegarde" bk sm-backup-health.sh
grep -q "CRITIQUE base_backup" <<<"$LAST_OUT" || fail supervision "absence de sauvegarde non signalée"
grep -q "copie externe NON CONFIGURÉE" <<<"$LAST_OUT" || fail supervision "copie externe non signalée"

if (( QUICK )); then
  log "mode rapide : étapes 3 à 12 sautées"
  expect_code 0 "sauvegarde (mode rapide)" bk sm-base-backup.sh
  expect_code 0 "vérification (mode rapide)" bk sm-backup-verify.sh
else
# --- 3. Sauvegarde pendant des écritures concurrentes ------------------------------------------
log "3. sauvegarde complète pendant des écritures concurrentes"
scenario write-loop --seconds 25 --tag concurrent >"$WORK/loop1.json" &
LOOP=$!
sleep 3
bk sm-base-backup.sh --max-rate 2M >"$WORK/b1.out" 2>&1 &
B1=$!
sleep 4
expect_code 75 "seconde sauvegarde concurrente refusée" bk sm-base-backup.sh
expect_code 75 "vérification concurrente refusée" bk sm-backup-verify.sh
wait "$B1" || fail sauvegarde "première sauvegarde : $(tail -n 5 "$WORK/b1.out")"
wait "$LOOP" || fail sauvegarde "écritures concurrentes en échec"
B1_STAMP="$(status_get base_backup label)"
[[ "$(status_get base_backup status)" == success ]] || fail sauvegarde "statut $(status_get base_backup status)"
pass sauvegarde_concurrente "sauvegarde $B1_STAMP vérifiée pendant les écritures $(cat "$WORK/loop1.json") ; seconde sauvegarde et vérification concurrentes refusées (code 75)"
metric backup_duration_seconds "$(status_get base_backup duration_seconds)"
expect_code 0 "vérification" bk sm-backup-verify.sh
pass verification "$(status_get verify message)"

# --- 4. PITR à un instant T ------------------------------------------------------------------
log "4. PITR"
scenario write-loop --seconds 4 --tag avant >/dev/null
sleep 1
T="$(sqlp -c "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || 'Z'")"
sleep 1
scenario write-loop --seconds 4 --tag apres >/dev/null
switch_and_wait || fail pitr "archivage après les écritures"
N_AVANT="$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name LIKE 'avant-%'")"
PP="$ROOT_IN/pitr"
bk_sh "mkdir -p '$PP/occupe' && touch '$PP/occupe/x'"
expect_code 1 "PITR dans un répertoire non vide refusé" bk sm-restore-pitr.sh --target-time "$T" --data-dir "$PP/occupe"
grep -q "n'est pas vide" <<<"$LAST_OUT" || fail pitr "motif du refus : $LAST_OUT"
expect_code 1 "PITR sur le répertoire de production refusé" bk sm-restore-pitr.sh --data-dir "$PRIMARY/sous"
expect_code 1 "PITR sur un répertoire interne refusé" bk sm-restore-pitr.sh --data-dir "$ROOT_IN/base/x"
expect_code 1 "PITR après le dernier WAL refusé" bk sm-restore-pitr.sh --target-time "2099-01-01 00:00:00Z" --data-dir "$PP/futur"
expect_code 1 "PITR en heure locale implicite refusé" bk sm-restore-pitr.sh --target-time "2026-01-01 00:00:00" --data-dir "$PP/local"
P0="$(date -u +%s)"
expect_code 0 "PITR à T" bk sm-restore-pitr.sh --target-time "$T" --data-dir "$PP/data" --port 5544 --start
P1="$(date -u +%s)"
cq() { bk_sh "psql -X -w -h '$PP' -p 5544 -U $1 -d stockmanager -Atq -c \"$2\" ${3:+-c \"$3\"}"; }
got_avant="$(cq stockmanager "SELECT count(*) FROM catalog_categories WHERE name LIKE 'avant-%'")"
got_apres="$(cq stockmanager "SELECT count(*) FROM catalog_categories WHERE name LIKE 'apres-%'")"
[[ "$got_avant" == "$N_AVANT" && "$got_apres" == 0 && "$N_AVANT" -gt 0 ]] || fail pitr "avant $got_avant/$N_AVANT, après $got_apres"
[[ "$(cq stockmanager "SHOW archive_mode")" == off ]] || fail pitr "archivage actif sur la copie"
alpha="$(cq stockmanager "SELECT id FROM tenants WHERE slug = 'alpha'")"
leak="$(cq stockmanager_app "SET app.tenant_id = '$alpha'" "SELECT count(*) FROM catalog_categories WHERE tenant_id <> '$alpha'")"
mine="$(cq stockmanager_app "SET app.tenant_id = '$alpha'" "SELECT count(*) FROM catalog_categories")"
none="$(cq stockmanager_app "SELECT count(*) FROM catalog_categories")"
[[ "$leak" == 0 && "$none" == 0 && "$mine" -gt 0 ]] || fail pitr "isolement sur la copie : autre entreprise $leak, sans contexte $none, alpha $mine"
bk_sh "pg_ctl -D '$PP/data' -m fast -w stop" >/dev/null
pass pitr "copie à $T : $got_avant ligne(s) « avant », 0 « après », archivage coupé, RLS effective (alpha : $mine, autre : 0, sans contexte : 0) ; refus : non vide, production, interne, futur, heure locale"
metric pitr_seconds "$(( P1 - P0 ))"

# --- 5. Test réel de restauration --------------------------------------------------------------
log "5. test de restauration"
expect_code 0 "test de restauration" bk sm-restore-test.sh
[[ "$(status_get restore_test checks_failed)" == 0 && "$(status_get restore_test cleanup)" == removed ]] || fail restauration "statut"
empty_in restore-test || fail restauration "environnement non supprimé"
checks="$(grep -o '"name": "[a-z_]*", "ok": true' "$ROOT_HOST/status/restore_test.json" | sed 's/"name": "\([a-z_]*\)".*/\1/' | tr '\n' ' ')"
for c in recovery_point_reached alembic_revision copy_isolated pg_amcheck foreign_keys check_constraints unique_constraints constraints_validated business_invariants rls_forced app_role_no_bypass rls_no_context rls_tenant_isolation rls_cross_tenant_write rto; do
  [[ " $checks" == *" $c "* ]] || fail restauration "contrôle absent ou en échec : $c ($(cat "$ROOT_HOST/status/restore_test.json"))"
done
pass restauration "RTO $(status_get restore_test rto_seconds) s, octets non restaurés $(status_get restore_test wal_bytes_not_recovered) ; contrôles réussis : $checks; environnement supprimé sous gardes"
metric rto_seconds "$(status_get restore_test rto_seconds)"
metric wal_bytes_not_recovered "$(status_get restore_test wal_bytes_not_recovered)"

# --- 6. Interruptions ------------------------------------------------------------------------
log "6. interruption d'une sauvegarde"
bk sm-base-backup.sh --max-rate 64k >/dev/null 2>&1 &
BG=$!
sleep 6
bk_sh 'kill -TERM $(pgrep -f "^bash .*[s]m-base-backup.sh" | head -n 1)'
wait "$BG" 2>/dev/null || true
# Processus VIVANTS seulement : un zombie (état Z) est déjà terminé ; il subsiste quand le PID 1
# du conteneur ne récupère pas les orphelins (conteneur de job de la CI : « tail -f /dev/null »).
no_basebackup() { bk_sh 'for i in $(seq 1 30); do live=0; for p in $(pgrep -f "[p]g_basebackup"); do st=$(cut -d")" -f2 /proc/$p/stat 2>/dev/null | cut -d" " -f2); [ -n "$st" ] && [ "$st" != Z ] && live=1; done; [ $live = 0 ] && exit 0; sleep 1; done; exit 1'; }
no_basebackup || fail interruption "pg_basebackup encore actif après l'interruption"
for _ in $(seq 1 30); do [[ "$(status_get base_backup status)" == running ]] || break; sleep 1; done
[[ "$(status_get base_backup status)" == interrupted ]] || fail interruption "statut $(status_get base_backup status)"
partials && fail interruption "répertoire partiel laissé"
bk sm-base-backup.sh --max-rate 64k >/dev/null 2>&1 &
BG=$!
sleep 6
bk_sh 'kill -KILL $(pgrep -f "^bash .*[s]m-base-backup.sh") $(pgrep -f "[p]g_basebackup") 2>/dev/null; true'
wait "$BG" 2>/dev/null || true
no_basebackup || fail interruption "pg_basebackup encore actif après l'arrêt brutal"
partials || fail interruption "arrêt brutal : répertoire partiel attendu"
expect_code 0 "sauvegarde après arrêt brutal" bk sm-base-backup.sh
B2_STAMP="$(status_get base_backup label)"
partials && fail interruption "partiel orphelin non nettoyé"
pass interruption "signal TERM : statut « interrupted », partiel supprimé ; arrêt brutal (KILL) : partiel orphelin supprimé sous verrou à l'exécution suivante ($B2_STAMP)"

# --- 7. Sauvegarde corrompue ------------------------------------------------------------------
log "7. sauvegarde corrompue"
bk_sh "f=\$(find '$ROOT_IN/base/$B2_STAMP/base' -type f -size +16k | head -n 1); chmod u+w \"\$f\" \"\$(dirname \"\$f\")\"; printf 'X' | dd of=\"\$f\" bs=1 seek=4096 conv=notrunc 2>/dev/null"
expect_code 1 "vérification d'une sauvegarde altérée" bk sm-backup-verify.sh
grep -q "$B2_STAMP : pg_verifybackup en ÉCHEC" "$ROOT_HOST/status/verify.json" || fail corruption "anomalie non rapportée"
expect_code 2 "supervision après vérification en échec" bk sm-backup-health.sh
grep -q "CRITIQUE verify" <<<"$LAST_OUT" || fail corruption "supervision : $LAST_OUT"
expect_code 1 "PITR depuis la sauvegarde altérée refusé" bk sm-restore-pitr.sh --backup "$B2_STAMP" --data-dir "$PP/altere"
expect_code 0 "nouvelle sauvegarde" bk sm-base-backup.sh
B3_STAMP="$(status_get base_backup label)"
pass corruption "octet modifié dans $B2_STAMP : pg_verifybackup en échec, sauvegarde exclue (PITR refusé), supervision CRITIQUE"

# --- 8. WAL manquant et WAL corrompu ---------------------------------------------------------
log "8. WAL manquant / corrompu"
# Trois segments terminés APRÈS le début de la dernière sauvegarde : celui du milieu est retiré
# (un trou dans la chaîne de cette sauvegarde, pas seulement avant elle).
sqlp -c "CREATE TABLE drill_wal (x int)"
for i in 1 2 3; do sqlp -c "INSERT INTO drill_wal VALUES ($i)"; switch_and_wait || fail wal "archivage"; done
sqlp -c "DROP TABLE drill_wal"
B3_START="$(bk_sh "sed -n 's/^  \"start_wal\": \"\\([0-9A-F]*\\)\",\$/\\1/p' '$ROOT_IN/base/$B3_STAMP/backup_info.json'")"
SEG="$(bk_sh "ls '$ROOT_IN/wal' | grep -E '^[0-9A-F]{24}\.gz\$' | sort | awk '\$0 > \"$B3_START\"' | sed -n 2p")"
[[ -n "$B3_START" && -n "$SEG" ]] || fail wal "segment à retirer introuvable (début $B3_START)"
bk_sh "mv '$ROOT_IN/wal/$SEG' '$ROOT_IN/state/$SEG.aside'"
expect_code 1 "vérification avec un WAL manquant" bk sm-backup-verify.sh
grep -q "manquant" "$ROOT_HOST/status/verify.json" || fail wal "trou non détecté"
expect_code 1 "PITR avec un WAL manquant refusé" bk sm-restore-pitr.sh --backup "$B3_STAMP" --data-dir "$PP/trou"
bk_sh "cp '$ROOT_IN/state/$SEG.aside' '$ROOT_IN/wal/$SEG' && chmod u+w '$ROOT_IN/wal/$SEG' && printf 'XXXX' | dd of='$ROOT_IN/wal/$SEG' bs=1 seek=200 conv=notrunc 2>/dev/null"
expect_code 1 "vérification avec un WAL corrompu" bk sm-backup-verify.sh --full
grep -q "${SEG%.gz}" "$ROOT_HOST/status/verify.json" || fail wal "corruption non détectée"
expect_code 126 "restitution d'un WAL corrompu (arrêt de la restauration)" bk_sh "SM_BACKUP_DIR='$ROOT_IN' '$SCRIPTS_IN/sm-wal-restore.sh' '${SEG%.gz}' /tmp/sm-drill-seg"
expect_code 1 "restitution d'un WAL absent (fin normale)" bk_sh "SM_BACKUP_DIR='$ROOT_IN' '$SCRIPTS_IN/sm-wal-restore.sh' 00000001000000FF000000FF /tmp/sm-drill-seg"
bk_sh "mv -f '$ROOT_IN/state/$SEG.aside' '$ROOT_IN/wal/$SEG'"
pass wal "segment $SEG retiré : trou détecté, PITR refusé ; altéré : CRC détecté, restitution arrêtée (126) ; absent : fin normale (1)"

# --- 9. Échec et retard d'archivage, base inactive --------------------------------------------
log "9. échec d'archivage"
bk_sh "chmod 0500 '$ROOT_IN/wal'"
sqlp -c "CREATE TABLE drill_probe (x int); INSERT INTO drill_probe VALUES (1)"
sqlp -c "SELECT pg_switch_wal()" >/dev/null
for _ in $(seq 1 60); do (( $(sqlp -c "SELECT failed_count FROM pg_stat_archiver") > 0 )) && break; sleep 1; done
expect_code 2 "supervision : échec d'archivage" bk sm-backup-health.sh
grep -q "CRITIQUE archivage : échec NON RÉSOLU" <<<"$LAST_OUT" || fail archivage "échec non signalé : $LAST_OUT"
sqlp -c "INSERT INTO drill_probe VALUES (2)"; sqlp -c "SELECT pg_switch_wal()" >/dev/null
XENV=(SM_BACKUP_NOW_EPOCH=$(( $(date -u +%s) + 3600 )))
expect_code 2 "supervision : retard d'archivage" bk sm-backup-health.sh
grep -q "CRITIQUE archivage : RETARD" <<<"$LAST_OUT" || fail archivage "retard non signalé : $LAST_OUT"
XENV=()
bk_sh "chmod 0700 '$ROOT_IN/wal'"
switch_and_wait || fail archivage "reprise après l'échec"
bk sm-backup-health.sh >"$WORK/after.out" 2>&1 || true
grep -q "INFO archivage : dernier échec résolu" "$WORK/after.out" || fail archivage "reprise non constatée : $(cat "$WORK/after.out")"
if grep -q "CRITIQUE archivage" "$WORK/after.out"; then fail archivage "alerte maintenue après reprise : $(cat "$WORK/after.out")"; fi
sqlp -c "DROP TABLE drill_probe"; switch_and_wait || true
XENV=(SM_BACKUP_NOW_EPOCH=$(( $(date -u +%s) + 7200 )))
bk sm-backup-health.sh >"$WORK/idle.out" 2>&1 || true
XENV=()
grep -q "OK archivage : à jour — base inactive" "$WORK/idle.out" || fail inactivite "fausse alerte sur base inactive : $(cat "$WORK/idle.out")"
pass archivage_supervision "échec non résolu → CRITIQUE ; segments en attente au-delà du seuil → CRITIQUE (retard) ; reprise → INFO ; base inactive depuis 2 h → OK (aucune fausse alerte)"

# --- 10. Rétention ---------------------------------------------------------------------------
log "10. rétention"
FUT=$(( $(date -u +%s) + 40 * 86400 ))
XENV=("SM_BACKUP_NOW_EPOCH=$FUT")
expect_code 0 "rétention : simulation par défaut" bk sm-backup-prune.sh
exists_in "base/$B1_STAMP" && [[ "$(status_get prune mode)" == dry-run ]] || fail retention "la simulation a supprimé"
grep -q "\"$B1_STAMP\"" "$ROOT_HOST/status/prune.json" || fail retention "simulation : $B1_STAMP non proposée"
# Dernière vérifiée protégée : B3 marquée en échec → la dernière vérifiée devient B1.
bk_sh "cp '$ROOT_IN/state/backups/$B3_STAMP.verify' '$ROOT_IN/state/b3.keep' && echo 'failed 0 test' > '$ROOT_IN/state/backups/$B3_STAMP.verify'"
XENV=("SM_BACKUP_NOW_EPOCH=$FUT" SM_BACKUP_KEEP_MIN=1)
expect_code 0 "rétention : dernière vérifiée protégée" bk sm-backup-prune.sh --apply
exists_in "base/$B1_STAMP" || fail retention "la dernière sauvegarde vérifiée a été supprimée"
bk_sh "mv -f '$ROOT_IN/state/b3.keep' '$ROOT_IN/state/backups/$B3_STAMP.verify'"
XENV=("SM_BACKUP_NOW_EPOCH=$FUT")
wal_before="$(bk_sh "ls '$ROOT_IN/wal' | wc -l" | tr -d ' ')"
expect_code 0 "rétention appliquée" bk sm-backup-prune.sh --apply
! exists_in "base/$B1_STAMP" && exists_in "base/$B3_STAMP" && exists_in "base/$B2_STAMP" || fail retention "sélection : $(bk_sh "ls '$ROOT_IN/base'")"
wal_after="$(bk_sh "ls '$ROOT_IN/wal' | wc -l" | tr -d ' ')"
(( wal_after < wal_before )) || fail retention "aucun WAL ancien supprimé ($wal_before → $wal_after)"
XENV=()
expect_code 0 "test de restauration après rétention" bk sm-restore-test.sh
pass retention "simulation par défaut sans suppression ; dernière vérifiée jamais supprimée ; application : $B1_STAMP supprimée, $B2_STAMP et $B3_STAMP conservées (minimum 2), WAL $wal_before → $wal_after ; restauration toujours possible"

# --- 11. Gardes de suppression ----------------------------------------------------------------
log "11. gardes de suppression"
RT="$ROOT_IN/restore-test"
FAKE="20260101T000000Z-0123456789abcdef"
LINKED="20260101T000000Z-fedcba9876543210"
bk_sh "mkdir -p '$RT/$FAKE/data' && touch '$RT/$FAKE/data/PG_VERSION'"
expect_code 3 "suppression sans marqueur refusée" bk sm-restore-test.sh --cleanup "$RT/$FAKE"
bk_sh "printf 'run_id=$FAKE\npath=/autre/chemin\n' > '$RT/$FAKE/.sm-restore-test'"
expect_code 3 "suppression avec un marqueur non conforme refusée" bk sm-restore-test.sh --cleanup "$RT/$FAKE"
bk_sh "mkdir -p '$ROOT_IN/victime' && touch '$ROOT_IN/victime/precieux' && ln -s '$ROOT_IN/victime' '$RT/$LINKED'"
expect_code 3 "suppression via un lien symbolique refusée" bk sm-restore-test.sh --cleanup "$RT/$LINKED"
expect_code 3 "suppression hors de restore-test refusée" bk sm-restore-test.sh --cleanup "$ROOT_IN/base/$B3_STAMP"
expect_code 3 "suppression d'un nom non conforme refusée" bk sm-restore-test.sh --cleanup "$RT/../victime"
exists_in victime/precieux && exists_in "base/$B3_STAMP" && exists_in "restore-test/$FAKE" || fail gardes "un élément protégé a été supprimé"
bk_sh "printf 'run_id=$FAKE\npath=%s\n' \"\$(realpath '$RT/$FAKE')\" > '$RT/$FAKE/.sm-restore-test'"
expect_code 0 "suppression conforme acceptée" bk sm-restore-test.sh --cleanup "$RT/$FAKE"
exists_in "restore-test/$FAKE" && fail gardes "suppression conforme non faite"
bk_sh "rm -f '$RT/$LINKED' && rm -rf '$ROOT_IN/victime'"
expect_code 0 "test de restauration conservé (--keep)" bk sm-restore-test.sh --keep
KEPT="$(status_get restore_test run_dir)"
[[ "$(status_get restore_test cleanup)" == kept ]] && exists_in "restore-test/${KEPT##*/}/data/PG_VERSION" || fail gardes "--keep non respecté"
expect_code 0 "suppression d'un environnement conservé" bk sm-restore-test.sh --cleanup "$KEPT"
pass gardes_suppression "refus (code 3, rien supprimé) : sans marqueur, marqueur non conforme, lien symbolique, hors restore-test, nom non conforme ; acceptés : marqueur conforme, environnement --keep"

# --- 12. Statut de la console -----------------------------------------------------------------
log "12. statut de la console (lecture de status/ uniquement)"
bk sm-backup-health.sh --write >/dev/null 2>&1 || true
(cd "$REPO/backend" && SM_BACKUP_STATUS_DIR="$ROOT_HOST/status" uv run --frozen python -m tests.backup_scenario console-status) >"$WORK/console.json" \
  || fail console "lecture du statut : $(cat "$WORK/console.json")"
py - "$WORK/console.json" <<'PY' || fail console "$(cat "$WORK/console.json")"
import json, sys
s = json.load(open(sys.argv[1]))
ops = s["operations"]
assert s["archiving"]["state"] in {"up_to_date", "idle", "archiving"}, s["archiving"]
assert s["archiving"]["level"] == "ok", s["archiving"]
assert ops["base_backup"]["level"] == "ok", ops["base_backup"]
assert ops["restore_test"]["level"] == "ok", ops["restore_test"]
assert ops["verify"]["level"] == "critical", ops["verify"]   # sauvegarde altérée toujours signalée
assert s["offsite"]["configured"] is False and s["offsite"]["level"] == "warning", s["offsite"]
assert s["storage"]["base_backups"] >= 2, s["storage"]
assert s["supervision"]["level"] == "critical", s["supervision"]
PY
pass console "archivage $(py -c 'import json,sys; print(json.load(open(sys.argv[1]))["archiving"]["state"])' "$WORK/console.json"), sauvegarde et test de restauration OK, vérification en alerte (sauvegarde altérée), copie externe non configurée"
fi

# --- 13. Console et agent (R11) : planification, sauvegarde, restauration d'une entreprise ----
log "13. console → agent : planification, sauvegardes, restaurations"
export SM_BACKUP_STATUS_DIR="$ROOT_HOST/status"
jget() { py -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {"d": d}))' "$1"; }
con() { scenario console "$@"; }
agent() { bk sm-backup-agent.sh >>"$WORK/agent.out" 2>&1 || fail agent "agent en échec : $(tail -n 5 "$WORK/agent.out")"; }
others_fp() { as_pg env "${ENVV[@]}" PGUSER=stockmanager PGDATABASE=stockmanager "$SCRIPTS_IN/sm-fingerprint.sh" 2>/dev/null | grep -v "|$GAMMA|" | grep -E "\|($ALPHA|$BETA)\|"; }
if [[ "$MODE" == docker ]]; then
  others_fp() { bk_sh "PGUSER=stockmanager PGDATABASE=stockmanager '$SCRIPTS_IN/sm-fingerprint.sh'" | grep -E "\|($ALPHA|$BETA)\|"; }
fi
ALPHA="$(sqlp -c "SELECT id FROM tenants WHERE slug = 'alpha'")"
BETA="$(sqlp -c "SELECT id FROM tenants WHERE slug = 'beta'")"
GAMMA="$(sqlp -c "SELECT id FROM tenants WHERE slug = 'gamma'")"
NTENANTS="$(sqlp -c "SELECT count(*) FROM tenants")"
scenario console-admins >/dev/null
# Seule la planification logique quotidienne reste active ; son échéance est atteinte.
sqlp -c "UPDATE backup_policies SET enabled = false, next_run_at = NULL WHERE NOT (category = 'logical' AND frequency = 'daily')"
sqlp -c "UPDATE backup_policies SET next_run_at = now() - interval '1 minute' WHERE category = 'logical' AND frequency = 'daily'"
agent
sched="$(sqlp -c "SELECT count(*) FROM backup_runs WHERE trigger = 'schedule' AND kind = 'logical' AND status = 'succeeded'")"
sitems="$(sqlp -c "SELECT count(*) FROM backup_items i JOIN backup_runs r ON r.id = i.run_id WHERE r.trigger = 'schedule' AND i.status = 'succeeded' AND i.verified")"
next="$(sqlp -c "SELECT next_run_at > now() FROM backup_policies WHERE category = 'logical' AND frequency = 'daily'")"
# Rétention de la politique planifiée (logique quotidienne : 30 jours) appliquée aux éléments.
ret="$(sqlp -c "SELECT bool_and(i.expires_at BETWEEN now() + interval '29 days' AND now() + interval '31 days') FROM backup_items i JOIN backup_runs r ON r.id = i.run_id WHERE r.trigger = 'schedule'")"
[[ "$ret" == t ]] || fail planification "rétention de la politique non appliquée"
[[ "$sched" == 1 && "$sitems" == "$NTENANTS" && "$next" == t ]] || fail planification "exécution planifiée $sched, entreprises $sitems/$NTENANTS, prochaine échéance future $next"
pass planification "échéance des paramètres atteinte → 1 sauvegarde logique planifiée exécutée par l'agent ($sitems entreprises, sommes vérifiées), prochaine exécution recalculée"

RUN="$(con --as a --method POST --path /operations/backups/runs --json "{\"kind\": \"logical\", \"scope\": \"tenant\", \"tenant_id\": \"$GAMMA\"}" --expect 201 | jget 'd["id"]')"
agent
detail="$(con --as a --path "/operations/backups/runs/$RUN")"
[[ "$(jget 'd["status"]' <<<"$detail")" == succeeded ]] || fail sauvegarde_entreprise "exécution : $detail"
ITEM="$(jget 'd["items"][0]["id"]' <<<"$detail")"
[[ "$(jget 'len(d["items"]) == 1 and d["items"][0]["tenant_id"] == "'"$GAMMA"'" and d["items"][0]["restorable"]' <<<"$detail")" == True ]] || fail sauvegarde_entreprise "élément : $detail"
pass sauvegarde_entreprise "sauvegarde immédiate de gamma depuis la console : réussie, 1 élément restaurable ($(jget 'd["items"][0]["row_count"]' <<<"$detail") lignes)"

[[ "$(scenario write-one --slug gamma --tag apres-sauvegarde | jget 'd["status"]')" == 201 ]] || fail restauration_entreprise "écriture de gamma"
OP="$(con --as a --method POST --path /operations/backups/restores --json "{\"tenant_id\": \"$GAMMA\", \"mode\": \"logical\", \"source_item_id\": \"$ITEM\", \"reason\": \"Exercice : retour de gamma\"}" --expect 201 | jget 'd["id"]')"
agent
op="$(con --as a --path "/operations/backups/restores/$OP")"
[[ "$(jget 'd["status"]' <<<"$op")" == verified ]] || fail verification_isolee "statut : $(jget 'd["status"], d["error"], d["verification"]' <<<"$op")"
for c in other_tenants_unchanged kept_tables_unchanged restored_exact tenant_apply rls_tenant_isolation foreign_keys; do
  [[ "$(jget '[x["ok"] for x in d["verification"]["copy"]["checks"] if x["name"] == "'"$c"'"]' <<<"$op")" == "[True]" ]] || fail verification_isolee "contrôle $c : $(jget 'd["verification"]["copy"]["checks"]' <<<"$op")"
done
[[ "$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name = 'apres-sauvegarde-gamma'")" == 1 ]] || fail verification_isolee "la vérification a modifié la production"
con --as a --method POST --path "/operations/backups/restores/$OP/approve" --json '{"reason": "Je valide ma propre demande"}' --expect 403 >/dev/null
con --as b --method POST --path "/operations/backups/restores/$OP/approve" --json '{"reason": "Rapport relu : conforme"}' >/dev/null
pass verification_isolee "copie isolée : autres entreprises et tables conservées inchangées, restauration exacte, RLS ; production non modifiée ; demandeur refusé (403), second administrateur approuve"

scenario write-loop --seconds 12 --tag pendant >"$WORK/loop2.json" &
LOOP=$!
sleep 2
agent
wait "$LOOP" || fail application "les autres entreprises ont été bloquées pendant la restauration de gamma"
op="$(con --as a --path "/operations/backups/restores/$OP")"
[[ "$(jget 'd["status"]' <<<"$op")" == applied ]] || fail application "statut : $(jget 'd["status"], d["error"]' <<<"$op")"
[[ "$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name = 'apres-sauvegarde-gamma'")" == 0 ]] || fail application "écriture postérieure à la sauvegarde toujours présente"
na="$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name LIKE 'pendant-alpha-%'")"
[[ "$na" == "$(jget 'd["operations"]["alpha"]' <"$WORK/loop2.json")" && "$na" -gt 0 ]] || fail application "écritures concurrentes d'alpha : $na / $(cat "$WORK/loop2.json")"
[[ -z "$(sqlp -c "SELECT restore_locked_at FROM tenants WHERE id = '$GAMMA'")" ]] || fail application "gamma encore verrouillée"
[[ "$(sqlp -c "SELECT count(*) FROM audit_logs WHERE tenant_id = '$GAMMA' AND action = 'backup.tenant_restored'")" == 1 ]] || fail application "journal de gamma"
pass application "gamma revenue à sa sauvegarde (écriture postérieure absente), sauvegarde préalable faite, verrou levé, journal ; alpha et beta ont écrit PENDANT l'application ($(cat "$WORK/loop2.json"))"

FP1="$(others_fp | sha256sum)"
OP2="$(con --as a --method POST --path "/operations/backups/restores/$OP/rollback" --json '{"reason": "Exercice : retour arrière"}' --expect 201 | jget 'd["id"]')"
agent
con --as b --method POST --path "/operations/backups/restores/$OP2/approve" --json '{"reason": "Retour arrière validé"}' >/dev/null
agent
[[ "$(con --as a --path "/operations/backups/restores/$OP2" | jget 'd["status"]')" == applied ]] || fail retour_arriere "statut"
[[ "$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name = 'apres-sauvegarde-gamma'")" == 1 ]] || fail retour_arriere "état antérieur non rétabli"
FP2="$(others_fp | sha256sum)"
[[ "$FP1" == "$FP2" ]] || fail retour_arriere "alpha ou beta modifiée par une restauration de gamma"
pass retour_arriere "retour arrière (même circuit : vérification, second administrateur) : écriture de gamma rétablie ; empreinte d'alpha et beta IDENTIQUE avant / après (${FP1:0:12})"

# Archive altérée : la vérification la refuse, la production n'est pas touchée.
REF="$(sqlp -c "SELECT storage_ref FROM backup_items WHERE id = '$ITEM'")"
bk_sh "chmod u+w '$ROOT_IN/$REF' '$ROOT_IN/$REF/catalog_categories.csv.gz' && printf 'X' >> '$ROOT_IN/$REF/catalog_categories.csv.gz'"
OP3="$(con --as a --method POST --path /operations/backups/restores --json "{\"tenant_id\": \"$GAMMA\", \"mode\": \"logical\", \"source_item_id\": \"$ITEM\", \"reason\": \"Exercice : archive altérée\"}" --expect 201 | jget 'd["id"]')"
agent
[[ "$(con --as a --path "/operations/backups/restores/$OP3" | jget 'd["status"]')" == verification_failed ]] || fail archive_alteree "statut"
[[ "$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name = 'apres-sauvegarde-gamma'")" == 1 ]] || fail archive_alteree "production modifiée"
pass archive_alteree "archive modifiée après la sauvegarde : vérification en échec (sommes de contrôle), aucune approbation possible, production intacte"

# PITR d'une entreprise : avant T présent, après T absent.
[[ "$(scenario write-one --slug gamma --tag pitr-avant | jget 'd["status"]')" == 201 ]] || fail pitr_entreprise "écriture avant"
sleep 2
T2="$(sqlp -c "SELECT to_char(date_trunc('second', clock_timestamp()) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS') || 'Z'")"
sleep 2
[[ "$(scenario write-one --slug gamma --tag pitr-apres | jget 'd["status"]')" == 201 ]] || fail pitr_entreprise "écriture après"
switch_and_wait || fail pitr_entreprise "archivage"
bk sm-backup-health.sh --write >/dev/null 2>&1 || true
OP4="$(con --as a --method POST --path /operations/backups/restores --json "{\"tenant_id\": \"$GAMMA\", \"mode\": \"pitr\", \"target_time\": \"$T2\", \"reason\": \"Exercice : gamma à T\"}" --expect 201 | jget 'd["id"]')"
agent
op="$(con --as b --path "/operations/backups/restores/$OP4")"
[[ "$(jget 'd["status"]' <<<"$op")" == verified && "$(jget 'd["materialized_item_id"] is not None' <<<"$op")" == True ]] || fail pitr_entreprise "vérification : $(jget 'd["status"], d["error"]' <<<"$op")"
con --as b --method POST --path "/operations/backups/restores/$OP4/approve" --json '{"reason": "Instant validé"}' >/dev/null
agent
[[ "$(con --as a --path "/operations/backups/restores/$OP4" | jget 'd["status"]')" == applied ]] || fail pitr_entreprise "application"
av="$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name = 'pitr-avant-gamma'")"
ap="$(sqlp -c "SELECT count(*) FROM catalog_categories WHERE name = 'pitr-apres-gamma'")"
[[ "$av" == 1 && "$ap" == 0 ]] || fail pitr_entreprise "avant $av, après $ap"
pass pitr_entreprise "gamma restaurée à $T2 (copie à l'instant, export, vérification sur l'état actuel, approbation) : écriture antérieure présente, postérieure absente"

# Restauration globale : vérifiée et approuvée, JAMAIS appliquée par l'agent.
OP5="$(con --as a --method POST --path /operations/backups/restores --json "{\"scope\": \"platform\", \"mode\": \"pitr\", \"target_time\": \"$T2\", \"reason\": \"Exercice : restauration globale\"}" --expect 201 | jget 'd["id"]')"
agent
[[ "$(con --as a --path "/operations/backups/restores/$OP5" | jget 'd["status"]')" == verified ]] || fail restauration_globale "vérification"
con --as b --method POST --path "/operations/backups/restores/$OP5/approve" --json '{"reason": "Procédure globale"}' >/dev/null
agent
[[ "$(con --as a --path "/operations/backups/restores/$OP5" | jget 'd["status"], d["manual_application"]')" == "('approved', True)" ]] || fail restauration_globale "l'agent a agi sur une restauration globale"
con --as a --method POST --path "/operations/backups/restores/$OP5/cancel" --json '{"reason": "Fin de l exercice"}' >/dev/null
pass restauration_globale "PITR global vérifié sur copie isolée puis approuvé ; application laissée à la procédure manuelle (agent sans effet)"

# Échec puis nouvelle tentative.
bk_sh "chmod 0500 '$ROOT_IN/logical/$GAMMA'"
RUN="$(con --as a --method POST --path /operations/backups/runs --json "{\"kind\": \"logical\", \"scope\": \"tenant\", \"tenant_id\": \"$GAMMA\"}" --expect 201 | jget 'd["id"]')"
agent
[[ "$(con --as a --path "/operations/backups/runs/$RUN" | jget 'd["status"]')" == failed ]] || fail nouvelle_tentative "échec attendu"
retry="$(sqlp -c "SELECT count(*) FROM backup_runs WHERE retry_of = '$RUN' AND status = 'queued' AND attempt = 2 AND not_before > now()")"
[[ "$retry" == 1 ]] || fail nouvelle_tentative "nouvelle tentative non planifiée"
bk_sh "chmod 0700 '$ROOT_IN/logical/$GAMMA'"
sqlp -c "UPDATE backup_runs SET not_before = now() WHERE retry_of = '$RUN'"
agent
[[ "$(sqlp -c "SELECT status FROM backup_runs WHERE retry_of = '$RUN'")" == succeeded ]] || fail nouvelle_tentative "nouvelle tentative en échec"
pass nouvelle_tentative "échec (répertoire non inscriptible) → nouvelle tentative différée (essai 2) → réussie"

write_report ok
cat "$REPORT"
