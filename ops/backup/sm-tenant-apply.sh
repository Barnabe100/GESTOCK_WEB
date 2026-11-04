#!/usr/bin/env bash
# Application d'une sauvegarde LOGIQUE à UNE entreprise (R11, ADR-0058) — tout ou rien.
#
#   SM_BACKUP_DIR=/backups sm-tenant-apply.sh --tenant UUID --artifact DIR --operation UUID
#       [--host SOCKET_DIR --port PORT] [--require-lock] [--report FICHIER]
#
# Sur la PRODUCTION, n'est lancé par l'agent qu'APRÈS : vérification sur une copie isolée,
# approbation par un second administrateur, sauvegarde préalable de l'entreprise, verrou de
# l'entreprise (--require-lock : relu dans la transaction). Sur une COPIE (--host) : épreuve de
# la vérification.
#
# Garanties :
# - rôle SM_TENANT_RESTORE_PGUSER (défaut stockmanager_tenant_restore), sans superutilisateur ni
#   BYPASSRLS, contexte app.tenant_id : la RLS limite CHAQUE lecture, suppression et insertion à
#   cette entreprise — une ligne d'une autre entreprise ne peut être ni vue, ni supprimée, ni
#   insérée (WITH CHECK), quel que soit le contenu de l'archive ;
# - périmètre = DROITS du rôle (migration 0063) : tables remplacées (DELETE), compteurs fusionnés
#   au maximum (jamais en arrière), tables exclues jamais écrites ; l'archive doit couvrir
#   EXACTEMENT ce périmètre, avec la MÊME révision de schéma et les mêmes colonnes ;
# - intégrité de l'archive revérifiée (SHA256SUMS, manifeste, marqueur .complete) ;
# - session_replication_role = replica le temps du chargement (les clés étrangères et les gardes
#   d'immuabilité sont des déclencheurs : un instantané se recharge en bloc), PUIS, dans la même
#   transaction, contrôles explicites : nombre de lignes par table, clés étrangères dans les DEUX
#   sens (lignes restaurées → parents ; lignes conservées → lignes restaurées), invariants
#   (restore-checks.sql) ; CHECK, NOT NULL et unicité restent appliqués par PostgreSQL pendant le
#   chargement (ce ne sont pas des déclencheurs) ; la seule règle d'insertion portée par un
#   déclencheur (miroir des codes-barres) est contenue dans l'instantané et contrôlée ;
# - effacements à 30 jours réappliqués (téléphones des commandes, notifications des membres) ;
# - entrée « backup.tenant_restored » au journal de l'entreprise ; COMMIT seulement si tout est
#   conforme, sinon ROLLBACK : rien n'est modifié.
set -euo pipefail
HERE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
. "$HERE/lib.sh"

TENANT="" ARTIFACT="" OPERATION="" CHOST="" CPORT="" REQUIRE_LOCK=0 REPORT=""
while (( $# )); do
  case "$1" in
    --tenant) TENANT="$2"; shift 2 ;;
    --artifact) ARTIFACT="$2"; shift 2 ;;
    --operation) OPERATION="$2"; shift 2 ;;
    --host) CHOST="$2"; shift 2 ;;
    --port) CPORT="$2"; shift 2 ;;
    --require-lock) REQUIRE_LOCK=1; shift ;;
    --report) REPORT="$2"; shift 2 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done
SM_ROOT="$(sm_root)"
sm_log_init tenant_apply
SM_OP=tenant_apply
sm_need psql gzip sha256sum
UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
[[ "$TENANT" =~ $UUID_RE ]] || sm_die "--tenant : identifiant invalide"
[[ "$OPERATION" =~ $UUID_RE ]] || sm_die "--operation : identifiant invalide"
# Constantes des effacements (code : CONTACT_RETENTION_DAYS, RETENTION_DAYS ; test statique).
CONTACT_RETENTION_DAYS=30
MEMBER_NOTIFICATION_RETENTION_DAYS=30

# --- Archive : emplacement, complétude, intégrité ---------------------------------------------
[[ -n "$ARTIFACT" && -d "$ARTIFACT" && ! -L "$ARTIFACT" ]] || sm_die "archive absente : $ARTIFACT"
ARTIFACT="$(realpath "$ARTIFACT")"
[[ "$ARTIFACT" =~ ^$SM_ROOT/logical/$TENANT/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$ ]] \
  || sm_die "archive hors de logical/$TENANT/ ou nom non conforme : $ARTIFACT"
[[ -f "$ARTIFACT/.complete" ]] || sm_die "archive incomplète (marqueur .complete absent)"
( cd "$ARTIFACT" && sha256sum -c SHA256SUMS ) >/dev/null 2>&1 || sm_die "sommes de contrôle de l'archive en échec"
MSHA="$(sha256sum "$ARTIFACT/manifest.tsv" | cut -d' ' -f1)"
grep -qx "manifest_sha256=$MSHA" "$ARTIFACT/.complete" || sm_die "manifeste différent de celui de la sauvegarde"
meta() { sed -n "s/^# $1=//p" "$ARTIFACT/manifest.tsv" | head -n 1; }
[[ "$(meta format)" == stockmanager-tenant-backup-1 ]] || sm_die "format d'archive inconnu"
[[ "$(meta tenant_id)" == "$TENANT" ]] || sm_die "archive d'une AUTRE entreprise : refus"
ART_REV="$(meta alembic_revision)"

sm_pg_env
export PGUSER="${SM_TENANT_RESTORE_PGUSER:-stockmanager_tenant_restore}"
export PGAPPNAME="stockmanager-tenant-apply"
if [[ -n "$CHOST" ]]; then
  [[ "$CPORT" =~ ^[0-9]+$ ]] || sm_die "--port requis avec --host"
  export PGHOST="$CHOST" PGPORT="$CPORT"
fi
REV="$(sm_psql -c "SELECT version_num FROM alembic_version")" || sm_die "connexion impossible avec le rôle $PGUSER"
[[ "$REV" == "$ART_REV" ]] || sm_die "révision du schéma différente (archive $ART_REV, base $REV) : restauration refusée"

# --- Périmètre : droits du rôle ----------------------------------------------------------------
# R = remplacée (DELETE), M = fusionnée (INSERT + UPDATE sans DELETE) ; colonnes physiques.
SCOPE="$(sm_psql -F $'\t' -c "
  SELECT c.relname,
         CASE WHEN has_table_privilege(c.oid, 'DELETE') THEN 'R' ELSE 'M' END,
         (SELECT string_agg(quote_ident(a.attname), ',' ORDER BY a.attnum) FROM pg_attribute a
           WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''),
         (SELECT string_agg(quote_ident(a.attname), ',' ORDER BY k.o)
            FROM pg_index i, unnest(i.indkey::int2[]) WITH ORDINALITY k(n, o), pg_attribute a
           WHERE i.indrelid = c.oid AND i.indisprimary AND a.attrelid = c.oid AND a.attnum = k.n),
         (SELECT string_agg(quote_ident(a.attname) || ' ' || format_type(a.atttypid, a.atttypmod), ', ' ORDER BY a.attnum)
            FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = '')
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute t ON t.attrelid = c.oid AND t.attname = 'tenant_id' AND NOT t.attisdropped
   WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity
     AND has_table_privilege(c.oid, 'INSERT')
     AND (has_table_privilege(c.oid, 'DELETE') OR has_table_privilege(c.oid, 'UPDATE'))
   ORDER BY 1")"
[[ -n "$SCOPE" ]] || sm_die "aucune table restaurable pour $PGUSER (migration 0063 appliquée ?)"
declare -A KIND COLS PK TYPES ROWS
while IFS=$'\t' read -r t k cols pk types; do
  KIND[$t]="$k"; COLS[$t]="$cols"; PK[$t]="$pk"; TYPES[$t]="$types"
done <<<"$SCOPE"
declare -A SEEN
while IFS=$'\t' read -r t rows sha cols; do
  [[ "$t" == \#* || -z "$t" ]] && continue
  [[ -n "${KIND[$t]:-}" ]] || sm_die "table $t de l'archive hors du périmètre restaurable : refus"
  [[ "$cols" == "${COLS[$t]}" ]] || sm_die "colonnes de $t différentes du schéma actuel : refus"
  [[ "$rows" =~ ^[0-9]+$ ]] || sm_die "manifeste invalide pour $t"
  [[ -f "$ARTIFACT/$t.csv.gz" ]] || sm_die "fichier absent : $t.csv.gz"
  [[ "$(gzip -dc "$ARTIFACT/$t.csv.gz" | sha256sum | cut -d' ' -f1)" == "$sha" ]] || sm_die "contenu de $t altéré"
  ROWS[$t]="$rows"; SEEN[$t]=1
done <"$ARTIFACT/manifest.tsv"
for t in "${!KIND[@]}"; do [[ -n "${SEEN[$t]:-}" ]] || sm_die "table $t absente de l'archive : refus"; done
for t in "${!KIND[@]}"; do
  if [[ "${KIND[$t]}" == M ]]; then
    [[ "$t" == document_sequences && "${COLS[$t]}" == "tenant_id,sequence_key,next_value" ]] \
      || sm_die "table fusionnée inattendue ou modifiée : $t (relecture du code requise)"
  fi
done
mapfile -t ORDERED < <(printf '%s\n' "${!KIND[@]}" | sort)
REPLACED="" ALL=""
for t in "${ORDERED[@]}"; do
  ALL+="${ALL:+,}'$t'"
  [[ "${KIND[$t]}" == R ]] && REPLACED+="${REPLACED:+,}'$t'"
done

# --- Transaction --------------------------------------------------------------------------------
WORK="$(mktemp -d "${TMPDIR:-/tmp}/sm-apply.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT
SQL="$WORK/apply.sql"
# Les « \\… » écrits sont des méta-commandes de psql, jamais des séquences d'échappement.
# shellcheck disable=SC2028
{
  echo '\set ON_ERROR_STOP 1'
  echo "SET datestyle = ISO, YMD; SET intervalstyle = postgres; SET timezone = 'UTC';"
  echo "BEGIN;"
  echo "SET LOCAL lock_timeout = '${SM_RESTORE_LOCK_TIMEOUT:-60s}';"
  echo "SELECT set_config('app.tenant_id', '$TENANT', true) \\g /dev/null"
  if (( REQUIRE_LOCK )); then
    echo "DO \$\$ BEGIN IF NOT EXISTS (SELECT 1 FROM tenants WHERE id = '$TENANT' AND restore_locked_at IS NOT NULL AND restore_operation_id = '$OPERATION') THEN RAISE EXCEPTION 'sm-check|tenant_locked|false|entreprise non verrouillée pour cette opération'; END IF; END \$\$;"
  fi
  echo "\\o $WORK/before.tsv"
  for t in "${ORDERED[@]}"; do echo "SELECT '$t', count(*) FROM $t;"; done
  echo "\\o"
  echo "SET LOCAL session_replication_role = replica;"
  # COPY FROM est refusé par PostgreSQL sous RLS : chargement dans une table TEMPORAIRE (sans
  # RLS, privée à la session), contrôle de l'entreprise, puis INSERT … SELECT, SOUMIS à la RLS.
  for t in "${ORDERED[@]}"; do
    echo "CREATE TEMP TABLE sm_stage_$t (${TYPES[$t]}) ON COMMIT DROP;"
    echo "\\copy sm_stage_$t(${COLS[$t]}) FROM PROGRAM 'gzip -dc $ARTIFACT/$t.csv.gz' WITH (FORMAT csv, HEADER)"
    echo "DO \$\$ BEGIN IF EXISTS (SELECT 1 FROM sm_stage_$t WHERE tenant_id IS DISTINCT FROM '$TENANT') THEN RAISE EXCEPTION 'sm-check|tenant_rows|false|ligne d''une autre entreprise dans l''archive ($t)'; END IF; END \$\$;"
  done
  for t in "${ORDERED[@]}"; do
    if [[ "${KIND[$t]}" == R ]]; then
      echo "DELETE FROM $t;"
      echo "INSERT INTO $t (${COLS[$t]}) SELECT ${COLS[$t]} FROM sm_stage_$t;"
    else
      echo "INSERT INTO $t (${COLS[$t]}) SELECT ${COLS[$t]} FROM sm_stage_$t ON CONFLICT (${PK[$t]}) DO UPDATE SET next_value = GREATEST($t.next_value, EXCLUDED.next_value);"
    fi
  done
  echo "SET LOCAL session_replication_role = origin;"
  # Contrôles : chaque échec lève une exception (ROLLBACK) ; chaque réussite est rapportée.
  echo "DO \$\$"
  echo "DECLARE n bigint; bad text := ''; total int := 0; r record;"
  echo "BEGIN"
  for t in "${ORDERED[@]}"; do
    if [[ "${KIND[$t]}" == R ]]; then
      echo "  SELECT count(*) INTO n FROM $t; IF n <> ${ROWS[$t]} THEN bad := bad || ' $t=' || n || '/${ROWS[$t]}'; END IF;"
    else
      echo "  SELECT count(*) INTO n FROM sm_stage_$t m WHERE NOT EXISTS (SELECT 1 FROM $t x WHERE x.tenant_id = m.tenant_id AND x.sequence_key = m.sequence_key AND x.next_value >= m.next_value); IF n <> 0 THEN bad := bad || ' $t:compteurs=' || n; END IF;"
      echo "  SELECT count(*) INTO n FROM sm_stage_$t; IF n <> ${ROWS[$t]} THEN bad := bad || ' $t=' || n || '/${ROWS[$t]}'; END IF;"
    fi
  done
  echo "  IF bad <> '' THEN RAISE EXCEPTION 'sm-check|row_counts|false|%', bad; END IF;"
  echo "  RAISE NOTICE 'sm-check|row_counts|true|${#ORDERED[@]} table(s) : nombres de lignes identiques à la sauvegarde';"
  # Clés étrangères touchant le périmètre, dans les deux sens. Parent invisible (RLS : autre
  # entreprise) = orphelin : échec, jamais un succès par défaut. Un contrôle impossible (droit
  # absent) lève une erreur : échec.
  echo "  FOR r IN SELECT k.conname, k.conrelid::regclass AS child, k.confrelid::regclass AS parent,"
  echo "      (SELECT string_agg(format('c.%I IS NOT NULL', a.attname), ' AND ') FROM unnest(k.conkey) u(x) JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.x) AS notnull,"
  echo "      (SELECT string_agg(format('p.%I = c.%I', pa.attname, ca.attname), ' AND ') FROM unnest(k.conkey, k.confkey) u(c, p)"
  echo "         JOIN pg_attribute ca ON ca.attrelid = k.conrelid AND ca.attnum = u.c JOIN pg_attribute pa ON pa.attrelid = k.confrelid AND pa.attnum = u.p) AS eq"
  echo "    FROM pg_constraint k WHERE k.contype = 'f' AND k.connamespace = 'public'::regnamespace"
  echo "      AND (k.conrelid::regclass::text IN ($ALL) OR k.confrelid::regclass::text IN ($REPLACED)) ORDER BY 1 LOOP"
  echo "    EXECUTE format('SELECT count(*) FROM %s c WHERE %s AND NOT EXISTS (SELECT 1 FROM %s p WHERE %s)', r.child, r.notnull, r.parent, r.eq) INTO n;"
  echo "    total := total + 1;"
  echo "    IF n <> 0 THEN bad := bad || ' ' || r.conname || '=' || n; END IF;"
  echo "  END LOOP;"
  echo "  IF bad <> '' THEN RAISE EXCEPTION 'sm-check|foreign_keys|false|orphelins :%', bad; END IF;"
  echo "  IF total = 0 THEN RAISE EXCEPTION 'sm-check|foreign_keys|false|aucune clé étrangère contrôlée'; END IF;"
  echo "  RAISE NOTICE 'sm-check|foreign_keys|true|% clé(s) étrangère(s) contrôlée(s) dans les deux sens, aucun orphelin', total;"
  # Miroir des codes-barres (seul déclencheur d'insertion des tables restaurées).
  echo "  SELECT count(*) INTO n FROM catalog_articles a WHERE a.barcode IS NOT NULL AND a.is_active"
  echo "    AND NOT EXISTS (SELECT 1 FROM catalog_barcodes b WHERE b.tenant_id = a.tenant_id AND b.article_id = a.id AND b.code = a.barcode);"
  echo "  IF n <> 0 THEN RAISE EXCEPTION 'sm-check|barcode_mirror|false|% code(s) principal(aux) sans miroir', n; END IF;"
  echo "  RAISE NOTICE 'sm-check|barcode_mirror|true|codes principaux et registre cohérents';"
  echo "END \$\$;"
  # Invariants métier (restore-checks.sql), limités à l'entreprise par la RLS.
  echo "\\o $WORK/invariants.tsv"
  echo "\\i $HERE/restore-checks.sql"
  echo "\\o"
  echo "\\o $WORK/purge.tsv"
  echo "WITH d AS (DELETE FROM member_notifications WHERE created_at < now() - make_interval(days => $MEMBER_NOTIFICATION_RETENTION_DAYS) RETURNING 1) SELECT 'member_notifications', count(*) FROM d;"
  echo "WITH u AS (UPDATE restaurant_orders SET contact_phone = NULL WHERE contact_phone IS NOT NULL AND status IN ('CLOSED', 'CANCELLED', 'REJECTED') AND coalesce(closed_at, cancelled_at) < now() - make_interval(days => $CONTACT_RETENTION_DAYS) RETURNING 1) SELECT 'restaurant_orders_contact', count(*) FROM u;"
  echo "\\o"
  echo "\\set invariants \`awk -F'|' '\$2 != 0' $WORK/invariants.tsv | tr '\\n' ' '\`"
  echo "\\set inv_count \`grep -c . $WORK/invariants.tsv || true\`"
  echo "SELECT (:'invariants' = '' AND :'inv_count'::int = $(grep -c '^SELECT ' "$HERE/restore-checks.sql")) AS inv_ok \\gset"
  echo "\\if :inv_ok"
  echo "\\else"
  echo "DO \$\$ BEGIN RAISE EXCEPTION 'sm-check|business_invariants|false|écarts aux invariants'; END \$\$;"
  echo "\\endif"
  echo "INSERT INTO audit_logs (id, tenant_id, action, entity_type, entity_id, data, occurred_at)"
  echo "VALUES (gen_random_uuid(), '$TENANT', 'backup.tenant_restored', 'restore_operation', '$OPERATION',"
  echo "  jsonb_build_object('source_created_at', '$(meta created_at)', 'source', '$(meta source)', 'manifest_sha256', '$MSHA'), now());"
  echo "COMMIT;"
} >"$SQL"
T0="$(date -u +%s)"
code=0
sm_psql -F '|' -f "$SQL" >"$WORK/out.txt" 2>"$WORK/err.txt" || code=$?
T1="$(date -u +%s)"
# Rapport : contrôles (NOTICE / EXCEPTION « sm-check|nom|ok|détail »), lignes avant / après.
checks=""
add_check() { checks+="${checks:+, }{\"name\": $(sm_js "$1"), \"ok\": $2, \"detail\": $(sm_js "$3")}"; }
if (( REQUIRE_LOCK )) && (( code == 0 )); then add_check tenant_locked true "entreprise verrouillée pour l'opération $OPERATION (relu dans la transaction)"; fi
while IFS='|' read -r _ name ok detail; do
  [[ -n "$name" ]] && add_check "$name" "$ok" "$detail"
done < <(grep -o 'sm-check|[^|]*|[a-z]*|.*' "$WORK/err.txt" || true)
inv_detail="$(awk -F'|' '{printf "%s%s=%s", (NR>1?", ":""), $1, $2}' "$WORK/invariants.tsv" 2>/dev/null || true)"
if [[ -s "$WORK/invariants.tsv" ]]; then
  if (( code == 0 )); then add_check business_invariants true "$inv_detail"; else add_check business_invariants false "$inv_detail"; fi
fi
purge="$(awk -F'|' '{printf "%s%s=%s", (NR>1?", ":""), $1, $2}' "$WORK/purge.tsv" 2>/dev/null || true)"
before_rows="$(awk -F'|' '{s += $2} END {print s + 0}' "$WORK/before.tsv" 2>/dev/null || echo 0)"
after_rows=0; for t in "${ORDERED[@]}"; do after_rows=$(( after_rows + ROWS[$t] )); done
err=""
if (( code != 0 )); then
  err="$(grep -v 'sm-check|[^|]*|true|' "$WORK/err.txt" | grep -E 'ERROR|ERREUR|FATAL' | head -n 3 | tr '\n' ' ')"
  [[ -n "$err" ]] || err="$(tail -n 3 "$WORK/err.txt" | tr '\n' ' ')"
fi
{
  echo "{"
  echo "  \"applied\": $( (( code == 0 )) && echo true || echo false),"
  echo "  \"target\": $(sm_js "${CHOST:+copy}${CHOST:-production}"),"
  echo "  \"tables\": ${#ORDERED[@]},"
  echo "  \"rows_before\": $before_rows,"
  echo "  \"rows_restored\": $after_rows,"
  echo "  \"duration_seconds\": $(( T1 - T0 )),"
  echo "  \"purged\": $(sm_js "$purge"),"
  echo "  \"source_created_at\": $(sm_js "$(meta created_at)"),"
  echo "  \"manifest_sha256\": $(sm_js "$MSHA"),"
  echo "  \"error\": $(sm_js "$err"),"
  echo "  \"checks\": [$checks]"
  echo "}"
} >"$WORK/report.json"
if [[ -n "$REPORT" ]]; then cp "$WORK/report.json" "$REPORT"; fi
if (( code != 0 )); then
  sm_log "application à $TENANT REFUSÉE (ROLLBACK, rien n'est modifié) : $err"
  cat "$WORK/report.json"
  exit 1
fi
sm_log "application à $TENANT réussie (${#ORDERED[@]} tables, $after_rows lignes, $(( T1 - T0 )) s ; effacements : $purge)"
cat "$WORK/report.json"
