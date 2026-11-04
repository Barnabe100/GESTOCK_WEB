#!/usr/bin/env bash
# Sauvegarde LOGIQUE d'UNE entreprise (R11, ADR-0058) — instantané cohérent, vérifiable.
#
#   SM_BACKUP_DIR=/backups sm-tenant-export.sh --tenant UUID --run-id ID
#       [--source live|pitr] [--source-point ISO] [--host SOCKET_DIR --port PORT]
#   sm-tenant-export.sh --tenant UUID --sha-only [--host … --port …]
#       → « SHA <table> <lignes> <sha256> » par table, sans archive (comparaison d'une copie
#         restaurée avec une sauvegarde : mêmes réglages, même tri, mêmes colonnes).
#
# - Rôle SM_TENANT_BACKUP_PGUSER (défaut stockmanager_tenant_backup) : SELECT seulement, SOUS RLS
#   (contexte app.tenant_id obligatoire : sans lui, rien n'est visible) ; ni superutilisateur ni
#   BYPASSRLS. Le périmètre (tables exportées) est celui des DROITS du rôle, fixés par la
#   migration 0063 : tables exclues (abonnements, licences, postes, journaux, notifications
#   d'échéance) jamais exportées.
# - UNE transaction REPEATABLE READ READ ONLY : toutes les tables du même instant, même sous
#   écritures concurrentes ; aucun verrou bloquant pour les autres entreprises.
# - Format : <table>.csv.gz (CSV avec en-tête, colonnes explicites, ordre de clé primaire,
#   gzip -n déterministe), manifest.tsv (révision Alembic, entreprise, lignes et SHA-256 du CSV
#   NON compressé par table), SHA256SUMS (fichiers compressés), marqueur .complete en dernier.
# - Construit dans <dir>.partial puis renommé : un export interrompu n'est jamais pris pour une
#   sauvegarde (le partiel est supprimé à la sortie, sous gardes).
# Sortie standard, dernière ligne : « ARTIFACT <chemin>|<tables>|<lignes>|<octets>|<sha256 du manifeste> ».
set -euo pipefail
. "$(dirname "$(realpath "${BASH_SOURCE[0]}")")/lib.sh"

TENANT="" RUN="" SOURCE=live POINT="" CHOST="" CPORT="" SHA_ONLY=0
while (( $# )); do
  case "$1" in
    --tenant) TENANT="$2"; shift 2 ;;
    --run-id) RUN="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --source-point) POINT="$2"; shift 2 ;;
    --host) CHOST="$2"; shift 2 ;;
    --port) CPORT="$2"; shift 2 ;;
    --sha-only) SHA_ONLY=1; RUN=00000000; shift ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
SM_ROOT="$(sm_root)"
sm_log_init tenant_export
sm_need psql gzip sha256sum
UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
[[ "$TENANT" =~ $UUID_RE ]] || sm_die "--tenant : identifiant invalide"
[[ "$RUN" =~ ^[0-9a-f]{8}$ ]] || sm_die "--run-id : 8 caractères hexadécimaux"
[[ "$SOURCE" =~ ^(live|pitr)$ ]] || sm_die "--source : live ou pitr"
[[ -z "$POINT" || "$POINT" =~ ^[0-9T:.+Z\ -]{10,40}$ ]] || sm_die "--source-point invalide"
SM_OP=tenant_export
sm_pg_env
export PGUSER="${SM_TENANT_BACKUP_PGUSER:-stockmanager_tenant_backup}"
if [[ -n "$CHOST" ]]; then
  [[ "$CPORT" =~ ^[0-9]+$ ]] || sm_die "--port requis avec --host"
  export PGHOST="$CHOST" PGPORT="$CPORT"
fi

# Tables exportées = tables d'entreprise (tenant_id, RLS activée ET forcée) lisibles par le rôle.
# Colonnes : ordre physique, sans colonne générée ; tri : clé primaire (sinon toutes les colonnes).
TABLES="$(sm_psql -F $'\t' -c "
  SELECT c.relname,
         (SELECT string_agg(quote_ident(a.attname), ',' ORDER BY a.attnum) FROM pg_attribute a
           WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''),
         coalesce((SELECT string_agg(quote_ident(a.attname), ',' ORDER BY k.o)
                     FROM pg_index i, unnest(i.indkey::int2[]) WITH ORDINALITY k(n, o), pg_attribute a
                    WHERE i.indrelid = c.oid AND i.indisprimary AND a.attrelid = c.oid AND a.attnum = k.n),
                  (SELECT string_agg(quote_ident(a.attname), ',' ORDER BY a.attnum) FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped))
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute t ON t.attrelid = c.oid AND t.attname = 'tenant_id' AND NOT t.attisdropped
   WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity
     AND has_table_privilege(c.oid, 'SELECT')
   ORDER BY 1")" || sm_die "connexion impossible avec le rôle $PGUSER"
[[ -n "$TABLES" ]] || sm_die "aucune table lisible par $PGUSER (migration 0063 appliquée ?)"
REVISION="$(sm_psql -c "SELECT version_num FROM alembic_version")" || sm_die "révision du schéma illisible"
[[ "$REVISION" =~ ^[0-9A-Za-z_]{1,32}$ ]] || sm_die "révision du schéma invalide : $REVISION"

if (( SHA_ONLY )); then
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/sm-export-sha.XXXXXX")"
  trap 'rm -rf -- "$WORK"' EXIT
else
LROOT="$SM_ROOT/logical"
[[ -d "$LROOT" && ! -L "$LROOT" ]] || { mkdir -p "$LROOT"; chmod 0700 "$LROOT"; }
TDIR="$LROOT/$TENANT"
[[ ! -L "$TDIR" ]] || sm_die "$TDIR est un lien symbolique : refus"
[[ -d "$TDIR" ]] || { mkdir "$TDIR"; chmod 0700 "$TDIR"; }
STAMP="$(sm_stamp "$(sm_now)")"
NAME="$STAMP-$RUN"
FINAL="$TDIR/$NAME"
WORK="$FINAL.partial"
[[ ! -e "$FINAL" && ! -e "$WORK" ]] || sm_die "$FINAL existe déjà"
mkdir "$WORK"
chmod 0700 "$WORK"
cleanup_partial() {
  local real
  real="$(realpath "$WORK" 2>/dev/null || true)"
  if [[ -n "$real" && "$real" == "$TDIR/$NAME.partial" && -d "$real" && ! -L "$real" ]]; then
    rm -rf -- "$real"
  fi
}
trap cleanup_partial EXIT
fi
trap 'exit 143' TERM
trap 'exit 130' INT

# Script psql : UNE session, UNE transaction, sortie paramétrée de façon déterministe.
SQL="$WORK/.export.sql"
{
  echo '\set ON_ERROR_STOP 1'
  echo "SET datestyle = ISO, YMD; SET intervalstyle = postgres; SET timezone = 'UTC';"
  echo "SET extra_float_digits = 3; SET bytea_output = hex; SET statement_timeout = 0;"
  echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"
  echo "SELECT set_config('app.tenant_id', '$TENANT', true) \\g /dev/null"
  echo "\\o $WORK/.meta"
  echo "SELECT 'created_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"');"
  while IFS=$'\t' read -r t cols order; do
    echo "SELECT 'rows', '$t', count(*) FROM $t;"
  done <<<"$TABLES"
  echo "\\o"
  while IFS=$'\t' read -r t cols order; do
    echo "\\copy (SELECT $cols FROM $t ORDER BY $order) TO PROGRAM 'gzip -n -c > $WORK/$t.csv.gz' WITH (FORMAT csv, HEADER)"
  done <<<"$TABLES"
  echo "COMMIT;"
} >"$SQL"
sm_psql -F $'\t' -f "$SQL" >/dev/null || sm_die "export de l'entreprise $TENANT en échec"
rm -f "$SQL"
if (( SHA_ONLY )); then
  while IFS=$'\t' read -r t _ _; do
    rows="$(awk -F'\t' -v t="$t" '$1 == "rows" && $2 == t {print $3}' "$WORK/.meta")"
    echo "SHA $t $rows $(gzip -dc "$WORK/$t.csv.gz" | sha256sum | cut -d' ' -f1)"
  done <<<"$TABLES"
  exit 0
fi

CREATED="$(awk -F'\t' '$1 == "created_at" {print $2}' "$WORK/.meta")"
{
  echo "# format=stockmanager-tenant-backup-1"
  echo "# tenant_id=$TENANT"
  echo "# alembic_revision=$REVISION"
  echo "# created_at=$CREATED"
  echo "# source=$SOURCE"
  echo "# source_point=${POINT:-$CREATED}"
  echo "# columns=table	rows	sha256	columns"
} >"$WORK/manifest.tsv"
total_rows=0 ntables=0
while IFS=$'\t' read -r t cols order; do
  rows="$(awk -F'\t' -v t="$t" '$1 == "rows" && $2 == t {print $3}' "$WORK/.meta")"
  [[ "$rows" =~ ^[0-9]+$ ]] || sm_die "nombre de lignes absent pour $t"
  [[ -s "$WORK/$t.csv.gz" ]] || sm_die "fichier absent pour $t"
  sha="$(gzip -dc "$WORK/$t.csv.gz" | sha256sum | cut -d' ' -f1)"
  printf '%s\t%s\t%s\t%s\n' "$t" "$rows" "$sha" "$cols" >>"$WORK/manifest.tsv"
  total_rows=$(( total_rows + rows )); ntables=$(( ntables + 1 ))
done <<<"$TABLES"
rm -f "$WORK/.meta"
( cd "$WORK" && sha256sum manifest.tsv ./*.csv.gz >SHA256SUMS && sha256sum -c --quiet SHA256SUMS ) \
  || sm_die "relecture des sommes de contrôle en échec"
MANIFEST_SHA="$(sha256sum "$WORK/manifest.tsv" | cut -d' ' -f1)"
chmod 0400 "$WORK"/*
printf 'complete_at=%s\nmanifest_sha256=%s\n' "$(sm_iso "$(sm_now)")" "$MANIFEST_SHA" >"$WORK/.complete"
chmod 0400 "$WORK/.complete"
mv "$WORK" "$FINAL"
chmod 0500 "$FINAL"
trap - EXIT
BYTES="$(sm_size_bytes "$FINAL")"
sm_log "entreprise $TENANT : $ntables tables, $total_rows lignes → $FINAL"
echo "ARTIFACT $FINAL|$ntables|$total_rows|$BYTES|$MANIFEST_SHA|$REVISION|$CREATED"
