#!/usr/bin/env bash
# Archivage d'un segment WAL (archive_command de PostgreSQL) — sauvegarde continue, PITR.
#
#   archive_command = '/opt/stockmanager/backup/sm-wal-archive.sh %p %f'
#
# Copie atomique (fichier temporaire, fsync, renommage) dans ${SM_BACKUP_DIR}/wal. Un segment
# déjà archivé n'est jamais écrasé : identique → succès (rejeu après incident), différent →
# ÉCHEC (PostgreSQL conserve le segment et réessaie ; l'alerte d'archivage se déclenche).
# Code de sortie non nul = PostgreSQL garde le WAL : aucune perte silencieuse.
set -euo pipefail

SOURCE="${1:?chemin du segment (%p) requis}"
NAME="${2:?nom du segment (%f) requis}"
ARCHIVE="${SM_BACKUP_DIR:?SM_BACKUP_DIR doit être défini}/wal"

mkdir -p "$ARCHIVE"
TARGET="$ARCHIVE/$NAME"
if [[ -f "$TARGET" ]]; then
  if cmp -s "$SOURCE" "$TARGET"; then
    exit 0
  fi
  echo "sm-wal-archive : $NAME existe déjà avec un contenu différent" >&2
  exit 1
fi
TMP="$ARCHIVE/.$NAME.tmp.$$"
cp "$SOURCE" "$TMP"
sync "$TMP" 2>/dev/null || sync
mv "$TMP" "$TARGET"
