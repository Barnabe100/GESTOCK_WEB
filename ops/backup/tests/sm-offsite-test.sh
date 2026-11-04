#!/bin/sh
# Test de la copie externe (R11-0) avec un dépôt Restic LOCAL et une clé éphémère générée pour
# le test (aucun fournisseur S3, aucun secret réel). CI : image restic/restic (POSIX sh) :
#   docker run --rm --entrypoint sh -v "$PWD:/repo:ro" restic/restic:0.17.3 /repo/ops/backup/tests/sm-offsite-test.sh
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
R="$T/root"; F=0
for d in wal base state locks logs status; do mkdir -p "$R/$d"; done
: >"$R/.sm-backup-root"
mkdir -p "$R/base/20261101T011500Z"; echo data >"$R/base/20261101T011500Z/f"
echo w | gzip >"$R/wal/000000010000000000000001.gz"
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "ÉCHEC - $1 : « $2 » ≠ « $3 »"; F=1; fi; }
field() { sed -n "s/^  \"$1\": \"\{0,1\}\([^\",]*\)\"\{0,1\},\{0,1\}\$/\1/p" "$R/status/offsite.json" | head -n 1; }
run() { SM_BACKUP_DIR="$R" sh "$HERE/sm-offsite-sync.sh" >/dev/null 2>&1; echo $?; }

check "non configurée : code" "$(run)" 0
check "non configurée : statut" "$(field status)" not_configured
check "non configurée : jamais une réussite" "$(field configured)" false
[ ! -e "$R/status/offsite.success.json" ] && echo "ok   - aucune réussite enregistrée" || { echo "ÉCHEC - réussite simulée"; F=1; }
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' >"$T/key"; chmod 600 "$T/key"
RESTIC_PASSWORD_FILE="$T/key" restic init -q -r "$T/repo" >/dev/null 2>&1
export RESTIC_PASSWORD_FILE="$T/key" RESTIC_REPOSITORY="$T/repo"
check "configurée : code" "$(run)" 0
check "configurée : statut" "$(field status)" success
[ -n "$(field snapshot_id)" ] && echo "ok   - instantané enregistré" || { echo "ÉCHEC - instantané"; F=1; }
check "copie relisible (restic check)" "$(restic check -q >/dev/null 2>&1; echo $?)" 0
restic restore latest -q --target "$T/restored" >/dev/null 2>&1
check "copie restaurée identique" "$(cat "$T/restored$R/base/20261101T011500Z/f" 2>/dev/null)" data
grep -q "$T/key\|$(cat "$T/key")" "$R/status/offsite.json" && { echo "ÉCHEC - clé dans le statut"; F=1; } || echo "ok   - aucune clé dans le statut"
chmod 644 "$T/key"
check "clé lisible par d'autres : refus" "$(run)" 1
chmod 600 "$T/key"
check "dépôt absent : refus" "$(RESTIC_REPOSITORY="$T/absent" run)" 1
check "dépôt absent : statut" "$(field status)" failed
exit "$F"
