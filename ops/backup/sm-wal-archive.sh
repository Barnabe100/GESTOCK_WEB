#!/usr/bin/env bash
# Archivage d'un fichier WAL (archive_command de PostgreSQL) — sauvegarde continue, PITR.
#
#   archive_command = '/opt/stockmanager/backup/sm-wal-archive.sh %p %f'   (SM_BACKUP_DIR défini)
#
# Procédure atomique : compression gzip dans un fichier temporaire du même répertoire,
# RELECTURE complète comparée à la source (cmp), fsync du fichier, renommage, fsync du
# répertoire. Un fichier déjà archivé n'est jamais écrasé : identique → succès (reprise après
# interruption : PostgreSQL rejoue l'archivage), différent → ÉCHEC. Tout code non nul fait
# conserver le WAL par PostgreSQL, qui réessaie : aucune perte silencieuse. L'échec est visible
# dans pg_stat_archiver (supervision : erreur non résolue = CRITIQUE) et journalisé.
#
# Refus aussi : racine sans marqueur (montage absent), nom inattendu, espace insuffisant (la
# réserve SM_BACKUP_ARCHIVE_RESERVE_MB, 512 Mio par défaut, reste toujours libre).
set -euo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"
SM_OP=archive

SOURCE="${1:-}"
NAME="${2:-}"
[[ -n "$SOURCE" && -n "$NAME" ]] || sm_die "usage : sm-wal-archive.sh %p %f"
SM_ROOT="$(sm_root)"
SM_LOG_FILE="$SM_ROOT/logs/archive-$(date -u +%Y%m).log"
fail() { sm_log "ÉCHEC $NAME : $*"; exit 1; }

# Segments, segments partiels (promotion), historiques de timeline, fichiers .backup.
[[ "$NAME" =~ ^[0-9A-F]{24}(\.partial|\.[0-9A-F]{8}\.backup)?$ || "$NAME" =~ ^[0-9A-F]{8}\.history$ ]] \
  || fail "nom de fichier WAL inattendu"
[[ -f "$SOURCE" ]] || fail "source absente : $SOURCE"
ARCHIVE="$SM_ROOT/wal"
[[ -d "$ARCHIVE" && ! -L "$ARCHIVE" ]] || fail "répertoire d'archive absent : $ARCHIVE"
TARGET="$ARCHIVE/$NAME.gz"

if [[ -e "$TARGET" ]]; then
  if gzip -dc "$TARGET" 2>/dev/null | cmp -s - "$SOURCE"; then exit 0; fi
  fail "déjà archivé avec un contenu DIFFÉRENT (jamais écrasé) : $TARGET"
fi
if [[ -e "$ARCHIVE/$NAME" ]]; then   # ancien format non compressé
  if cmp -s "$ARCHIVE/$NAME" "$SOURCE"; then exit 0; fi
  fail "déjà archivé (non compressé) avec un contenu DIFFÉRENT : $ARCHIVE/$NAME"
fi

need_kb=$(( $(stat -c %s "$SOURCE") / 1024 + 1 ))
reserve_kb=$(( ${SM_BACKUP_ARCHIVE_RESERVE_MB:-512} * 1024 ))
free_kb="$(sm_free_kb "$ARCHIVE")"
(( free_kb - need_kb >= reserve_kb )) \
  || fail "espace insuffisant sur l'archive (${free_kb} Kio libres, réserve ${reserve_kb} Kio)"

TMP="$ARCHIVE/.$NAME.gz.tmp.$$"
trap 'rm -f "$TMP"' EXIT
gzip -c "-${SM_WAL_GZIP_LEVEL:-6}" <"$SOURCE" >"$TMP" || fail "compression impossible"
gzip -dc "$TMP" | cmp -s - "$SOURCE" || fail "relecture : la copie compressée diffère de la source"
chmod 0600 "$TMP"
sync "$TMP" 2>/dev/null || sync
mv "$TMP" "$TARGET" || fail "renommage impossible"
trap - EXIT
sync "$ARCHIVE" 2>/dev/null || sync
