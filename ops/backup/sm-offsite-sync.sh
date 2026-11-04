#!/bin/sh
# Copie EXTERNE chiffrée des sauvegardes (Restic, stockage S3-compatible) — R11-0, ADR-0057.
#
#   (service « offsite » de docker-compose.backup.yml, image restic/restic, profil offsite)
#
# NON OPÉRATIONNELLE tant que l'exploitant n'a pas configuré, HORS DÉPÔT :
#   RESTIC_REPOSITORY      ex. s3:https://<point d'accès>/<compartiment>/stockmanager
#   RESTIC_PASSWORD_FILE   clé du dépôt (fichier 0400/0600 monté en lecture seule)
#   AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (ou AWS_SHARED_CREDENTIALS_FILE)
# Sans configuration : statut « configured: false » (la console et la supervision affichent
# « copie externe non configurée ») et code 0 — jamais une réussite simulée.
# Configurée : contrôle du dépôt (jamais initialisé automatiquement : la création de la clé est
# une décision de TechNova), copie de base/ wal/ state/ (chiffrée AES-256 par Restic),
# rétention SM_OFFSITE_KEEP_WITHIN (35d), élagage seulement si SM_OFFSITE_PRUNE=1 (incompatible
# avec un verrouillage d'objets en mode conformité : voir la documentation), puis
# VÉRIFICATION de la copie (restic check --read-data-subset, SM_OFFSITE_CHECK_SUBSET = 5%).
# Script POSIX (l'image restic ne contient pas bash). Aucun secret affiché ni écrit.
set -eu
ROOT="${SM_BACKUP_DIR:-/backups}"
ST="$ROOT/status"
LOG="$ROOT/logs/offsite-$(date -u +%Y%m).log"
START="$(date -u +%s)"
log() { line="$(date -u +%Y-%m-%dT%H:%M:%SZ) [offsite] $*"; echo "$line" >&2; echo "$line" >>"$LOG" 2>/dev/null || true; }
js() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n\r\t' '   ')"; }
iso() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }
write_status() { # statut configuré message [champs supplémentaires]
  now="$(date -u +%s)"
  tmp="$ST/.offsite.json.tmp.$$"
  {
    echo "{"
    echo "  \"schema\": 1,"
    echo "  \"operation\": \"offsite\","
    echo "  \"status\": $(js "$1"),"
    echo "  \"configured\": $2,"
    echo "  \"started_at_epoch\": $START,"
    echo "  \"finished_at\": $(js "$(iso "$now")"),"
    echo "  \"finished_at_epoch\": $now,"
    echo "  \"duration_seconds\": $((now - START)),"
    echo "  \"repository\": $(js "$(printf '%s' "${RESTIC_REPOSITORY:-}" | sed 's#//[^/@]*@#//***@#')"),"
    if [ -n "${4:-}" ]; then printf '%s\n' "$4"; fi
    echo "  \"message\": $(js "$3")"
    echo "}"
  } >"$tmp"
  chmod 0644 "$tmp"; mv -f "$tmp" "$ST/offsite.json"
  if [ "$1" = success ]; then cp "$ST/offsite.json" "$tmp" && mv -f "$tmp" "$ST/offsite.success.json"; fi
}
[ -f "$ROOT/.sm-backup-root" ] || { echo "racine des sauvegardes absente ou non initialisée : $ROOT" >&2; exit 1; }
if [ -z "${RESTIC_REPOSITORY:-}" ] || [ -z "${RESTIC_PASSWORD_FILE:-}" ]; then
  write_status not_configured false "copie externe non configurée (RESTIC_REPOSITORY / RESTIC_PASSWORD_FILE absents)"
  log "non configurée : aucune copie externe"
  exit 0
fi
fail() { log "ÉCHEC : $*"; write_status failed true "$*"; exit 1; }
[ -f "$RESTIC_PASSWORD_FILE" ] || fail "clé du dépôt introuvable (RESTIC_PASSWORD_FILE)"
perm="$(stat -c %a "$RESTIC_PASSWORD_FILE")"
case "$perm" in 400|600) ;; *) fail "clé du dépôt lisible par d'autres comptes (droits $perm, attendu 0400/0600)" ;; esac
command -v restic >/dev/null 2>&1 || fail "restic absent"
command -v flock >/dev/null 2>&1 || fail "flock absent : copie refusée sans verrou"
exec 9>>"$ROOT/locks/backup.lock"
flock -x -w "${SM_BACKUP_LOCK_WAIT_SECONDS:-21600}" 9 || fail "verrou des sauvegardes non obtenu"

restic cat config >/dev/null 2>>"$LOG" || fail "dépôt inaccessible ou non initialisé (restic init : décision de l'exploitant)"
log "copie de base/ wal/ state/ vers le dépôt externe"
summary="$(restic backup --json --tag stockmanager --host "${SM_OFFSITE_HOST:-stockmanager}" \
  --exclude "$ROOT/base/.partial-*" "$ROOT/base" "$ROOT/wal" "$ROOT/state" 2>>"$LOG" | grep '"message_type":"summary"' | tail -n 1)" \
  || fail "restic backup a échoué"
[ -n "$summary" ] || fail "restic backup : aucun résumé"
snap="$(printf '%s' "$summary" | sed -n 's/.*"snapshot_id":"\([0-9a-f]*\)".*/\1/p')"
added="$(printf '%s' "$summary" | sed -n 's/.*"data_added":\([0-9]*\).*/\1/p')"
files="$(printf '%s' "$summary" | sed -n 's/.*"total_files_processed":\([0-9]*\).*/\1/p')"
keep="${SM_OFFSITE_KEEP_WITHIN:-35d}"
if [ "${SM_OFFSITE_PRUNE:-0}" = 1 ]; then
  restic forget --tag stockmanager --keep-within "$keep" --prune >>"$LOG" 2>&1 || fail "restic forget --prune a échoué"
else
  restic forget --tag stockmanager --keep-within "$keep" >>"$LOG" 2>&1 || fail "restic forget a échoué"
fi
subset="${SM_OFFSITE_CHECK_SUBSET:-5%}"
restic check --read-data-subset="$subset" >>"$LOG" 2>&1 || fail "VÉRIFICATION de la copie externe en échec (restic check)"
extra="  \"snapshot_id\": $(js "$snap"),
  \"files\": ${files:-0},
  \"bytes_added\": ${added:-0},
  \"checked_subset\": $(js "$subset"),"
write_status success true "copie externe $snap vérifiée (échantillon $subset)" "$extra"
log "réussite : instantané $snap, $files fichier(s), ${added:-0} octet(s) ajoutés, vérification $subset"
