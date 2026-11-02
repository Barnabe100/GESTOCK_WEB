#!/usr/bin/env bash
# Restauration à un instant donné (PITR) dans un répertoire de données NEUF.
#
#   SM_BACKUP_DIR=… sm-restore-pitr.sh --target-time "2026-11-02 10:15:00+00" \
#       --data-dir /srv/pg-restore/2026-11-02 [--backup 20261102T020000Z] [--port 5544] [--start]
#
# Ne touche JAMAIS une base existante : refuse un répertoire non vide ; la production n'est
# basculée sur la copie restaurée qu'après vérification, par une décision humaine (procédure
# docs/exploitation/SAUVEGARDE_RESTAURATION.md). Sauvegarde de base par défaut : la plus récente
# ANTÉRIEURE à l'instant visé. --target-time absent : restauration jusqu'au dernier WAL archivé.
# Fin de restauration : promotion (la copie devient une base autonome, l'archive n'est pas
# modifiée : archive_mode est désactivé sur la copie).
set -euo pipefail

ROOT="${SM_BACKUP_DIR:?SM_BACKUP_DIR doit être défini}"
TARGET="" DATA="" BACKUP="" PORT="" START=0
while (( $# )); do
  case "$1" in
    --target-time) TARGET="$2"; shift 2 ;;
    --data-dir) DATA="$2"; shift 2 ;;
    --backup) BACKUP="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --start) START=1; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
[[ -n "$DATA" ]] || { echo "--data-dir requis" >&2; exit 2; }
if [[ -e "$DATA" ]] && [[ -n "$(ls -A "$DATA" 2>/dev/null)" ]]; then
  echo "refus : $DATA n'est pas vide (jamais de restauration sur une base existante)" >&2
  exit 1
fi

if [[ -z "$BACKUP" ]]; then
  LIMIT="99999999T999999Z"
  if [[ -n "$TARGET" ]]; then LIMIT="$(date -u -d "$TARGET" +%Y%m%dT%H%M%SZ)"; fi
  BACKUP="$(find "$ROOT/base" -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\n' \
    | sort | awk -v limit="$LIMIT" '$0 <= limit' | tail -n 1)"
fi
[[ -n "$BACKUP" && -d "$ROOT/base/$BACKUP" ]] || {
  echo "aucune sauvegarde de base antérieure à l'instant visé" >&2; exit 1; }

mkdir -p "$DATA"
chmod 0700 "$DATA"
cp -a "$ROOT/base/$BACKUP/." "$DATA/"
chmod -R u+w "$DATA"
rm -f "$DATA/backup_info.json" "$DATA/postmaster.pid"
{
  echo ""
  echo "# --- Restauration StockManager ($(date -u +%Y-%m-%dT%H:%M:%SZ), base $BACKUP) ---"
  echo "restore_command = 'cp \"$ROOT/wal/%f\" \"%p\"'"
  [[ -n "$TARGET" ]] && echo "recovery_target_time = '$TARGET'"
  echo "recovery_target_action = 'promote'"
  echo "archive_mode = off"
  [[ -n "$PORT" ]] && echo "port = $PORT"
} >> "$DATA/postgresql.auto.conf"
touch "$DATA/recovery.signal"
echo "base : $BACKUP ; cible : ${TARGET:-dernier WAL archivé} ; données : $DATA"

if (( START )); then
  pg_ctl -D "$DATA" -l "$DATA/restore.log" -w -t 3600 start
  # La promotion termine la restauration : attendre la fin du mode récupération.
  until [[ "$(psql -h 127.0.0.1 -p "${PORT:-5432}" -d postgres -Atc 'SELECT pg_is_in_recovery()' 2>/dev/null)" == "f" ]]; do
    sleep 1
  done
  echo "restauration terminée, base promue"
fi
