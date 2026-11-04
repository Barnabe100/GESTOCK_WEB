#!/usr/bin/env bash
# Agent des sauvegardes (R11, ADR-0058) — UNIQUE exécutant des opérations demandées dans la
# console TechNova ou échues selon les paramètres de planification.
#
#   SM_BACKUP_DIR=/backups sm-backup-agent.sh [--once]
#
# Lancé chaque minute (minuterie systemd stockmanager-backup-agent.timer → sm-backup-host.sh
# agent), dans le conteneur db (utilisateur postgres, socket locale, rôle de réplication
# stockmanager_backup : file d'attente et comptes rendus seulement, AUCUNE donnée d'entreprise).
#
# Un seul agent à la fois (verrou « agent » non bloquant : une minute occupée est simplement
# sautée) ; les opérations lourdes partagent en plus le verrou « backup » de R11-0. Cycle :
#   1. signe de vie (backup_agent_state), reprise des exécutions abandonnées (backup_reclaim_stale :
#      échec + nouvelle tentative), restaurations interrompues marquées en échec (rien n'est
#      jamais appliqué à moitié : une transaction interrompue est annulée par PostgreSQL) ;
#   2. mise en file des échéances (backup_enqueue_due : les paramètres de la console PILOTENT
#      les tâches ; une échéance manquée pendant un arrêt n'est exécutée qu'une fois) ;
#   3. décisions expirées (24 h) ; restaurations APPROUVÉES appliquées ; restaurations
#      DEMANDÉES vérifiées sur une copie isolée ;
#   4. exécutions en file, une à la fois (physique, logique, vérification, test de restauration) ;
#      échec → nouvelle tentative différée (5, 10 min…, 3 essais au plus) par backup_finish ;
#   5. rétention des sauvegardes logiques (jamais la dernière d'une entreprise, jamais une
#      sauvegarde protégée ou utilisée par une restauration active).
# Chaque fin d'opération est journalisée dans platform_audit_logs (acteur « agent »).
set -euo pipefail
HERE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
. "$HERE/lib.sh"
ONCE=0
[[ "${1:-}" == --once ]] && ONCE=1
SM_ROOT="$(sm_root)"
SM_OP=agent
sm_log_init agent
sm_need psql gzip sha256sum
WAIT="${SM_BACKUP_LOCK_WAIT_SECONDS:-21600}"
BUDGET="${SM_AGENT_MAX_SECONDS:-3300}"
DECISION_HOURS="${SM_RESTORE_DECISION_HOURS:-24}"
ROLLBACK_HOURS="${SM_RESTORE_ROLLBACK_HOURS:-24}"
DRAIN="${SM_RESTORE_DRAIN_SECONDS:-5}"
STALE="${SM_AGENT_STALE_MINUTES:-120}"
AGENT="agent@$(hostname 2>/dev/null || echo db)"
UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

# Un seul agent : verrou non bloquant (minute occupée = sautée, sans erreur).
if command -v flock >/dev/null 2>&1; then
  exec 8>>"$SM_ROOT/locks/agent.lock"
  flock -x -n 8 || exit 0
else
  mkdir "$SM_ROOT/locks/agent.lock.d" 2>/dev/null || {
    pid="$(cat "$SM_ROOT/locks/agent.lock.d/pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then exit 0; fi
    rm -f "$SM_ROOT/locks/agent.lock.d/pid"
  }
  echo $$ >"$SM_ROOT/locks/agent.lock.d/pid"
  trap 'rm -rf "$SM_ROOT/locks/agent.lock.d"' EXIT
fi
sm_pg_env
export PGAPPNAME=stockmanager-backup-agent
q() { sm_psql -v ON_ERROR_STOP=1 "$@"; }
lit() { printf "'%s'" "${1//\'/\'\'}"; }    # littéral SQL
sjs() { sm_js "$1"; }

audit() { # action cible_type cible_id entreprise(ou vide) données_json
  local tenant=NULL data="${5:-}"
  [[ -n "${4:-}" ]] && tenant="$(lit "$4")::uuid"
  [[ -n "$data" ]] || data='{}'
  q -c "INSERT INTO platform_audit_logs (id, occurred_at, actor_label, action, target_type, target_id, tenant_id, before, after, data)
        VALUES (gen_random_uuid(), now(), $(lit "$AGENT"), $(lit "$1"), $(lit "$2"), $(lit "$3"),
                $tenant, '{}'::jsonb, '{}'::jsonb, $(lit "$data")::jsonb)" >/dev/null || true
}
heartbeat() {
  q -c "INSERT INTO backup_agent_state (id, last_seen_at, agent, last_error) VALUES (1, now(), $(lit "$AGENT"), NULL)
        ON CONFLICT (id) DO UPDATE SET last_seen_at = now(), agent = EXCLUDED.agent" >/dev/null
}
agent_error() { q -c "UPDATE backup_agent_state SET last_error = $(lit "${1:0:2000}") WHERE id = 1" >/dev/null || true; }

# Signe de vie d'une exécution / d'une restauration pendant une tâche longue.
HB_PID=""
hb_start() { # table id
  ( while sleep 30; do q -c "UPDATE $1 SET heartbeat_at = now() WHERE id = '$2'" >/dev/null 2>&1 || true
                        q -c "UPDATE backup_agent_state SET last_seen_at = now() WHERE id = 1" >/dev/null 2>&1 || true; done ) 8>&- &
  HB_PID=$!
}
hb_stop() { if [[ -n "$HB_PID" ]]; then kill "$HB_PID" 2>/dev/null || true; wait "$HB_PID" 2>/dev/null || true; HB_PID=""; fi; }
progress() { # table id pourcentage message
  q -c "UPDATE $1 SET progress_percent = $3, progress_message = $(lit "${4:0:300}"), heartbeat_at = now() WHERE id = '$2'" >/dev/null || true
}
status_json() { cat "$SM_ROOT/status/$1.json" 2>/dev/null || echo '{}'; }
status_msg() { sm_json_get "$SM_ROOT/status/$1.json" message 2>/dev/null || true; }
# Exécution d'un script enfant : verrou de l'agent NON hérité (une copie de base temporaire ne
# doit jamais retenir l'agent).
child() { env "$@" 8>&- 2>&1; }

retention_days() { # catégorie fréquence(ou vide = manuelle)
  q -c "SELECT retention_days FROM backup_policies WHERE category = $(lit "$1") AND frequency = $(lit "${2:-manual}")"
}

# --- Sauvegarde logique d'une entreprise ---------------------------------------------------------
# Crée l'élément, exporte, renseigne ; renvoie l'identifiant de l'élément (succès) ou échoue.
export_tenant() { # run_id tenant_id expire_jours protégé_heures(ou 0) source(live|pitr) [point]
  local run="$1" tenant="$2" days="$3" prot="$4" src="${5:-live}" point="${6:-}" item out art f
  item="$(q -c "INSERT INTO backup_items (id, run_id, tenant_id, status, source, source_point)
               VALUES (gen_random_uuid(), '$run', '$tenant', 'running', '$src', $( [[ -n "$point" ]] && echo "$(lit "$point")::timestamptz" || echo NULL)) RETURNING id")" || return 1
  item="$(head -n 1 <<<"$item")"
  [[ "$item" =~ $UUID_RE ]] || return 1
  if out="$(child SM_BACKUP_DIR="$SM_ROOT" "$HERE/sm-tenant-export.sh" --tenant "$tenant" --run-id "${item:0:8}")"; then
    art="$(grep '^ARTIFACT ' <<<"$out" | tail -n 1)"; art="${art#ARTIFACT }"
    IFS='|' read -r f ntables nrows nbytes msha rev created <<<"$art"
    q -c "UPDATE backup_items SET status = 'succeeded', storage_ref = $(lit "${f#"$SM_ROOT"/}"), table_count = $ntables,
            row_count = $nrows, size_bytes = $nbytes, manifest_sha256 = $(lit "$msha"), alembic_revision = $(lit "$rev"),
            source_point = coalesce(source_point, $(lit "$created")::timestamptz),
            verified = true, verified_at = now(), finished_at = now(),
            expires_at = now() + make_interval(days => $days),
            protected_until = CASE WHEN $prot > 0 THEN now() + make_interval(hours => $prot) END
          WHERE id = '$item'" >/dev/null
    echo "$item"
    return 0
  fi
  q -c "UPDATE backup_items SET status = 'failed', finished_at = now(), error = $(lit "$(tail -n 3 <<<"$out" | tr '\n' ' ' | cut -c1-2000)") WHERE id = '$item'" >/dev/null || true
  sm_log "export de l'entreprise $tenant en échec : $(tail -n 1 <<<"$out")"
  return 1
}

# --- Exécutions en file ---------------------------------------------------------------------------
finish_run() { # run ok(true|false) résultat_json erreur
  local retry
  retry="$(q -c "SELECT backup_finish('$1', $2, $(lit "$3")::jsonb, $( [[ -n "$4" ]] && lit "${4:0:4000}" || echo NULL))")"
  audit "backup.run_$( [[ "$2" == true ]] && echo succeeded || echo failed)" backup_run "$1" "${RUN_TENANT:-}" \
    "{\"kind\": $(sjs "$RUN_KIND"), \"scope\": $(sjs "$RUN_SCOPE"), \"retry\": $(sjs "$retry")}"
}

run_logical() { # run policy scope tenant
  local run="$1" policy="$2" scope="$3" tenant="$4" days tenants=() i=0 n ok=0 failed=0 errs=""
  if [[ -n "$policy" ]]; then days="$(q -c "SELECT retention_days FROM backup_policies WHERE id = '$policy'")"; else days="$(retention_days logical)"; fi
  if [[ "$scope" == tenant ]]; then tenants=("$tenant"); else
    mapfile -t tenants < <(q -c "SELECT id FROM tenants ORDER BY id")
  fi
  n=${#tenants[@]}
  for t in "${tenants[@]}"; do
    i=$(( i + 1 ))
    progress backup_runs "$run" $(( (i - 1) * 100 / (n > 0 ? n : 1) )) "entreprise $i/$n"
    if export_tenant "$run" "$t" "$days" 0 live >/dev/null; then ok=$(( ok + 1 )); else failed=$(( failed + 1 )); errs+="$t "; fi
  done
  local res="{\"tenants\": $n, \"succeeded\": $ok, \"failed\": $failed, \"retention_days\": $days}"
  if (( failed == 0 )); then finish_run "$run" true "$res" ""; else finish_run "$run" false "$res" "$failed entreprise(s) en échec : $errs"; fi
}

run_physical() { # run
  local run="$1" out code=0 ret pout pcode=0
  progress backup_runs "$run" 5 "sauvegarde physique complète (pg_basebackup)"
  out="$(child SM_BACKUP_DIR="$SM_ROOT" "$HERE/sm-base-backup.sh" --wait "$WAIT")" || code=$?
  if (( code != 0 )); then finish_run "$run" false "$(status_json base_backup)" "$(status_msg base_backup)"; return; fi
  # Rétention physique : la plus longue des politiques physiques actives (ou de la manuelle).
  ret="$(q -c "SELECT max(retention_days) FROM backup_policies WHERE category = 'physical' AND (enabled OR frequency = 'manual')")"
  progress backup_runs "$run" 90 "rétention (${ret} jours)"
  pout="$(child SM_BACKUP_DIR="$SM_ROOT" SM_BACKUP_RETENTION_DAYS="$ret" "$HERE/sm-backup-prune.sh" --apply --wait "$WAIT")" || pcode=$?
  local res
  res="{\"backup\": $(status_json base_backup), \"retention_days\": $ret, \"prune_ok\": $( (( pcode == 0 )) && echo true || echo false), \"prune\": $(sjs "$(status_msg prune)")}"
  (( pcode == 0 )) || agent_error "rétention physique en échec : $(tail -n 1 <<<"$pout")"
  finish_run "$run" true "$res" ""
}

run_script() { # run statut_op script args…
  local run="$1" op="$2" code=0; shift 2
  progress backup_runs "$run" 5 "$op"
  child SM_BACKUP_DIR="$SM_ROOT" "$@" >/dev/null || code=$?
  if (( code == 0 )); then finish_run "$run" true "$(status_json "$op")" ""; else finish_run "$run" false "$(status_json "$op")" "$(status_msg "$op")"; fi
}

process_run() { # ligne réservée par backup_claim
  local row="$1" run policy
  IFS=$'\t' read -r run RUN_KIND RUN_SCOPE RUN_TENANT policy <<<"$row"
  sm_log "exécution $run : $RUN_KIND ($RUN_SCOPE${RUN_TENANT:+ $RUN_TENANT})"
  hb_start backup_runs "$run"
  case "$RUN_KIND" in
    logical) run_logical "$run" "$policy" "$RUN_SCOPE" "$RUN_TENANT" ;;
    physical) run_physical "$run" ;;
    verification)
      if [[ "$(date -u +%u)" == "${SM_BACKUP_VERIFY_FULL_WEEKDAY:-7}" ]]; then
        run_script "$run" verify "$HERE/sm-backup-verify.sh" --wait "$WAIT" --full
      else
        run_script "$run" verify "$HERE/sm-backup-verify.sh" --wait "$WAIT"
      fi ;;
    restore_test) run_script "$run" restore_test "$HERE/sm-restore-test.sh" --wait "$WAIT" ;;
  esac
  hb_stop
}

# --- Restaurations ----------------------------------------------------------------------------------
op_set() { # opération affectations_SQL
  q -c "UPDATE restore_operations SET $2 WHERE id = '$1'" >/dev/null
}
item_path() { # élément → chemin absolu de l'archive (contrôlé)
  local ref
  ref="$(q -c "SELECT storage_ref FROM backup_items WHERE id = '$1' AND status = 'succeeded'")"
  [[ -n "$ref" && "$ref" =~ ^logical/[0-9a-f-]{36}/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$ ]] || return 1
  [[ -d "$SM_ROOT/$ref" && -f "$SM_ROOT/$ref/.complete" ]] || return 1
  echo "$SM_ROOT/$ref"
}
# Exécution technique rattachée à une restauration (sauvegarde préalable, matérialisation PITR).
op_run() { # opération entreprise déclencheur
  q -c "INSERT INTO backup_runs (id, kind, scope, tenant_id, trigger, status, max_attempts, attempt, requested_label,
          claimed_by, claimed_at, started_at, heartbeat_at, restore_operation_id)
        VALUES (gen_random_uuid(), 'logical', 'tenant', '$2', '$3', 'running', 1, 1, $(lit "$AGENT"),
          $(lit "$AGENT"), now(), now(), now(), '$1') RETURNING id" | head -n 1
}
verify_copy() { # args de sm-restore-test.sh → 0/1 ; rapport dans status/restore_verify.json
  child SM_BACKUP_DIR="$SM_ROOT" "$HERE/sm-restore-test.sh" --op restore_verify --wait "$WAIT" "$@" >/dev/null
}

verify_operation() { # opération
  local op="$1" row scope tenant mode item target art code=0 report mat="" run
  row="$(q -F $'\t' -c "SELECT scope, coalesce(tenant_id::text, ''), mode, coalesce(source_item_id::text, ''),
                         coalesce(to_char(target_time AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || 'Z', '')
                    FROM restore_operations WHERE id = '$op'")"
  IFS=$'\t' read -r scope tenant mode item target <<<"$row"
  op_set "$op" "status = 'verifying', claimed_by = $(lit "$AGENT"), heartbeat_at = now(), progress_percent = 5, progress_message = 'copie isolée'"
  sm_log "restauration $op : vérification ($scope, $mode)"
  hb_start restore_operations "$op"
  # Commit récent puis bascule du segment : un instant demandé dans le passé est toujours suivi
  # d'une transaction archivée (la récupération à un instant s'arrête sur un commit).
  heartbeat
  if [[ "$scope" == platform ]]; then
    verify_copy --target-time "$target" || code=$?
    report="{\"copy\": $(status_json restore_verify)}"
  elif [[ "$mode" == pitr ]]; then
    progress restore_operations "$op" 10 "copie à l'instant demandé et export de l'entreprise"
    run="$(op_run "$op" "$tenant" restore_source)"
    if verify_copy --target-time "$target" --tenant-export "$tenant" --export-run-id "${run:0:8}"; then
      art="$(sed -n 's/^  "artifact": "\(.*\)",\?$/\1/p' "$SM_ROOT/status/restore_verify.json" | head -n 1)"
      local f ntables nrows nbytes msha rev created
      IFS='|' read -r f ntables nrows nbytes msha rev created <<<"$art"
      if [[ -d "$f" ]]; then
        mat="$(q -c "INSERT INTO backup_items (id, run_id, tenant_id, status, source, source_point, storage_ref, table_count, row_count,
                       size_bytes, manifest_sha256, alembic_revision, verified, verified_at, finished_at, expires_at)
                     VALUES (gen_random_uuid(), '$run', '$tenant', 'succeeded', 'pitr', $(lit "$target")::timestamptz,
                       $(lit "${f#"$SM_ROOT"/}"), $ntables, $nrows, $nbytes, $(lit "$msha"), $(lit "$rev"), true, now(), now(),
                       now() + make_interval(days => $(retention_days logical))) RETURNING id" | head -n 1)"
        q -c "SELECT backup_finish('$run', true, '{}'::jsonb, NULL)" >/dev/null
      else
        q -c "SELECT backup_finish('$run', false, '{}'::jsonb, 'export de l''instant demandé absent')" >/dev/null
      fi
    else
      q -c "SELECT backup_finish('$run', false, '{}'::jsonb, 'copie à l''instant demandé en échec')" >/dev/null
    fi
    report="{\"materialization\": $(status_json restore_verify)"
    if [[ -n "$mat" ]]; then
      op_set "$op" "materialized_item_id = '$mat'"
      progress restore_operations "$op" 55 "épreuve de la restauration sur une copie de l'état actuel"
      verify_copy --tenant-apply "$tenant" --artifact "$(item_path "$mat")" --operation "$op" || code=$?
      report+=", \"copy\": $(status_json restore_verify)}"
    else
      code=1; report+="}"
    fi
  else
    if art="$(item_path "$item")"; then
      verify_copy --tenant-apply "$tenant" --artifact "$art" --operation "$op" || code=$?
      report="{\"copy\": $(status_json restore_verify)}"
    else
      code=1; report="{\"error\": \"sauvegarde source absente, expirée ou incomplète\"}"
    fi
  fi
  hb_stop
  if (( code == 0 )); then
    op_set "$op" "status = 'verified', verified_at = now(), verification = $(lit "$report")::jsonb, progress_percent = 100,
            progress_message = 'vérifiée : en attente d''approbation', decision_expires_at = now() + make_interval(hours => $DECISION_HOURS)"
    audit restore.verified restore_operation "$op" "$tenant" "{\"scope\": $(sjs "$scope"), \"mode\": $(sjs "$mode")}"
  else
    op_set "$op" "status = 'verification_failed', verified_at = now(), verification = $(lit "$report")::jsonb, finished_at = now(),
            progress_message = 'vérification en échec', error = 'contrôles en échec sur la copie isolée (voir le rapport)'"
    audit restore.verification_failed restore_operation "$op" "$tenant" "{\"scope\": $(sjs "$scope"), \"mode\": $(sjs "$mode")}"
  fi
}

unlock_tenant() { # entreprise opération
  q -c "UPDATE tenants SET restore_locked_at = NULL, restore_operation_id = NULL WHERE id = '$1' AND restore_operation_id = '$2'" >/dev/null || true
}
apply_operation() { # opération (entreprise, approuvée, non expirée)
  local op="$1" tenant item art pre run report code=0 out
  tenant="$(q -c "SELECT tenant_id FROM restore_operations WHERE id = '$op'")"
  item="$(q -c "SELECT coalesce(materialized_item_id, source_item_id) FROM restore_operations WHERE id = '$op'")"
  op_set "$op" "status = 'applying', claimed_by = $(lit "$AGENT"), heartbeat_at = now(), progress_percent = 5, progress_message = 'verrou de l''entreprise'"
  sm_log "restauration $op : APPLICATION à l'entreprise $tenant"
  hb_start restore_operations "$op"
  fail_apply() {
    hb_stop
    unlock_tenant "$tenant" "$op"
    op_set "$op" "status = 'apply_failed', finished_at = now(), error = $(lit "${1:0:2000}"), progress_message = 'échec : rien n''a été modifié'"
    audit restore.apply_failed restore_operation "$op" "$tenant" "{\"error\": $(sjs "${1:0:500}")}"
    sm_log "restauration $op : ÉCHEC — $1"
  }
  art="$(item_path "$item")" || { fail_apply "sauvegarde source absente ou incomplète"; return; }
  # Verrou : FOR UPDATE attend la fin des requêtes en cours de l'entreprise (FOR KEY SHARE) ;
  # les suivantes lisent le verrou et sont refusées (503). Les autres entreprises continuent.
  if ! q -c "BEGIN; SET LOCAL lock_timeout = '${SM_RESTORE_LOCK_WAIT:-120s}';
             SELECT 1 FROM tenants WHERE id = '$tenant' FOR UPDATE;
             UPDATE tenants SET restore_locked_at = now(), restore_operation_id = '$op' WHERE id = '$tenant' AND restore_locked_at IS NULL;
             COMMIT;" >/dev/null 2>&1; then
    fail_apply "verrou de l'entreprise impossible (requêtes en cours trop longues) : réessayer"; return
  fi
  [[ "$(q -c "SELECT restore_operation_id FROM tenants WHERE id = '$tenant'")" == "$op" ]] || { fail_apply "entreprise déjà verrouillée par une autre opération"; return; }
  sleep "$DRAIN"
  progress restore_operations "$op" 20 "sauvegarde préalable de l'état actuel"
  run="$(op_run "$op" "$tenant" pre_restore)"
  if ! pre="$(export_tenant "$run" "$tenant" "$(( (ROLLBACK_HOURS + 23) / 24 + 1 ))" "$ROLLBACK_HOURS" live)"; then
    q -c "SELECT backup_finish('$run', false, '{}'::jsonb, 'sauvegarde préalable en échec')" >/dev/null
    fail_apply "sauvegarde préalable en échec : restauration refusée"; return
  fi
  q -c "SELECT backup_finish('$run', true, '{}'::jsonb, NULL)" >/dev/null
  op_set "$op" "pre_restore_item_id = '$pre'"
  progress restore_operations "$op" 60 "application (transaction unique)"
  report="$SM_ROOT/state/apply-$op.json"
  out="$(child SM_BACKUP_DIR="$SM_ROOT" "$HERE/sm-tenant-apply.sh" --tenant "$tenant" --artifact "$art" --operation "$op" --require-lock --report "$report")" || code=$?
  local rep='{}'
  [[ -f "$report" ]] && rep="$(cat "$report")"
  if (( code != 0 )); then
    op_set "$op" "verification = verification || jsonb_build_object('application', $(lit "$rep")::jsonb)"
    fail_apply "application refusée (ROLLBACK) : $(sed -n 's/^  "error": "\(.*\)",$/\1/p' "$report" 2>/dev/null | head -c 1500)"; return
  fi
  hb_stop
  unlock_tenant "$tenant" "$op"
  op_set "$op" "status = 'applied', applied_at = now(), finished_at = now(), progress_percent = 100, progress_message = 'appliquée',
          rollback_deadline = now() + make_interval(hours => $ROLLBACK_HOURS),
          verification = verification || jsonb_build_object('application', $(lit "$rep")::jsonb)"
  audit restore.applied restore_operation "$op" "$tenant" "{\"pre_restore_item_id\": $(sjs "$pre")}"
  sm_log "restauration $op : appliquée ; retour arrière possible ${ROLLBACK_HOURS} h (sauvegarde préalable $pre)"
}

# --- Rétention des sauvegardes logiques -----------------------------------------------------------
prune_logical() {
  local id ref real n=0
  while IFS=$'\t' read -r id ref; do
    [[ "$id" =~ $UUID_RE && "$ref" =~ ^logical/([0-9a-f-]{36})/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$ ]] || continue
    real="$(realpath "$SM_ROOT/$ref" 2>/dev/null || true)"
    if [[ -n "$real" ]]; then
      [[ "$real" == "$SM_ROOT/$ref" && -d "$real" && ! -L "$real" && -f "$real/manifest.tsv" ]] || { sm_log "rétention : $ref refusée (chemin non conforme)"; continue; }
      chmod -R u+w "$real" && rm -rf -- "$real"
      [[ ! -e "$real" ]] || { sm_log "rétention : suppression incomplète de $ref"; continue; }
    fi
    q -c "UPDATE backup_items SET status = 'expired' WHERE id = '$id'" >/dev/null
    n=$(( n + 1 ))
  done < <(q -F $'\t' -c "
    SELECT i.id, i.storage_ref FROM backup_items i
     WHERE i.status = 'succeeded' AND i.expires_at < now() AND coalesce(i.protected_until, '-infinity') < now()
       AND EXISTS (SELECT 1 FROM backup_items j WHERE j.tenant_id = i.tenant_id AND j.status = 'succeeded'
                     AND j.source = 'live' AND j.created_at > i.created_at)
       AND NOT EXISTS (SELECT 1 FROM restore_operations o WHERE o.status IN ('requested', 'verifying', 'verified', 'approved', 'applying')
                         AND i.id IN (o.source_item_id, o.materialized_item_id, o.pre_restore_item_id))
       AND NOT EXISTS (SELECT 1 FROM restore_operations o WHERE o.status = 'applied' AND o.rollback_deadline > now()
                         AND i.id = o.pre_restore_item_id)
     ORDER BY i.created_at LIMIT 500")
  if (( n > 0 )); then audit backup.logical_pruned backup_items - "" "{\"expired\": $n}"; sm_log "rétention logique : $n sauvegarde(s) expirée(s) supprimée(s)"; fi
}

# --- Cycle ------------------------------------------------------------------------------------------
heartbeat || { sm_log "base injoignable avec le rôle $PGUSER"; exit 1; }
T0="$(date -u +%s)"
stale="$(q -c "SELECT backup_reclaim_stale(make_interval(mins => $STALE))")"
[[ "$stale" == 0 ]] || sm_log "$stale exécution(s) abandonnée(s) reprise(s)"
# Restaurations interrompues (agent arrêté) : vérification à refaire / application annulée par
# PostgreSQL (transaction unique), entreprise déverrouillée.
while IFS=$'\t' read -r op st tenant; do
  [[ "$op" =~ $UUID_RE ]] || continue
  if [[ "$st" == applying ]]; then
    unlock_tenant "$tenant" "$op"
    op_set "$op" "status = 'apply_failed', finished_at = now(), error = 'interrompue (arrêt de l''agent) : aucune modification validée'"
  else
    op_set "$op" "status = 'verification_failed', finished_at = now(), error = 'vérification interrompue (arrêt de l''agent)'"
  fi
  audit restore.interrupted restore_operation "$op" "$tenant" "{\"status\": $(sjs "$st")}"
done < <(q -F $'\t' -c "SELECT id, status, coalesce(tenant_id::text, '') FROM restore_operations
                         WHERE status IN ('verifying', 'applying') AND heartbeat_at < now() - make_interval(mins => $STALE)")
due="$(q -c "SELECT backup_enqueue_due(now())")"
[[ "$due" == 0 ]] || sm_log "$due exécution(s) planifiée(s) mise(s) en file"

# Chaque opération s'exécute dans un sous-shell (set -e effectif, signe de vie arrêté à la
# sortie) ; une erreur imprévue ne laisse jamais une opération « en cours » : elle est marquée en
# échec (restauration : entreprise déverrouillée ; aucune modification n'a été validée).
crashed() { # type identifiant code
  sm_log "erreur interne de l'agent ($1 $2, code $3)"
  agent_error "erreur interne de l'agent ($1 $2, code $3)"
  if [[ "$1" == restore ]]; then
    local tenant
    tenant="$(q -c "SELECT coalesce(tenant_id::text, '') FROM restore_operations WHERE id = '$2'")" || true
    [[ -z "$tenant" ]] || unlock_tenant "$tenant" "$2"
    q -c "UPDATE restore_operations SET finished_at = now(), error = 'erreur interne de l''agent (code $3) : aucune modification validée',
            status = CASE status WHEN 'verifying' THEN 'verification_failed' ELSE 'apply_failed' END
          WHERE id = '$2' AND status IN ('verifying', 'applying')" >/dev/null || true
  else
    q -c "SELECT backup_finish(id, false, '{}'::jsonb, 'erreur interne de l''agent (code $3)') FROM backup_runs
          WHERE id = '$2' AND status = 'running'" >/dev/null || true
  fi
}
guarded() { # type identifiant fonction arguments…
  local code
  set +e
  ( set -e; trap hb_stop EXIT; "${@:3}" )
  code=$?
  set -e
  if (( code != 0 )); then crashed "$1" "$2" "$code"; fi
}

while :; do
  heartbeat
  # Décisions non prises à temps : expirées (une vérification ancienne ne vaut plus approbation).
  q -c "UPDATE restore_operations SET status = 'expired', finished_at = now(), progress_message = 'décision expirée'
        WHERE status IN ('verified', 'approved') AND decision_expires_at < now()" >/dev/null
  op="$(q -c "SELECT id FROM restore_operations WHERE status = 'approved' AND scope = 'tenant' ORDER BY approved_at LIMIT 1")"
  if [[ -n "$op" ]]; then
    guarded restore "$op" apply_operation "$op"
  else
    op="$(q -c "SELECT id FROM restore_operations WHERE status = 'requested' ORDER BY requested_at LIMIT 1")"
    if [[ -n "$op" ]]; then
      guarded restore "$op" verify_operation "$op"
    else
      row="$(q -F $'\t' -c "SELECT id, kind, scope, coalesce(tenant_id::text, ''), coalesce(policy_id::text, '')
                             FROM backup_claim($(lit "$AGENT"), ARRAY['physical', 'logical', 'verification', 'restore_test'])")"
      [[ -n "$row" ]] || break
      guarded run "${row%%$'\t'*}" process_run "$row"
    fi
  fi
  (( ONCE == 0 )) || break
  (( $(date -u +%s) - T0 < BUDGET )) || { sm_log "budget de temps atteint : suite au prochain passage"; break; }
done
set +e
( set -e; prune_logical )
(( $? == 0 )) || agent_error "rétention logique en échec"
set -e
heartbeat
