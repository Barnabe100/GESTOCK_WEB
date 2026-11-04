#!/usr/bin/env bash
# Restitution d'un fichier WAL archivé (restore_command d'une restauration).
#
#   restore_command = 'SM_BACKUP_DIR=/backups /opt/stockmanager/backup/sm-wal-restore.sh %f %p'
#
# Codes de sortie (sémantique de PostgreSQL) :
#   0   fichier restitué (décompressé dans un temporaire puis renommé : jamais de fichier partiel) ;
#   1   fichier absent : fin normale des WAL disponibles (ou historique inexistant) ;
#   126 fichier PRÉSENT mais illisible ou corrompu : PostgreSQL ARRÊTE la restauration (une
#       restauration ne s'arrête jamais en silence avant la cible à cause d'une archive abîmée).
# Ne lit que l'archive ; n'écrit que dans pg_wal de l'instance restaurée (%p).
set -uo pipefail
NAME="${1:-}" DEST="${2:-}"
ROOT="${SM_BACKUP_DIR:-}"
if [[ -z "$NAME" || -z "$DEST" || -z "$ROOT" ]]; then echo "sm-wal-restore : usage %f %p (SM_BACKUP_DIR requis)" >&2; exit 126; fi
if ! [[ "$NAME" =~ ^[0-9A-F]{24}(\.partial)?$ || "$NAME" =~ ^[0-9A-F]{8}\.history$ ]]; then
  echo "sm-wal-restore : nom inattendu $NAME" >&2; exit 126
fi
TMP="$DEST.sm-restore.$$"
if [[ -f "$ROOT/wal/$NAME.gz" ]]; then
  if ! gzip -dc "$ROOT/wal/$NAME.gz" >"$TMP"; then
    rm -f "$TMP"; echo "sm-wal-restore : $NAME.gz CORROMPU — restauration arrêtée" >&2; exit 126
  fi
elif [[ -f "$ROOT/wal/$NAME" ]]; then
  if ! cp "$ROOT/wal/$NAME" "$TMP"; then rm -f "$TMP"; echo "sm-wal-restore : $NAME illisible" >&2; exit 126; fi
else
  exit 1
fi
mv -f "$TMP" "$DEST" || { rm -f "$TMP"; exit 126; }
exit 0
