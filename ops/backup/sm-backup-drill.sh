#!/usr/bin/env bash
# Exercice de sauvegarde / restauration (PITR) mesuré — preuve du RPO et du RTO.
#
#   ops/backup/sm-backup-drill.sh            (depuis la racine du dépôt ; CI : job « backup-drill »)
#
# Monte un cluster PostgreSQL 16 TEMPORAIRE (127.0.0.1, port libre, répertoire jetable) : aucune
# base existante n'est lue ni modifiée. Étapes :
#   1. cluster avec archivage WAL (scripts de production : sm-wal-archive.sh, archive_timeout) ;
#   2. schéma RÉEL de l'application (rôles, migrations Alembic jusqu'à head, catalogue) et une
#      entreprise provisionnée par la CLI ;
#   3. sauvegarde de base vérifiée (sm-base-backup.sh) ;
#   4. écritures « avant » l'instant visé, horodatage T, écritures « après » ;
#   5. restauration PITR à T dans un répertoire NEUF (sm-restore-pitr.sh), sur un autre port ;
#   6. vérifications : données « avant » présentes, « après » absentes, RLS active et forcée,
#      rôle applicatif sans accès hors contexte d'entreprise, révision Alembic identique ;
#   7. rétention (sm-backup-prune.sh) sans perte de la dernière sauvegarde.
# Rapport JSON : ${SM_DRILL_REPORT:-/tmp/sm-backup-drill-report.json} ; code de sortie 0 = réussite.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="${SM_PG_BINDIR:-$(pg_config --bindir 2>/dev/null || echo /usr/lib/postgresql/16/bin)}"
export PATH="$BIN:$PATH"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/sm-backup-drill.XXXXXX")"
chmod 0755 "$WORK"
REPORT="${SM_DRILL_REPORT:-${TMPDIR:-/tmp}/sm-backup-drill-report.json}"
RPO_TARGET=900   # 15 minutes
RTO_TARGET=14400 # 4 heures
ARCHIVE_TIMEOUT="${SM_DRILL_ARCHIVE_TIMEOUT:-60}"

# PostgreSQL refuse de tourner en root : serveur et fichiers de données confiés à « postgres ».
as_pg() {
  if (( EUID == 0 )); then runuser -u postgres -- env PATH="$PATH" "$@"; else "$@"; fi
}
# Port libre : Python du système, sinon celui de l'environnement du backend (uv).
free_port() {
  local code='import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'
  if command -v python3 >/dev/null; then python3 -c "$code"
  else (cd "$REPO/backend" && uv run --frozen python -c "$code"); fi
}
log() { echo "[exercice] $*"; }

PRIMARY="$WORK/primary" RESTORED="$WORK/restored" BACKUPS="$WORK/backups"
PORT="$(free_port)" RPORT="$(free_port)"
cleanup() {
  as_pg pg_ctl -D "$RESTORED" -m immediate stop >/dev/null 2>&1 || true
  as_pg pg_ctl -D "$PRIMARY" -m immediate stop >/dev/null 2>&1 || true
  if [[ -z "${SM_DRILL_KEEP:-}" ]]; then rm -rf "$WORK"; else log "conservé : $WORK"; fi
}
trap cleanup EXIT

# Scripts de production copiés dans le répertoire jetable (lisibles par le serveur).
mkdir -p "$WORK/bin" "$BACKUPS"
cp "$REPO"/ops/backup/sm-*.sh "$WORK/bin/"
(( EUID == 0 )) && chown -R postgres "$WORK"

log "1. cluster temporaire (port $PORT), archivage WAL toutes les ${ARCHIVE_TIMEOUT} s au plus"
as_pg initdb -D "$PRIMARY" -U stockmanager --auth=trust --encoding=UTF8 --locale=C.UTF-8 >/dev/null
as_pg tee -a "$PRIMARY/postgresql.auto.conf" >/dev/null <<CONF
port = $PORT
listen_addresses = '127.0.0.1'
unix_socket_directories = '$WORK'
wal_level = replica
archive_mode = on
archive_command = 'SM_BACKUP_DIR=$BACKUPS $WORK/bin/sm-wal-archive.sh %p %f'
archive_timeout = $ARCHIVE_TIMEOUT
CONF
as_pg pg_ctl -D "$PRIMARY" -l "$WORK/primary.log" -w start >/dev/null

export PGHOST=127.0.0.1 PGPORT="$PORT" PGUSER=stockmanager
psql_q() { psql -v ON_ERROR_STOP=1 -Atq "$@"; }
psql_q -d postgres -c "CREATE DATABASE stockmanager"
for role in stockmanager_app stockmanager_platform stockmanager_purge; do
  psql_q -d stockmanager -c "CREATE ROLE $role LOGIN PASSWORD '$role' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
done

log "2. schéma réel (migrations Alembic) et entreprise de démonstration"
URL="postgresql+psycopg://%s@127.0.0.1:$PORT/stockmanager"
export SM_ENVIRONMENT=development
export SM_MIGRATION_DATABASE_URL="$(printf "$URL" stockmanager)"
export SM_DATABASE_URL="$(printf "$URL" stockmanager_app:stockmanager_app)"
export SM_PLATFORM_DATABASE_URL="$(printf "$URL" stockmanager_platform:stockmanager_platform)"
export SM_PURGE_DATABASE_URL="$(printf "$URL" stockmanager_purge:stockmanager_purge)"
export SM_JWT_SECRET="exercice-de-restauration-uniquement-0123456789"
cd "$REPO/backend"
uv run --frozen alembic upgrade head >/dev/null 2>&1
uv run --frozen stockmanager catalog sync >/dev/null
echo "Exercice-Restauration-2026" | uv run --frozen stockmanager create-tenant --name "Exercice" \
  --slug exercice --business-profile retail.quincaillerie --country BF --plan ENTREPRISE \
  --owner-email exercice@example.com --owner-name "Exercice" --owner-password-stdin >/dev/null
TENANT="$(psql_q -d stockmanager -c "SELECT id FROM tenants WHERE slug = 'exercice'")"
REVISION="$(psql_q -d stockmanager -c "SELECT version_num FROM alembic_version")"
cd "$REPO"

log "3. sauvegarde de base vérifiée"
BASE="$(SM_BACKUP_DIR="$BACKUPS" as_pg env PGHOST=127.0.0.1 PGPORT="$PORT" PGUSER=stockmanager \
  "$WORK/bin/sm-base-backup.sh")"
log "   $BASE"

log "4. écritures avant / après l'instant visé"
insert() {
  psql_q -d stockmanager -c "INSERT INTO catalog_categories (id, tenant_id, name, is_active) VALUES (gen_random_uuid(), '$TENANT', '$1', true)"
}
for i in 1 2 3; do insert "avant-$i"; done
sleep 1
TARGET="$(psql_q -d stockmanager -c "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00'")"
sleep 1
insert "apres-1"
LAST_COMMIT_EPOCH="$(date -u +%s)"
# Attente du VRAI archivage (archive_timeout), sans pg_switch_wal : mesure du RPO effectif.
until psql_q -d stockmanager -c "SELECT extract(epoch FROM last_archived_time) >= $LAST_COMMIT_EPOCH FROM pg_stat_archiver" | grep -q t; do
  sleep 1
  (( $(date -u +%s) - LAST_COMMIT_EPOCH > RPO_TARGET )) && { log "ÉCHEC : WAL non archivé sous le RPO"; exit 1; }
done
RPO_MEASURED=$(( $(date -u +%s) - LAST_COMMIT_EPOCH ))
FAILED_ARCHIVES="$(psql_q -d stockmanager -c "SELECT failed_count FROM pg_stat_archiver")"
log "   dernière écriture archivée en ${RPO_MEASURED} s (échecs d'archivage : $FAILED_ARCHIVES)"

log "5. restauration à $TARGET dans un répertoire neuf (port $RPORT)"
# Garde : jamais de restauration sur un répertoire de données existant (ici, le primaire).
if SM_BACKUP_DIR="$BACKUPS" as_pg "$WORK/bin/sm-restore-pitr.sh" --target-time "$TARGET" \
  --data-dir "$PRIMARY" >/dev/null 2>&1; then
  log "ÉCHEC : restauration acceptée sur un répertoire non vide"; exit 1
fi
RESTORE_START="$(date -u +%s)"
SM_BACKUP_DIR="$BACKUPS" as_pg "$WORK/bin/sm-restore-pitr.sh" --target-time "$TARGET" \
  --data-dir "$RESTORED" --port "$RPORT" >/dev/null
as_pg tee -a "$RESTORED/postgresql.auto.conf" >/dev/null <<CONF
unix_socket_directories = '$WORK'
CONF
as_pg pg_ctl -D "$RESTORED" -l "$WORK/restored.log" -w -t 3600 start >/dev/null
until [[ "$(psql_q -p "$RPORT" -d postgres -c 'SELECT pg_is_in_recovery()')" == "f" ]]; do sleep 1; done
RTO_MEASURED=$(( $(date -u +%s) - RESTORE_START ))

log "6. vérifications"
rq() { psql_q -p "$RPORT" -d stockmanager -c "$1"; }
fail() { log "ÉCHEC : $*"; exit 1; }
[[ "$(rq "SELECT count(*) FROM catalog_categories WHERE name LIKE 'avant-%'")" == 3 ]] || fail "données « avant » absentes"
[[ "$(rq "SELECT count(*) FROM catalog_categories WHERE name LIKE 'apres-%'")" == 0 ]] || fail "données « après » présentes"
[[ "$(rq "SELECT count(*) FROM tenants WHERE id = '$TENANT'")" == 1 ]] || fail "entreprise absente"
[[ "$(rq "SELECT version_num FROM alembic_version")" == "$REVISION" ]] || fail "révision Alembic différente"
[[ "$(rq "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE relname = 'catalog_categories'")" == t ]] || fail "RLS désactivée"
UNSAFE="$(rq "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT (c.relrowsecurity AND c.relforcerowsecurity) AND has_table_privilege('stockmanager_app', c.oid, 'SELECT')")"
# Toute table portant tenant_id : RLS forcée, ou inaccessible au rôle applicatif (journal de la
# console TechNova, ADR-0031).
[[ "$UNSAFE" == 0 ]] || fail "$UNSAFE table(s) d'entreprise lisibles sans RLS forcée"
APP_ROWS="$(PGUSER=stockmanager_app PGPASSWORD=stockmanager_app psql_q -p "$RPORT" -d stockmanager -c "SELECT count(*) FROM catalog_categories")"
[[ "$APP_ROWS" == 0 ]] || fail "rôle applicatif : $APP_ROWS ligne(s) visibles hors contexte d'entreprise"
[[ "$(rq "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'stockmanager_app'")" == f ]] || fail "BYPASSRLS"

log "7. rétention"
SM_BACKUP_DIR="$BACKUPS" as_pg "$WORK/bin/sm-backup-prune.sh" >/dev/null
[[ -d "$BASE" ]] || fail "la dernière sauvegarde a été supprimée"

STATUS=ok
(( RPO_MEASURED <= RPO_TARGET )) || STATUS=rpo_exceeded
(( RTO_MEASURED <= RTO_TARGET )) || STATUS=rto_exceeded
cat > "$REPORT" <<JSON
{
  "status": "$STATUS",
  "postgresql": "$(postgres --version | awk '{print $3}')",
  "alembic_revision": "$REVISION",
  "archive_timeout_seconds": $ARCHIVE_TIMEOUT,
  "rpo_target_seconds": $RPO_TARGET,
  "rpo_measured_seconds": $RPO_MEASURED,
  "rto_target_seconds": $RTO_TARGET,
  "rto_measured_seconds": $RTO_MEASURED,
  "target_time": "$TARGET",
  "failed_archives": $FAILED_ARCHIVES,
  "checks": ["refus d'un répertoire non vide", "avant présentes", "après absentes", "révision identique", "RLS forcée sur toute table d'entreprise lisible par le rôle applicatif", "rôle applicatif sans accès hors contexte", "rétention"]
}
JSON
cat "$REPORT"
[[ "$STATUS" == ok ]]
