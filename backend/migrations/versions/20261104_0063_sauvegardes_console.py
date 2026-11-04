"""Sauvegardes pilotées par la console TechNova (R11, ADR-0058).

Tables de la PLATEFORME (aucune donnée d'entreprise) :

- ``backup_policies`` : planification (catégorie × fréquence ; physique, logique, vérification,
  test de restauration ; heure, jour, date de début, fuseau IANA, rétention) ; ``next_run_at``
  calculé par ``backup_policy_next_run`` — UNIQUE source de vérité du planificateur ;
- ``backup_runs`` : file d'attente + historique ; une seule exécution active par type, portée et
  entreprise (``uq_backup_runs_active``) ; nouvelles tentatives (``backup_finish``), reprise des
  exécutions abandonnées (``backup_reclaim_stale``) ;
- ``backup_items`` : sauvegarde logique d'UNE entreprise (métadonnées ; contenu sur disque) ;
- ``restore_operations`` : machine à états contrôlée par un déclencheur (transitions permises ET
  rôle autorisé pour chacune), deux administrateurs distincts (contrainte), une opération active
  par entreprise ;
- ``backup_agent_state`` : dernier passage de l'agent ;
- ``tenants.restore_locked_at`` / ``restore_operation_id`` : verrou pendant l'application.

Rôles (créés par ``ops/backup/sm-backup-role.sh``, ou ici, sans mot de passe, si la migration
est jouée par un superutilisateur), tous sans BYPASSRLS ni superutilisateur :

- console (``SM_DB_PLATFORM_ROLE``) : lecture, demandes, paramètres, approbations ;
- agent (``SM_DB_BACKUP_ROLE``, rôle de réplication de R11-0) : file d'attente et comptes rendus,
  verrou d'entreprise, liste des entreprises (identifiants seulement), journal de la plateforme ;
  TOUJOURS aucune donnée d'entreprise ;
- lecture d'une entreprise (``SM_DB_TENANT_BACKUP_ROLE``) : SELECT des tables d'entreprise
  sauvegardées, sous RLS (contexte ``app.tenant_id`` obligatoire) ;
- restauration d'une entreprise (``SM_DB_TENANT_RESTORE_ROLE``) : SELECT / INSERT / DELETE des
  tables restaurées sous RLS, mise à jour croissante des compteurs (``document_sequences``),
  ajout au journal de l'entreprise. Les droits définissent le périmètre : tables EXCLUES (D2) —
  abonnements, paiements d'abonnement, licences, postes, journaux, notifications d'échéance —
  sans droit d'écriture, jamais remplacées.

Descente refusée dès qu'une exécution ou une opération existe (historique jamais perdu).

Revision ID: 0063
Revises: 0062
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables d'entreprise JAMAIS remplacées par une restauration (arbitrage D2 et identité) :
EXCLUDED = (
    "subscriptions",
    "subscription_payments",
    "licenses",
    "license_activations",
    "platform_audit_logs",
    "audit_logs",
    "notifications",
    "notification_reads",
)
# Compteurs de numérotation : jamais en arrière (fusion au maximum).
MERGED = ("document_sequences",)

NIL = "'00000000-0000-0000-0000-000000000000'::uuid"


def _quote(role: str) -> str:
    return op.get_bind().dialect.identifier_preparer.quote(role)


def _ensure_roles(backup: str, reader: str, restorer: str) -> None:
    """Rôles requis, sans BYPASSRLS ni superutilisateur.

    Absents : créés ici si la migration est jouée par un superutilisateur (cas de l'image
    postgres : propriétaire = POSTGRES_USER), SANS mot de passe — connexion par la socket locale
    du conteneur seulement (l'agent y tourne) ; sinon refus explicite (les créer avant avec
    ops/backup/sm-backup-role.sh). Le rôle de l'agent est celui de R11-0 (réplication).
    """
    bind = op.get_bind()
    is_super = bind.execute(
        sa.text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    ).scalar_one()
    attrs = {
        backup: "LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS "
        "CONNECTION LIMIT 5",
        reader: "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION "
        "CONNECTION LIMIT 3",
        restorer: "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION "
        "CONNECTION LIMIT 2",
    }
    for role, role_attrs in attrs.items():
        row = bind.execute(
            sa.text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = :r"), {"r": role}
        ).first()
        if row is not None:
            if row[0]:
                raise RuntimeError(f"Rôle SQL « {role} » refusé : BYPASSRLS ou superutilisateur.")
            continue
        if not is_super:
            raise RuntimeError(
                f"Rôle SQL « {role} » absent : le créer avec ops/backup/sm-backup-role.sh "
                "(superutilisateur) avant la migration."
            )
        op.execute(f"CREATE ROLE {_quote(role)} {role_attrs}")
    db = bind.execute(sa.text("SELECT current_database()")).scalar_one()
    op.execute(
        f"GRANT CONNECT ON DATABASE {_quote(db)} "
        f"TO {_quote(backup)}, {_quote(reader)}, {_quote(restorer)}"
    )


def _tenant_tables() -> list[str]:
    """Tables d'ENTREPRISE : ``tenant_id`` ET RLS activée et forcée (les tables de la plateforme
    portant ``tenant_id`` — exécutions, journal de la plateforme — n'en sont pas)."""
    rows = op.get_bind().execute(
        sa.text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'tenant_id' "
            "AND NOT a.attisdropped WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND c.relrowsecurity AND c.relforcerowsecurity ORDER BY 1"
        )
    )
    return [r[0] for r in rows]


def upgrade() -> None:
    s = get_settings()
    _ensure_roles(s.db_backup_role, s.db_tenant_backup_role, s.db_tenant_restore_role)
    platform, agent = _quote(s.db_platform_role), _quote(s.db_backup_role)
    reader, restorer = _quote(s.db_tenant_backup_role), _quote(s.db_tenant_restore_role)

    op.add_column("tenants", sa.Column("restore_locked_at", sa.DateTime(timezone=True)))
    op.add_column("tenants", sa.Column("restore_operation_id", sa.Uuid()))

    op.create_table(
        "backup_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("run_time", sa.Time()),
        sa.Column("weekday", sa.SmallInteger()),
        sa.Column("month_day", sa.SmallInteger()),
        sa.Column("interval_hours", sa.SmallInteger()),
        sa.Column("start_date", sa.Date(), server_default=sa.text("CURRENT_DATE"), nullable=False),
        sa.Column("timezone", sa.String(64), server_default="UTC", nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Uuid()),
        sa.UniqueConstraint("category", "frequency", name="uq_backup_policies_category_frequency"),
        sa.CheckConstraint(
            "category IN ('physical', 'logical', 'verification', 'restore_test')",
            name="ck_backup_policies_category_valid",
        ),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly', 'automatic', 'manual')",
            name="ck_backup_policies_frequency_valid",
        ),
        sa.CheckConstraint(
            "weekday IS NULL OR weekday BETWEEN 1 AND 7", name="ck_backup_policies_weekday_valid"
        ),
        sa.CheckConstraint(
            "month_day IS NULL OR month_day BETWEEN 1 AND 31",
            name="ck_backup_policies_month_day_valid",
        ),
        sa.CheckConstraint(
            "interval_hours IS NULL OR interval_hours BETWEEN 1 AND 168",
            name="ck_backup_policies_interval_valid",
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650", name="ck_backup_policies_retention_valid"
        ),
        sa.CheckConstraint(
            "frequency NOT IN ('daily', 'weekly', 'monthly') OR run_time IS NOT NULL",
            name="ck_backup_policies_time_required",
        ),
        sa.CheckConstraint(
            "frequency <> 'weekly' OR weekday IS NOT NULL",
            name="ck_backup_policies_weekday_required",
        ),
        sa.CheckConstraint(
            "frequency <> 'monthly' OR month_day IS NOT NULL",
            name="ck_backup_policies_month_day_required",
        ),
        sa.CheckConstraint(
            "frequency <> 'automatic' OR interval_hours IS NOT NULL",
            name="ck_backup_policies_interval_required",
        ),
        sa.CheckConstraint(
            "frequency <> 'manual' OR NOT enabled", name="ck_backup_policies_manual_not_scheduled"
        ),
    )

    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT")),
        sa.Column("policy_id", sa.Uuid(), sa.ForeignKey("backup_policies.id", ondelete="RESTRICT")),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("attempt", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("max_attempts", sa.SmallInteger(), server_default="3", nullable=False),
        sa.Column("retry_of", sa.Uuid(), sa.ForeignKey("backup_runs.id", ondelete="RESTRICT")),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column(
            "not_before", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("requested_label", sa.String(254), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("claimed_by", sa.String(100)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("progress_percent", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("progress_message", sa.String(300)),
        sa.Column(
            "result", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("error", sa.Text()),
        sa.Column("restore_operation_id", sa.Uuid()),
        sa.UniqueConstraint("policy_id", "scheduled_for", name="uq_backup_runs_occurrence"),
        sa.CheckConstraint(
            "kind IN ('physical', 'logical', 'verification', 'restore_test')",
            name="ck_backup_runs_kind_valid",
        ),
        sa.CheckConstraint("scope IN ('platform', 'tenant')", name="ck_backup_runs_scope_valid"),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'manual', 'retry', 'pre_restore', 'restore_source')",
            name="ck_backup_runs_trigger_valid",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_backup_runs_status_valid",
        ),
        sa.CheckConstraint(
            "(scope = 'tenant') = (tenant_id IS NOT NULL)", name="ck_backup_runs_scope_tenant"
        ),
        sa.CheckConstraint(
            "kind = 'logical' OR scope = 'platform'", name="ck_backup_runs_tenant_scope_logical"
        ),
        sa.CheckConstraint(
            "progress_percent BETWEEN 0 AND 100", name="ck_backup_runs_progress_valid"
        ),
        sa.CheckConstraint(
            "attempt BETWEEN 1 AND max_attempts", name="ck_backup_runs_attempt_valid"
        ),
    )
    op.create_index("ix_backup_runs_queue", "backup_runs", ["status", "not_before"])
    op.create_index("ix_backup_runs_tenant", "backup_runs", ["tenant_id", "requested_at"])
    op.create_index("ix_backup_runs_requested", "backup_runs", ["requested_at"])
    op.execute(
        "CREATE UNIQUE INDEX uq_backup_runs_active ON backup_runs "
        f"(kind, scope, coalesce(tenant_id, {NIL})) "
        "WHERE status IN ('queued', 'running')"
    )

    op.create_table(
        "backup_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("backup_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("status", sa.String(20), server_default="running", nullable=False),
        sa.Column("source", sa.String(10), server_default="live", nullable=False),
        sa.Column("source_point", sa.DateTime(timezone=True)),
        sa.Column("storage_ref", sa.String(300)),
        sa.Column("table_count", sa.Integer()),
        sa.Column("row_count", sa.BigInteger()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("alembic_revision", sa.String(32)),
        sa.Column("verified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("protected_until", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "tenant_id", name="uq_backup_items_run_tenant"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'expired')",
            name="ck_backup_items_status_valid",
        ),
        sa.CheckConstraint("source IN ('live', 'pitr')", name="ck_backup_items_source_valid"),
    )
    op.create_index("ix_backup_items_tenant", "backup_items", ["tenant_id", "created_at"])

    op.create_table(
        "restore_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope", sa.String(10), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT")),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column(
            "source_item_id", sa.Uuid(), sa.ForeignKey("backup_items.id", ondelete="RESTRICT")
        ),
        sa.Column("target_time", sa.DateTime(timezone=True)),
        sa.Column(
            "rollback_of", sa.Uuid(), sa.ForeignKey("restore_operations.id", ondelete="RESTRICT")
        ),
        sa.Column("status", sa.String(30), server_default="requested", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "requested_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requested_label", sa.String(254), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "verification",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("approved_label", sa.String(254)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approval_reason", sa.Text()),
        sa.Column("decision_expires_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("rollback_deadline", sa.DateTime(timezone=True)),
        sa.Column(
            "pre_restore_item_id", sa.Uuid(), sa.ForeignKey("backup_items.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "materialized_item_id", sa.Uuid(), sa.ForeignKey("backup_items.id", ondelete="RESTRICT")
        ),
        sa.Column("claimed_by", sa.String(100)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("progress_percent", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("progress_message", sa.String(300)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.CheckConstraint(
            "scope IN ('tenant', 'platform')", name="ck_restore_operations_scope_valid"
        ),
        sa.CheckConstraint("mode IN ('logical', 'pitr')", name="ck_restore_operations_mode_valid"),
        sa.CheckConstraint(
            "status IN ('requested', 'verifying', 'verified', 'verification_failed', 'approved', "
            "'applying', 'applied', 'apply_failed', 'rejected', 'cancelled', 'expired')",
            name="ck_restore_operations_status_valid",
        ),
        sa.CheckConstraint(
            "(scope = 'tenant') = (tenant_id IS NOT NULL)",
            name="ck_restore_operations_scope_tenant",
        ),
        sa.CheckConstraint(
            "scope = 'tenant' OR mode = 'pitr'", name="ck_restore_operations_platform_is_pitr"
        ),
        sa.CheckConstraint(
            "(mode = 'logical') = (source_item_id IS NOT NULL)",
            name="ck_restore_operations_logical_has_item",
        ),
        sa.CheckConstraint(
            "(mode = 'pitr') = (target_time IS NOT NULL)",
            name="ck_restore_operations_pitr_has_time",
        ),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> requested_by",
            name="ck_restore_operations_two_distinct_admins",
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) >= 5", name="ck_restore_operations_reason_required"
        ),
    )
    op.create_index(
        "ix_restore_operations_tenant", "restore_operations", ["tenant_id", "requested_at"]
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_restore_operations_active ON restore_operations "
        f"(coalesce(tenant_id, {NIL})) "
        "WHERE status IN ('requested', 'verifying', 'verified', 'approved', 'applying')"
    )
    op.create_foreign_key(
        "backup_runs_restore_operation_id_fkey",
        "backup_runs",
        "restore_operations",
        ["restore_operation_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "backup_agent_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent", sa.String(100), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_backup_agent_state_single_row"),
    )

    # --- Planificateur : prochaine exécution (fuseau IANA de la politique) ------------------
    op.execute("""
CREATE FUNCTION backup_policy_next_run(p backup_policies, after_ts timestamptz)
RETURNS timestamptz LANGUAGE plpgsql STABLE AS $$
DECLARE
  local_after timestamp := after_ts AT TIME ZONE p.timezone;
  d date;
  m date;
  cand timestamp;
  anchor timestamptz;
  step interval;
  n bigint;
  last_day int;
BEGIN
  IF NOT p.enabled OR p.frequency = 'manual' THEN
    RETURN NULL;
  END IF;
  IF p.frequency = 'automatic' THEN
    anchor := (p.start_date + coalesce(p.run_time, time '00:00')) AT TIME ZONE p.timezone;
    step := make_interval(hours => p.interval_hours);
    IF after_ts < anchor THEN
      RETURN anchor;
    END IF;
    n := floor(extract(epoch FROM after_ts - anchor) / extract(epoch FROM step))::bigint + 1;
    RETURN anchor + n * step;
  END IF;
  d := greatest(p.start_date, local_after::date);
  FOR i IN 0..800 LOOP
    IF p.frequency = 'monthly' THEN
      m := date_trunc('month', d)::date;
      last_day := extract(day FROM (m + interval '1 month' - interval '1 day'))::int;
      cand := (m + (least(p.month_day, last_day) - 1)) + p.run_time;
      IF cand::date >= p.start_date AND cand > local_after THEN
        RETURN cand AT TIME ZONE p.timezone;
      END IF;
      d := (m + interval '1 month')::date;
      CONTINUE;
    END IF;
    IF p.frequency = 'daily' OR extract(isodow FROM d) = p.weekday THEN
      cand := d + p.run_time;
      IF cand > local_after THEN
        RETURN cand AT TIME ZONE p.timezone;
      END IF;
    END IF;
    d := d + 1;
  END LOOP;
  RETURN NULL;
END $$""")

    # Mise en file des échéances atteintes (idempotent : une exécution par occurrence ; une
    # échéance manquée pendant un arrêt n'est exécutée qu'une fois ; aucune exécution
    # concurrente du même type : l'index partiel l'interdit et l'occurrence est alors sautée).
    op.execute("""
CREATE FUNCTION backup_enqueue_due(now_ts timestamptz DEFAULT now())
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
  p backup_policies;
  inserted integer := 0;
  c integer;
BEGIN
  FOR p IN SELECT * FROM backup_policies
           WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= now_ts
           ORDER BY next_run_at FOR UPDATE SKIP LOCKED LOOP
    INSERT INTO backup_runs (id, kind, scope, policy_id, trigger, scheduled_for, requested_label)
    VALUES (gen_random_uuid(), p.category, 'platform', p.id, 'schedule', p.next_run_at,
            'planificateur')
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS c = ROW_COUNT;
    inserted := inserted + c;
    UPDATE backup_policies
       SET next_run_at = backup_policy_next_run(p, now_ts), last_enqueued_at = now_ts
     WHERE id = p.id;
  END LOOP;
  RETURN inserted;
END $$""")

    op.execute("""
CREATE FUNCTION backup_claim(agent_name text, kinds text[])
RETURNS SETOF backup_runs LANGUAGE plpgsql AS $$
DECLARE
  r backup_runs;
BEGIN
  SELECT * INTO r FROM backup_runs
   WHERE status = 'queued' AND not_before <= now() AND kind = ANY(kinds)
   ORDER BY not_before, requested_at
   FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  UPDATE backup_runs
     SET status = 'running', claimed_by = agent_name, claimed_at = now(), started_at = now(),
         heartbeat_at = now(), progress_percent = 0, progress_message = 'démarrage'
   WHERE id = r.id
   RETURNING * INTO r;
  RETURN NEXT r;
END $$""")

    # Fin d'exécution : échec → nouvelle tentative différée (5, 10, 20 min…) jusqu'à
    # max_attempts, sauf pour les sauvegardes préalables à une restauration (l'opération
    # elle-même échoue alors, fermée).
    op.execute("""
CREATE FUNCTION backup_finish(run_id uuid, ok boolean, res jsonb, err text)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
  r backup_runs;
  retry uuid;
BEGIN
  UPDATE backup_runs
     SET status = CASE WHEN ok THEN 'succeeded' ELSE 'failed' END,
         finished_at = now(), heartbeat_at = now(),
         progress_percent = CASE WHEN ok THEN 100 ELSE progress_percent END,
         progress_message = CASE WHEN ok THEN 'terminée' ELSE 'échec' END,
         result = coalesce(res, '{}'::jsonb), error = err
   WHERE id = run_id AND status = 'running'
   RETURNING * INTO r;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'exécution % absente ou non en cours', run_id;
  END IF;
  IF NOT ok AND r.attempt < r.max_attempts
     AND r.trigger NOT IN ('pre_restore', 'restore_source') THEN
    INSERT INTO backup_runs (id, kind, scope, tenant_id, policy_id, trigger, attempt, max_attempts,
                             retry_of, not_before, requested_by, requested_label)
    VALUES (gen_random_uuid(), r.kind, r.scope, r.tenant_id, r.policy_id, 'retry', r.attempt + 1,
            r.max_attempts, r.id, now() + make_interval(mins => (5 * power(2, r.attempt - 1))::int),
            r.requested_by, r.requested_label)
    ON CONFLICT DO NOTHING
    RETURNING id INTO retry;
  END IF;
  RETURN retry;
END $$""")

    op.execute("""
CREATE FUNCTION backup_reclaim_stale(max_silence interval DEFAULT interval '2 hours')
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
  rid uuid;
  n integer := 0;
BEGIN
  FOR rid IN SELECT id FROM backup_runs
              WHERE status = 'running' AND heartbeat_at < now() - max_silence
              FOR UPDATE SKIP LOCKED LOOP
    PERFORM backup_finish(rid, false, '{}'::jsonb,
                          'interrompue : agent sans signe de vie (arrêt ou panne)');
    n := n + 1;
  END LOOP;
  RETURN n;
END $$""")

    # --- Transitions des restaurations : permises ET réservées au bon rôle ------------------
    op.execute(f"""
CREATE FUNCTION restore_operations_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  who text := current_user;
  pair text := OLD.status || '>' || NEW.status;
BEGIN
  IF NEW.requested_by <> OLD.requested_by OR NEW.reason <> OLD.reason OR NEW.scope <> OLD.scope
     OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.mode <> OLD.mode
     OR NEW.source_item_id IS DISTINCT FROM OLD.source_item_id
     OR NEW.target_time IS DISTINCT FROM OLD.target_time THEN
    RAISE EXCEPTION 'restauration % : demande immuable', OLD.id;
  END IF;
  IF NEW.status = OLD.status THEN
    RETURN NEW;
  END IF;
  IF who = {_literal(s.db_platform_role)} AND pair IN (
       'requested>cancelled', 'verified>approved', 'verified>rejected', 'verified>cancelled',
       'verification_failed>cancelled', 'approved>cancelled') THEN
    IF NEW.status = 'approved' AND (NEW.approved_by IS NULL OR NEW.approved_at IS NULL) THEN
      RAISE EXCEPTION 'approbation sans approbateur';
    END IF;
    RETURN NEW;
  END IF;
  IF who = {_literal(s.db_backup_role)} AND pair IN (
       'requested>verifying', 'verifying>verified', 'verifying>verification_failed',
       'approved>applying', 'applying>applied', 'applying>apply_failed',
       'requested>expired', 'verified>expired', 'approved>expired') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'restauration % : transition % refusée pour le rôle %', OLD.id, pair, who;
END $$""")
    op.execute(
        "CREATE TRIGGER restore_operations_guard BEFORE UPDATE ON restore_operations "
        "FOR EACH ROW EXECUTE FUNCTION restore_operations_guard()"
    )
    op.execute("""
CREATE FUNCTION backup_history_no_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'historique des sauvegardes : suppression interdite (%)', TG_TABLE_NAME;
END $$""")
    for table in ("backup_runs", "backup_items", "restore_operations"):
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION backup_history_no_delete()"
        )

    # --- Politiques par défaut (UTC) : complète hebdomadaire + logique quotidienne ----------
    op.execute("""
INSERT INTO backup_policies (id, category, frequency, enabled, run_time, weekday, month_day,
                             interval_hours, retention_days) VALUES
 (gen_random_uuid(), 'physical', 'daily',     false, '01:15', NULL, NULL, NULL, 30),
 (gen_random_uuid(), 'physical', 'weekly',    true,  '01:15', 7,    NULL, NULL, 30),
 (gen_random_uuid(), 'physical', 'monthly',   false, '01:15', NULL, 1,    NULL, 90),
 (gen_random_uuid(), 'physical', 'automatic', false, NULL,    NULL, NULL, 24,   30),
 (gen_random_uuid(), 'physical', 'manual',    false, NULL,    NULL, NULL, NULL, 30),
 (gen_random_uuid(), 'logical',  'daily',     true,  '02:45', NULL, NULL, NULL, 30),
 (gen_random_uuid(), 'logical',  'weekly',    false, '02:45', 7,    NULL, NULL, 60),
 (gen_random_uuid(), 'logical',  'monthly',   false, '02:45', NULL, 1,    NULL, 365),
 (gen_random_uuid(), 'logical',  'automatic', false, NULL,    NULL, NULL, 12,   7),
 (gen_random_uuid(), 'logical',  'manual',    false, NULL,    NULL, NULL, NULL, 30),
 (gen_random_uuid(), 'verification', 'daily', true,  '02:15', NULL, NULL, NULL, 30),
 (gen_random_uuid(), 'restore_test', 'monthly', true, '03:30', NULL, 1,   NULL, 90)""")
    op.execute("UPDATE backup_policies p SET next_run_at = backup_policy_next_run(p, now())")

    # --- Droits ------------------------------------------------------------------------------
    op.execute(f"GRANT USAGE ON SCHEMA public TO {agent}, {reader}, {restorer}")
    # Console : lecture, demandes, paramètres, décisions — jamais d'exécution.
    op.execute(
        "GRANT SELECT ON backup_policies, backup_runs, backup_items, restore_operations, "
        f"backup_agent_state TO {platform}"
    )
    op.execute(
        "GRANT UPDATE (enabled, run_time, weekday, month_day, interval_hours, start_date, "
        "timezone, "
        f"retention_days, next_run_at, updated_at, updated_by) ON backup_policies TO {platform}"
    )
    op.execute(f"GRANT INSERT ON backup_runs TO {platform}")
    op.execute(f"GRANT UPDATE (status, finished_at, error) ON backup_runs TO {platform}")
    op.execute(f"GRANT INSERT ON restore_operations TO {platform}")
    op.execute(
        "GRANT UPDATE (status, approved_by, approved_label, approved_at, approval_reason, "
        f"decision_expires_at, finished_at, error) ON restore_operations TO {platform}"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION backup_policy_next_run(backup_policies, timestamptz) "
        f"TO {platform}, {agent}"
    )
    # Agent : file d'attente, comptes rendus, verrou, journal — aucune donnée d'entreprise.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON backup_runs, backup_items TO {agent}")
    op.execute(f"GRANT SELECT ON backup_policies TO {agent}")
    op.execute(f"GRANT UPDATE (next_run_at, last_enqueued_at) ON backup_policies TO {agent}")
    op.execute(f"GRANT SELECT ON restore_operations TO {agent}")
    op.execute(
        "GRANT UPDATE (status, verified_at, verification, decision_expires_at, applied_at, "
        "rollback_deadline, pre_restore_item_id, materialized_item_id, claimed_by, heartbeat_at, "
        f"progress_percent, progress_message, finished_at, error) ON restore_operations TO {agent}"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON backup_agent_state TO {agent}")
    op.execute(f"GRANT INSERT ON platform_audit_logs TO {agent}")
    op.execute(
        f"GRANT SELECT (id, status, restore_locked_at, restore_operation_id) ON tenants TO {agent}"
    )
    op.execute(f"GRANT UPDATE (restore_locked_at, restore_operation_id) ON tenants TO {agent}")
    op.execute(
        f"CREATE POLICY backup_agent_tenants_read ON tenants FOR SELECT TO {agent} USING (true)"
    )
    op.execute(
        f"CREATE POLICY backup_agent_tenants_lock ON tenants FOR UPDATE TO {agent} "
        "USING (true) WITH CHECK (true)"
    )
    for fn in (
        "backup_enqueue_due(timestamptz)",
        "backup_claim(text, text[])",
        "backup_finish(uuid, boolean, jsonb, text)",
        "backup_reclaim_stale(interval)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {agent}")
    # Lecture / restauration d'UNE entreprise : sous RLS, périmètre fixé ici (D2).
    op.execute(f"GRANT EXECUTE ON FUNCTION app_current_tenant_id() TO {reader}, {restorer}")
    # Révision du schéma : une sauvegarde ne se restaure que dans le MÊME schéma (la console
    # refuse une demande sur une sauvegarde d'une autre révision).
    op.execute(f"GRANT SELECT ON alembic_version TO {platform}, {agent}, {reader}, {restorer}")
    tables = _tenant_tables()
    restored = [t for t in tables if t not in EXCLUDED and t not in MERGED]
    kept = [t for t in tables if t in EXCLUDED]
    op.execute(f"GRANT SELECT ON {', '.join(restored + list(MERGED))} TO {reader}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON {', '.join(restored)} TO {restorer}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {', '.join(MERGED)} TO {restorer}")
    # Tables conservées : lecture seule (contrôle des références vers les lignes restaurées,
    # ex. un abonnement rattaché à un site) ; journal : ajout de l'entrée de la restauration.
    op.execute(f"GRANT SELECT ON {', '.join(kept)} TO {restorer}")
    op.execute(f"GRANT INSERT ON audit_logs TO {restorer}")
    # Effacement des téléphones des commandes finies depuis plus de 30 jours (R7) : réappliqué
    # après une restauration (une sauvegarde ancienne ne les ressuscite jamais).
    op.execute(f"GRANT UPDATE (contact_phone) ON restaurant_orders TO {restorer}")
    # Verrou de l'entreprise relu DANS la transaction d'application (opération attendue).
    op.execute(f"GRANT SELECT (restore_locked_at, restore_operation_id) ON tenants TO {restorer}")
    # Politiques propres (certaines tables n'ont de politique que pour le rôle applicatif) :
    # MÊME prédicat que l'isolation des entreprises, contexte app.tenant_id obligatoire.
    for t in restored + list(MERGED):
        op.execute(
            f"CREATE POLICY backup_tenant_read ON {t} FOR SELECT TO {reader} "
            "USING (tenant_id = app_current_tenant_id())"
        )
    for t in restored + list(MERGED):
        op.execute(
            f"CREATE POLICY backup_tenant_restore ON {t} FOR ALL TO {restorer} "
            "USING (tenant_id = app_current_tenant_id()) "
            "WITH CHECK (tenant_id = app_current_tenant_id())"
        )
    for t in kept:
        op.execute(
            f"CREATE POLICY backup_tenant_restore ON {t} FOR SELECT TO {restorer} "
            "USING (tenant_id = app_current_tenant_id())"
        )
    # Parents hors entreprise des clés étrangères (contrôle des orphelins) : colonnes
    # référencées seulement ; comptes : identifiants seuls.
    parents = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT DISTINCT p.relname, a.attname, p.relrowsecurity FROM pg_constraint k "
                "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_class p ON p.oid = k.confrelid "
                "JOIN unnest(k.confkey) u(n) ON true "
                "JOIN pg_attribute a ON a.attrelid = p.oid AND a.attnum = u.n "
                "WHERE k.contype = 'f' AND c.relname = ANY(:t) AND NOT (p.relname = ANY(:t)) "
                "ORDER BY 1, 2"
            ),
            {"t": tables},
        )
        .all()
    )
    for parent, column, rls in parents:
        op.execute(f"GRANT SELECT ({_quote(column)}) ON {parent} TO {restorer}")
        if rls and parent != "tenants":
            op.execute(
                f"CREATE POLICY backup_restore_fk_check ON {parent} FOR SELECT TO {restorer} "
                "USING (true)"
            )
    # session_replication_role = replica (déclencheurs désactivés le temps de l'application,
    # vérifications explicites ensuite) : accordé si la migration est jouée par un
    # superutilisateur, sinon par sm-backup-role.sh (superutilisateur).
    op.execute(f"""
DO $$ BEGIN
  IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    EXECUTE 'GRANT SET ON PARAMETER session_replication_role TO {restorer}';
  END IF;
END $$""")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def downgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM backup_runs) + (SELECT count(*) FROM restore_operations)"
        )
    ).scalar_one()
    if used:
        raise RuntimeError(
            "Descente de 0063 refusée : l'historique des sauvegardes et des restaurations n'est "
            "jamais supprimé."
        )
    s = get_settings()
    agent, reader, restorer = (
        _quote(s.db_backup_role),
        _quote(s.db_tenant_backup_role),
        _quote(s.db_tenant_restore_role),
    )
    op.execute("DROP POLICY IF EXISTS backup_agent_tenants_lock ON tenants")
    op.execute("DROP POLICY IF EXISTS backup_agent_tenants_read ON tenants")
    for (table,) in bind.execute(
        sa.text(
            "SELECT DISTINCT tablename FROM pg_policies WHERE policyname IN "
            "('backup_tenant_read', 'backup_tenant_restore', 'backup_restore_fk_check')"
        )
    ).all():
        for policy in ("backup_tenant_read", "backup_tenant_restore", "backup_restore_fk_check"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {reader}, {restorer}")
    op.execute(f"REVOKE ALL ON tenants, platform_audit_logs FROM {agent}")
    for fn in (
        "backup_reclaim_stale(interval)",
        "backup_finish(uuid, boolean, jsonb, text)",
        "backup_claim(text, text[])",
        "backup_enqueue_due(timestamptz)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.execute("ALTER TABLE backup_runs DROP CONSTRAINT backup_runs_restore_operation_id_fkey")
    op.drop_table("restore_operations")
    op.drop_table("backup_items")
    op.drop_table("backup_runs")
    op.drop_table("backup_agent_state")
    op.execute("DROP FUNCTION IF EXISTS restore_operations_guard()")
    op.execute("DROP FUNCTION IF EXISTS backup_history_no_delete()")
    op.execute("DROP FUNCTION IF EXISTS backup_policy_next_run(backup_policies, timestamptz)")
    op.drop_table("backup_policies")
    op.drop_column("tenants", "restore_operation_id")
    op.drop_column("tenants", "restore_locked_at")
