#!/usr/bin/env bash
# Rétention des sauvegardes (défaut : 30 jours) — à lancer après chaque sauvegarde de base.
#
# Conserve toutes les sauvegardes de base de la fenêtre ET la plus récente plus ancienne que la
# fenêtre (sans elle, le début de la fenêtre ne serait pas restaurable), puis supprime les WAL
# antérieurs au début de la plus ancienne sauvegarde conservée (pg_archivecleanup). Ne supprime
# jamais la dernière sauvegarde. --dry-run : affiche sans supprimer.
set -euo pipefail

ROOT="${SM_BACKUP_DIR:?SM_BACKUP_DIR doit être défini}"
DAYS="${SM_BACKUP_RETENTION_DAYS:-30}"
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1

mapfile -t BACKUPS < <(find "$ROOT/base" -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\n' | sort)
(( ${#BACKUPS[@]} > 0 )) || { echo "aucune sauvegarde de base" >&2; exit 1; }
CUTOFF="$(date -u -d "-$DAYS days" +%Y%m%dT%H%M%SZ)"

KEEP_FROM=0
for i in "${!BACKUPS[@]}"; do
  if [[ "${BACKUPS[$i]}" < "$CUTOFF" ]]; then KEEP_FROM=$i; fi
done
for i in "${!BACKUPS[@]}"; do
  if (( i < KEEP_FROM )); then
    echo "suppression : base/${BACKUPS[$i]}"
    if (( DRY == 0 )); then chmod -R u+w "$ROOT/base/${BACKUPS[$i]}"; rm -rf "$ROOT/base/${BACKUPS[$i]}"; fi
  fi
done
OLDEST="${BACKUPS[$KEEP_FROM]}"
START_WAL="$(sed -n 's/.*"start_wal": "\(.*\)".*/\1/p' "$ROOT/base/$OLDEST/backup_info.json")"
[[ -n "$START_WAL" ]] || { echo "WAL de départ introuvable pour $OLDEST" >&2; exit 1; }
echo "plus ancienne sauvegarde conservée : $OLDEST (WAL $START_WAL)"
if [[ -d "$ROOT/wal" ]]; then
  if (( DRY == 1 )); then
    pg_archivecleanup -n "$ROOT/wal" "$START_WAL" | sed 's/^/suppression : /'
  else
    pg_archivecleanup "$ROOT/wal" "$START_WAL"
  fi
fi
