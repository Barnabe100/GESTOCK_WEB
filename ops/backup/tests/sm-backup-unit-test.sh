#!/usr/bin/env bash
# Tests unitaires des scripts de sauvegarde (R11-0), SANS serveur PostgreSQL.
#
#   ops/backup/tests/sm-backup-unit-test.sh
#   CI : dans l'image postgres:16-alpine (bash + busybox, comme la production Docker) :
#   docker run --rm -v "$PWD:/repo:ro" postgres:16-alpine bash /repo/ops/backup/tests/sm-backup-unit-test.sh
#
# Couvre : lecture/écriture JSON, arithmétique des segments et des LSN, garde de la racine,
# archivage (compression, relecture, idempotence, refus d'écrasement, nom, réserve d'espace),
# restitution (0 / 1 / 126), chaîne WAL (trous, timelines, historique manquant), rétention
# (simulation par défaut, sélection, dernière vérifiée, minimum, chemins refusés), verrou
# (exécution concurrente refusée sans écraser le statut), supervision sans base (seuils,
# copie externe), gardes de suppression du test de restauration. Tout dans un répertoire jetable.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPROOT="$(mktemp -d "${TMPDIR:-/tmp}/sm-backup-unit.XXXXXX")"
trap 'rm -rf "$TMPROOT"' EXIT
PASSED=0 FAILED=0
ok() { PASSED=$(( PASSED + 1 )); echo "ok   - $1"; }
ko() { FAILED=$(( FAILED + 1 )); echo "ÉCHEC - $1${2:+ : $2}"; }
eq() { if [[ "$2" == "$3" ]]; then ok "$1"; else ko "$1" "obtenu « $2 », attendu « $3 »"; fi; }
code() { local want="$1" name="$2"; shift 2; local got=0; "$@" >"$TMPROOT/out" 2>&1 || got=$?; eq "$name (code)" "$got" "$want"; }

. "$HERE/lib.sh"

# --- 1. Bibliothèque ---------------------------------------------------------------------------
f="$TMPROOT/s.json"
printf '{\n  "a": 12,\n  "b": "x, \\"y\\"",\n  "c": true,\n  "d": null,\n  "e": -3\n}\n' >"$f"
eq "json entier" "$(sm_json_get "$f" a)" 12
eq "json chaîne avec virgule" "$(sm_json_get "$f" b)" 'x, \"y\"'
eq "json booléen" "$(sm_json_get "$f" c)" true
eq "json null" "$(sm_json_get "$f" d)" null
eq "json négatif" "$(sm_json_get "$f" e)" -3
eq "json absent" "$(sm_json_get "$f" z)" ""
eq "échappement JSON" "$(sm_js $'a"b\\c\nd')" '"a\"b\\c\nd"'
eq "segment → numéro" "$(sm_seg_parse 0000000100000002000000FF)" "1 767"
eq "numéro → segment" "$(sm_seg_name 1 767)" 0000000100000002000000FF
eq "segment suivant (changement d'identifiant)" "$(sm_seg_name 1 768)" 000000010000000300000000
eq "LSN → segment" "$(sm_lsn_seg 0/5000060)" 5
eq "LSN haut → segment" "$(sm_lsn_seg 1/0)" 256
eq "horodatage → époque" "$(sm_stamp_epoch 20261102T101500Z)" "$(date -u -d '2026-11-02 10:15:00' +%s)"
if sm_stamp_epoch 2026-11-02 >/dev/null 2>&1; then ko "horodatage invalide refusé"; else ok "horodatage invalide refusé"; fi
if sm_paths_overlap /a/b /a/b/c && sm_paths_overlap /a/b/c /a/b && ! sm_paths_overlap /a/bc /a/b; then ok "recouvrement de chemins"; else ko "recouvrement de chemins"; fi

# --- 2. Racine ---------------------------------------------------------------------------------
R="$TMPROOT/root"
mkdir -p "$R"
export SM_BACKUP_DIR="$R" SM_BACKUP_ARCHIVE_RESERVE_MB=1 SM_BACKUP_RESERVE_MB=1
code 1 "archivage sans marqueur refusé" "$HERE/sm-wal-archive.sh" /etc/hostname 000000010000000000000001
code 1 "racine relative refusée" env SM_BACKUP_DIR=relatif "$HERE/sm-backup-init.sh"
code 0 "initialisation" "$HERE/sm-backup-init.sh"
code 0 "initialisation idempotente" "$HERE/sm-backup-init.sh"
eq "droits de la racine" "$(stat -c %a "$R")" 711
eq "droits de wal/" "$(stat -c %a "$R/wal")" 700
eq "droits de status/" "$(stat -c %a "$R/status")" 755
ln -s "$TMPROOT" "$TMPROOT/lien"
code 1 "racine lien symbolique refusée" env SM_BACKUP_DIR="$TMPROOT/lien" "$HERE/sm-wal-archive.sh" /etc/hostname 000000010000000000000001

# --- 3. Archivage ------------------------------------------------------------------------------
SEG="$TMPROOT/seg"; head -c 1048576 /dev/urandom >"$SEG"
code 0 "archivage d'un segment" "$HERE/sm-wal-archive.sh" "$SEG" 000000010000000000000001
if gzip -dc "$R/wal/000000010000000000000001.gz" | cmp -s - "$SEG"; then ok "archive compressée identique à la source"; else ko "archive compressée identique"; fi
eq "droits du segment archivé" "$(stat -c %a "$R/wal/000000010000000000000001.gz")" 600
code 0 "rejeu identique accepté (reprise après interruption)" "$HERE/sm-wal-archive.sh" "$SEG" 000000010000000000000001
before="$(sha256sum "$R/wal/000000010000000000000001.gz")"
printf 'X' >>"$SEG"
code 1 "contenu différent jamais écrasé" "$HERE/sm-wal-archive.sh" "$SEG" 000000010000000000000001
eq "archive inchangée après refus" "$(sha256sum "$R/wal/000000010000000000000001.gz")" "$before"
code 1 "nom inattendu refusé" "$HERE/sm-wal-archive.sh" "$SEG" "../evasion"
code 1 "source absente" "$HERE/sm-wal-archive.sh" "$TMPROOT/absent" 000000010000000000000002
code 1 "réserve d'espace" env SM_BACKUP_ARCHIVE_RESERVE_MB=999999999 "$HERE/sm-wal-archive.sh" "$SEG" 000000010000000000000003
[[ ! -e "$R/wal/000000010000000000000003.gz" ]] && ok "rien écrit sous la réserve" || ko "rien écrit sous la réserve"
tmps=("$R"/wal/.*.tmp.*); [[ ! -e "${tmps[0]}" ]] && ok "aucun temporaire laissé" || ko "aucun temporaire laissé"
printf 'tl\n' >"$TMPROOT/h"; code 0 "historique de timeline archivé" "$HERE/sm-wal-archive.sh" "$TMPROOT/h" 00000002.history
grep -q "ÉCHEC" "$R"/logs/archive-*.log && ok "échecs d'archivage journalisés" || ko "échecs journalisés"

# --- 4. Restitution ----------------------------------------------------------------------------
code 0 "restitution" "$HERE/sm-wal-restore.sh" 000000010000000000000001 "$TMPROOT/r1"
code 1 "restitution d'un absent (fin normale)" "$HERE/sm-wal-restore.sh" 000000010000000000000009 "$TMPROOT/r2"
cp "$R/wal/000000010000000000000001.gz" "$R/wal/000000010000000000000005.gz"; chmod u+w "$R/wal/000000010000000000000005.gz"
printf 'ZZZZ' | dd of="$R/wal/000000010000000000000005.gz" bs=1 seek=100 conv=notrunc 2>/dev/null
code 126 "restitution d'une archive corrompue : arrêt" "$HERE/sm-wal-restore.sh" 000000010000000000000005 "$TMPROOT/r3"
[[ ! -e "$TMPROOT/r3" ]] && ok "aucun fichier partiel restitué" || ko "aucun fichier partiel restitué"
code 126 "nom inattendu" "$HERE/sm-wal-restore.sh" "../x" "$TMPROOT/r4"
rm -f "$R"/wal/*

# --- 5. Chaîne WAL et timelines ------------------------------------------------------------------
SM_ROOT="$(realpath "$R")"
for n in 2 3 5 6; do : | gzip >"$R/wal/$(sm_seg_name 1 "$n").gz"; done
chain="$(sm_wal_chain 1 2)"
eq "trou détecté" "$(grep -c '^M' <<<"$chain")" 1
eq "segment manquant identifié" "$(grep '^M' <<<"$chain")" "M 1 4"
eq "fin de chaîne" "$(grep '^S' <<<"$chain" | tail -n 1 | cut -d' ' -f3)" 6
: | gzip >"$R/wal/$(sm_seg_name 1 4).gz"
printf '1\t0/6000000\tno recovery target specified\n' | gzip >"$R/wal/00000002.history.gz"
for n in 6 7 8; do : | gzip >"$R/wal/$(sm_seg_name 2 "$n").gz"; done
chain="$(sm_wal_chain 1 2)"
eq "timeline suivie (segment 6 lu sur la timeline 2)" "$(grep '^S 2 6 ' <<<"$chain" | wc -l | tr -d ' ')" 1
eq "chaîne continue jusqu'à la timeline 2" "$(grep -c '^M' <<<"$chain")" 0
eq "nombre de timelines" "$(grep '^T' <<<"$chain")" "T 2"
rm "$R/wal/00000002.history.gz"
grep -q "^E historique de timeline absent" <<<"$(sm_wal_chain 1 2)" && ok "historique manquant signalé" || ko "historique manquant signalé"
rm -f "$R"/wal/*

# --- 6. Rétention ------------------------------------------------------------------------------
fake_backup() { # horodatage fin_époque start_wal [verified]
  local d="$R/base/$1"; mkdir -p "$d"
  printf '{\n  "stamp": "%s",\n  "finished_at_epoch": %s,\n  "start_wal": "%s",\n  "size_bytes": 1000,\n  "verified": %s\n}\n' \
    "$1" "$2" "$3" "${4:-true}" >"$d/backup_info.json"
  : >"$d/backup_manifest"
}
NOW=$(date -u +%s); DAY=86400
fake_backup 20260901T011500Z $(( NOW - 60 * DAY )) "$(sm_seg_name 1 2)"
fake_backup 20260920T011500Z $(( NOW - 41 * DAY )) "$(sm_seg_name 1 4)"
fake_backup 20261001T011500Z $(( NOW - 31 * DAY )) "$(sm_seg_name 1 6)"
fake_backup 20261020T011500Z $(( NOW - 12 * DAY )) "$(sm_seg_name 1 8)"
fake_backup 20261101T011500Z $(( NOW - 1 * DAY )) "$(sm_seg_name 1 10)"
for n in $(seq 1 11); do : | gzip >"$R/wal/$(sm_seg_name 1 "$n").gz"; done
code 0 "rétention : simulation par défaut" "$HERE/sm-backup-prune.sh"
eq "simulation : rien supprimé" "$(ls "$R/base" | wc -l | tr -d ' ')" 5
eq "simulation : mode" "$(sm_json_get "$R/status/prune.json" mode)" dry-run
grep -q '"backups_deleted": \["20260901T011500Z", "20260920T011500Z"\]' "$R/status/prune.json" && ok "simulation : sélection (ancre = dernière avant la fenêtre)" || ko "simulation : sélection" "$(grep backups_deleted "$R/status/prune.json")"
# Ancre non vérifiée : la plus récente VÉRIFIÉE avant la fenêtre est conservée.
echo "failed 0 daily" >"$R/state/backups/20261001T011500Z.verify"
"$HERE/sm-backup-prune.sh" >/dev/null 2>&1
grep -q '"backups_deleted": \["20260901T011500Z"\]' "$R/status/prune.json" && ok "ancre non vérifiée : sauvegarde vérifiée antérieure conservée" || ko "ancre non vérifiée" "$(grep backups_deleted "$R/status/prune.json")"
rm "$R/state/backups/20261001T011500Z.verify"
# Toutes les récentes en échec : rien après la dernière vérifiée n'est supprimé.
for s in 20261001T011500Z 20261020T011500Z 20261101T011500Z; do echo "failed 0 daily" >"$R/state/backups/$s.verify"; done
"$HERE/sm-backup-prune.sh" >/dev/null 2>&1
grep -q '"latest_verified": "20260920T011500Z"' "$R/status/prune.json" && grep -q '"backups_deleted": \["20260901T011500Z"\]' "$R/status/prune.json" && ok "dernière vérifiée jamais supprimée" || ko "dernière vérifiée" "$(grep -E 'latest_verified|backups_deleted' "$R/status/prune.json")"
rm -f "$R"/state/backups/*.verify
code 0 "minimum conservé" env SM_BACKUP_KEEP_MIN=5 "$HERE/sm-backup-prune.sh"
grep -q '"backups_deleted": \[\]' "$R/status/prune.json" && ok "SM_BACKUP_KEEP_MIN respecté" || ko "SM_BACKUP_KEEP_MIN"
rm "$R/base/20260901T011500Z/backup_manifest"
code 1 "sauvegarde sans manifeste : suppression refusée" "$HERE/sm-backup-prune.sh" --apply
eq "refus : rien supprimé" "$(ls "$R/base" | wc -l | tr -d ' ')" 5
: >"$R/base/20260901T011500Z/backup_manifest"
code 0 "rétention appliquée" "$HERE/sm-backup-prune.sh" --apply
eq "sauvegardes conservées" "$(ls "$R/base" | tr '\n' ' ')" "20261001T011500Z 20261020T011500Z 20261101T011500Z "
eq "WAL antérieurs supprimés, chaîne conservée" "$(ls "$R/wal" | head -n 1)" "$(sm_seg_name 1 6).gz"
grep -q "SUPPRIMÉE : base/20260901T011500Z" "$R"/logs/prune-*.log && ok "suppressions journalisées" || ko "suppressions journalisées"
code 2 "option inconnue" "$HERE/sm-backup-prune.sh" --force

# --- 7. Verrou ---------------------------------------------------------------------------------
if command -v flock >/dev/null 2>&1; then
  ( exec 9>>"$R/locks/backup.lock"; flock -x 9; sleep 6 ) &
else
  ( mkdir "$R/locks/backup.lock.d"; echo $BASHPID >"$R/locks/backup.lock.d/pid"; sleep 6; rm -rf "$R/locks/backup.lock.d" ) &
fi
HOLDER=$!; sleep 1
cp "$R/status/prune.json" "$TMPROOT/prune.before"
code 75 "exécution concurrente refusée" "$HERE/sm-backup-prune.sh"
if cmp -s "$R/status/prune.json" "$TMPROOT/prune.before"; then ok "le refus n'écrase pas le statut de l'opération en cours"; else ko "statut écrasé par le refus"; fi
code 0 "attente du verrou (--wait)" "$HERE/sm-backup-prune.sh" --wait 30
wait "$HOLDER" 2>/dev/null || true

# --- 8. Supervision sans base ------------------------------------------------------------------
st="$R/status"
mk() { # op âge_secondes statut
  local e=$(( NOW - $2 ))
  printf '{\n  "status": "%s",\n  "started_at_epoch": %s,\n  "finished_at_epoch": %s,\n  "message": "m"\n}\n' "$3" $(( e - 60 )) "$e" >"$st/$1.json"
  [[ "$3" == success ]] && cp "$st/$1.json" "$st/$1.success.json"
  return 0
}
mk base_backup 3600 success; mk verify 3600 success; mk restore_test 3600 success; mk prune 3600 success
code 1 "supervision : copie externe non configurée = avertissement" "$HERE/sm-backup-health.sh" --no-db --write
grep -q "copie externe NON CONFIGURÉE" "$TMPROOT/out" && ok "copie externe signalée" || ko "copie externe signalée"
eq "health.json écrit" "$(sm_json_get "$st/health.json" level)" AVERTISSEMENT
mk base_backup $(( 9 * DAY )) success
code 2 "sauvegarde complète > 8 jours : critique" "$HERE/sm-backup-health.sh" --no-db
mk base_backup 3600 success; mk verify $(( 27 * 3600 )) success
code 2 "vérification > 26 h : critique" "$HERE/sm-backup-health.sh" --no-db
mk verify 3600 success; mk restore_test $(( 36 * DAY )) success
code 2 "test de restauration > 35 jours : critique" "$HERE/sm-backup-health.sh" --no-db
mk restore_test 3600 success; mk verify 600 failed
code 2 "vérification en échec : critique" "$HERE/sm-backup-health.sh" --no-db
mk verify 3600 success; mk base_backup $(( 30 * 3600 )) running
grep -q "bloquée" <<<"$("$HERE/sm-backup-health.sh" --no-db 2>&1)" && ok "opération bloquée signalée" || ko "opération bloquée signalée"
mk base_backup 3600 success
printf '{\n  "status": "success",\n  "configured": true,\n  "finished_at_epoch": %s\n}\n' "$NOW" >"$st/offsite.json"; cp "$st/offsite.json" "$st/offsite.success.json"
code 0 "tout conforme (copie externe configurée)" env SM_BACKUP_DISK_WARN_FREE_PERCENT=0 SM_BACKUP_DISK_CRITICAL_FREE_PERCENT=0 "$HERE/sm-backup-health.sh" --no-db

# --- 9. Gardes de suppression du test de restauration --------------------------------------------
if command -v pg_ctl >/dev/null 2>&1 && command -v pg_amcheck >/dev/null 2>&1; then
  RT="$R/restore-test" RUN="20260101T000000Z-0123456789abcdef"
  mkdir -p "$RT/$RUN/data"
  code 3 "sans marqueur : refus" "$HERE/sm-restore-test.sh" --cleanup "$RT/$RUN"
  printf 'run_id=%s\npath=/ailleurs\n' "$RUN" >"$RT/$RUN/.sm-restore-test"
  code 3 "marqueur non conforme : refus" "$HERE/sm-restore-test.sh" --cleanup "$RT/$RUN"
  printf 'run_id=%s\npath=%s\n' "$RUN" "$(realpath "$RT/$RUN")" >"$RT/$RUN/.sm-restore-test"
  touch "$RT/$RUN/data/postmaster.pid"
  code 3 "postmaster.pid présent : refus" "$HERE/sm-restore-test.sh" --cleanup "$RT/$RUN"
  rm "$RT/$RUN/data/postmaster.pid"; ln -s /etc "$RT/$RUN/data/lien"
  code 3 "lien symbolique interne : refus" "$HERE/sm-restore-test.sh" --cleanup "$RT/$RUN"
  [[ -e /etc/hostname || -e /etc/passwd ]] && ok "cible du lien intacte" || ko "cible du lien"
  rm "$RT/$RUN/data/lien"
  mkdir -p "$TMPROOT/precieux"; touch "$TMPROOT/precieux/f"; ln -s "$TMPROOT/precieux" "$RT/20260101T000000Z-fedcba9876543210"
  code 3 "répertoire lien symbolique : refus" "$HERE/sm-restore-test.sh" --cleanup "$RT/20260101T000000Z-fedcba9876543210"
  [[ -f "$TMPROOT/precieux/f" ]] && ok "cible intacte" || ko "cible intacte"
  code 3 "hors de restore-test : refus" "$HERE/sm-restore-test.sh" --cleanup "$R/base/20261101T011500Z"
  code 3 "nom non conforme : refus" "$HERE/sm-restore-test.sh" --cleanup "$RT/../base"
  [[ -d "$R/base/20261101T011500Z" ]] && ok "sauvegardes intactes" || ko "sauvegardes intactes"
  code 0 "suppression conforme" "$HERE/sm-restore-test.sh" --cleanup "$RT/$RUN"
  [[ ! -e "$RT/$RUN" ]] && ok "environnement supprimé" || ko "environnement supprimé"
else
  ko "gardes de suppression" "pg_ctl / pg_amcheck absents (lancer dans l'image postgres:16)"
fi

echo
echo "$PASSED réussi(s), $FAILED échec(s)"
(( FAILED == 0 ))
