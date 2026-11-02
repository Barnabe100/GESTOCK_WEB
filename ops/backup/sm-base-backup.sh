#!/usr/bin/env bash
# Sauvegarde de base (physique) du cluster PostgreSQL — point de départ de toute restauration.
#
#   SM_BACKUP_DIR=/srv/backups/stockmanager PGHOST=… PGPORT=… PGUSER=… sm-base-backup.sh
#
# pg_basebackup (point de contrôle immédiat, WAL de la sauvegarde inclus, manifeste SHA-256),
# puis pg_verifybackup : une sauvegarde non vérifiée est supprimée et le script ÉCHOUE.
# Résultat : ${SM_BACKUP_DIR}/base/<AAAAMMJJTHHMMSSZ>/ (lecture seule) et backup_info.json.
# Rôle : un rôle REPLICATION dédié (jamais le rôle applicatif). Mot de passe : ~/.pgpass ou
# PGPASSFILE, jamais en argument.
set -euo pipefail

ROOT="${SM_BACKUP_DIR:?SM_BACKUP_DIR doit être défini}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$ROOT/base/$STAMP"
PARTIAL="$ROOT/base/.partial-$STAMP"
umask 077
mkdir -p "$ROOT/base"
trap 'rm -rf "$PARTIAL"' EXIT

START_EPOCH="$(date -u +%s)"
pg_basebackup --pgdata="$PARTIAL" --format=plain --wal-method=stream --checkpoint=fast \
  --manifest-checksums=SHA256 --label="stockmanager-$STAMP" --no-password
pg_verifybackup --quiet "$PARTIAL"
END_EPOCH="$(date -u +%s)"
START_WAL="$(sed -n 's/^START WAL LOCATION: .* (file \(.*\))$/\1/p' "$PARTIAL/backup_label")"
cat > "$PARTIAL/backup_info.json" <<JSON
{
  "label": "stockmanager-$STAMP",
  "started_at": "$(date -u -d "@$START_EPOCH" +%Y-%m-%dT%H:%M:%SZ)",
  "finished_at": "$(date -u -d "@$END_EPOCH" +%Y-%m-%dT%H:%M:%SZ)",
  "duration_seconds": $((END_EPOCH - START_EPOCH)),
  "start_wal": "$START_WAL",
  "verified": true
}
JSON
mv "$PARTIAL" "$DEST"
trap - EXIT
chmod -R a-w "$DEST"
echo "$DEST"
