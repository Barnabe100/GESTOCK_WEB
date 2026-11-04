import { Card } from 'primereact/card';
import { Message } from 'primereact/message';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import { useBackupStatus } from '../queries';
import type { BackupLevel, BackupOperation, BackupStatus } from '../types';

const TONES = { ok: 'success', warning: 'warning', critical: 'danger' } as const;
const ICONS = {
  ok: 'pi pi-check',
  warning: 'pi pi-exclamation-circle',
  critical: 'pi pi-exclamation-triangle',
} as const;
const OPERATIONS = ['base_backup', 'verify', 'restore_test', 'prune'] as const;

/**
 * Sauvegardes (lecture seule, R11-0) : niveaux CALCULÉS PAR LE SERVEUR à partir de l'archivage
 * lu en direct et des statuts écrits par les scripts d'exploitation (aucun contenu de
 * sauvegarde). Aucune action : sauvegarder et restaurer restent des opérations d'exploitation
 * documentées et tracées.
 */
export function BackupsPage() {
  const { t } = useTranslation();
  const status = useBackupStatus();
  if (status.isLoading) return <LoadingState />;
  if (status.isError || !status.data) {
    return <ErrorMessage error={status.error} onRetry={() => void status.refetch()} />;
  }
  const data = status.data;
  return (
    <>
      <PageHeader title={t('console:backups.title')} description={t('console:backups.subtitle')} />
      <p data-testid="backups-overall">
        <LevelBadge level={data.level} /> {t(`console:backups.overall.${data.level}`)}
      </p>
      {!data.status_configured && (
        <Message severity="error" text={t('console:backups.statusNotConfigured')} />
      )}
      <div className="sm-dashboard-grid">
        <ArchivingCard data={data} />
        <OperationsCard data={data} />
        <StorageCard data={data} />
        <OffsiteCard data={data} />
        <SupervisionCard data={data} />
      </div>
      <p className="sm-help">{t('console:backups.procedure')}</p>
    </>
  );
}

function LevelBadge({ level }: { level: BackupLevel }) {
  const { t } = useTranslation();
  return (
    <StatusBadge
      tone={TONES[level]}
      icon={ICONS[level]}
      label={t(`console:backups.level.${level}`)}
    />
  );
}

function useFormat() {
  const { t } = useTranslation();
  const when = (iso: string | null) =>
    iso ? formatDateTime(iso, 'fr') : t('console:backups.never');
  const duration = (seconds: number | null) => {
    if (seconds === null) return '—';
    if (seconds < 120) return t('console:backups.seconds', { count: seconds });
    if (seconds < 7200) return t('console:backups.minutes', { count: Math.round(seconds / 60) });
    if (seconds < 172800) return t('console:backups.hours', { count: Math.round(seconds / 3600) });
    return t('console:backups.days', { count: Math.round(seconds / 86400) });
  };
  const bytes = (value: number | null) =>
    value === null ? '—' : t('console:backups.mebibytes', { value: (value / 1048576).toFixed(1) });
  return {
    when,
    duration,
    bytes,
    ago: (s: number | null) => t('console:backups.age', { value: duration(s) }),
  };
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function ArchivingCard({ data }: { data: BackupStatus }) {
  const { t } = useTranslation();
  const f = useFormat();
  const a = data.archiving;
  return (
    <Card title={t('console:backups.archiving')} subTitle={<LevelBadge level={a.level} />}>
      <p data-testid="backups-archiving-state">
        <strong>{t(`console:backups.state.${a.state}`, { count: a.pending_segments ?? 0 })}</strong>
      </p>
      <dl className="sm-details" data-testid="backups-archiving">
        <Row label={t('console:backups.mode')}>{a.archive_mode ?? '—'}</Row>
        <Row label={t('console:backups.timeout')}>{f.duration(a.archive_timeout_seconds)}</Row>
        <Row label={t('console:backups.lastArchived')}>
          {f.when(a.last_archived_at)}
          {a.last_archived_age_seconds !== null && ` (${f.ago(a.last_archived_age_seconds)})`}
        </Row>
        <Row label={t('console:backups.pending')}>{a.pending_segments ?? '—'}</Row>
        <Row label={t('console:backups.failed')}>
          {a.failed_count}
          {a.last_failed_at &&
            ` — ${t('console:backups.lastFailed')} : ${f.when(a.last_failed_at)}${
              a.failure_unresolved ? ` (${t('console:backups.unresolved')})` : ''
            }`}
        </Row>
      </dl>
      {a.messages.map((m) => (
        <Message key={m} severity="error" text={t(`console:backups.messages.${m}`)} />
      ))}
      {!a.data_checksums && <Message severity="info" text={t('console:backups.checksumsOff')} />}
      <p className="sm-help">
        {t('console:backups.rpo', { minutes: Math.round(data.rpo_seconds / 60) })}
      </p>
    </Card>
  );
}

function OperationsCard({ data }: { data: BackupStatus }) {
  const { t } = useTranslation();
  return (
    <Card title={t('console:backups.operationsTitle')}>
      <dl className="sm-details" data-testid="backups-operations">
        {OPERATIONS.map((op) => (
          <OperationRow key={op} op={data.operations[op]} />
        ))}
      </dl>
    </Card>
  );
}

function OperationRow({ op }: { op: BackupOperation }) {
  const { t } = useTranslation();
  const f = useFormat();
  const passed = op.checks.filter((c) => c.ok).length;
  return (
    <div data-testid={`backups-op-${op.operation}`}>
      <dt>
        {t(`console:backups.operations.${op.operation}`)} <LevelBadge level={op.level} />
      </dt>
      <dd>
        {op.last_success_at
          ? t('console:backups.lastSuccess', {
              when: f.when(op.last_success_at),
              age: f.duration(op.last_success_age_seconds),
            })
          : t('console:backups.noSuccess')}
        {' — '}
        {t('console:backups.maxAge', { value: f.duration(op.max_age_seconds) })}
        {op.problem && (
          <>
            <br />
            {t(`console:backups.problem.${op.problem}`)}
            {op.last_message && ` : ${op.last_message}`}
          </>
        )}
        {op.operation === 'restore_test' && op.checks.length > 0 && (
          <>
            <br />
            {t('console:backups.checks', { passed, total: op.checks.length })}
            {typeof op.details.rto_seconds === 'number' &&
              ` — ${t('console:backups.rto', { value: f.duration(op.details.rto_seconds) })}`}
          </>
        )}
        {op.errors.length > 0 && (
          <ul>
            {op.errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}
      </dd>
    </div>
  );
}

function StorageCard({ data }: { data: BackupStatus }) {
  const { t } = useTranslation();
  const f = useFormat();
  const s = data.storage;
  return (
    <Card title={t('console:backups.storage')} subTitle={<LevelBadge level={s.level} />}>
      <dl className="sm-details" data-testid="backups-storage">
        <Row label={t('console:backups.count')}>
          {t('console:backups.countValue', {
            count: s.base_backups,
            verified: s.base_backups_verified,
          })}
        </Row>
        <Row label={t('console:backups.latest')}>{s.latest_verified_backup ?? '—'}</Row>
        <Row label={t('console:backups.restorableFrom')}>{f.when(s.restorable_from)}</Row>
        <Row label={t('console:backups.size')}>{f.bytes(s.base_backups_bytes)}</Row>
        <Row label={t('console:backups.walFiles')}>
          {s.wal_files} ({f.bytes(s.wal_bytes)})
        </Row>
        <Row label={t('console:backups.diskFree')}>
          {s.disk_free_percent === null ? '—' : `${s.disk_free_percent} %`} (
          {f.bytes(s.disk_free_bytes)})
        </Row>
      </dl>
      {s.messages.map((m) => (
        <Message key={m} severity="warn" text={t(`console:backups.messages.${m}`)} />
      ))}
    </Card>
  );
}

function OffsiteCard({ data }: { data: BackupStatus }) {
  const { t } = useTranslation();
  const f = useFormat();
  const o = data.offsite;
  return (
    <Card title={t('console:backups.offsite')} subTitle={<LevelBadge level={o.level} />}>
      {!o.configured ? (
        <Message
          severity="warn"
          text={t('console:backups.offsiteNotConfigured')}
          data-testid="backups-offsite-missing"
        />
      ) : (
        <dl className="sm-details" data-testid="backups-offsite">
          <Row label={t('console:backups.repository')}>{o.repository ?? '—'}</Row>
          <Row label={t('console:backups.lastCopy')}>
            {f.when(o.last_success_at)}
            {o.last_success_age_seconds !== null && ` (${f.ago(o.last_success_age_seconds)})`}
          </Row>
          {o.problem && (
            <Row label={t('console:backups.problemLabel')}>
              {t(`console:backups.problem.${o.problem}`)}
            </Row>
          )}
        </dl>
      )}
    </Card>
  );
}

function SupervisionCard({ data }: { data: BackupStatus }) {
  const { t } = useTranslation();
  const f = useFormat();
  const s = data.supervision;
  return (
    <Card title={t('console:backups.supervision')} subTitle={<LevelBadge level={s.level} />}>
      <p data-testid="backups-supervision">
        {s.last_check_at
          ? t('console:backups.lastCheck', {
              when: f.when(s.last_check_at),
              age: f.duration(s.age_seconds),
            })
          : t('console:backups.noCheck')}
      </p>
      {s.stale && <Message severity="error" text={t('console:backups.supervisionStale')} />}
    </Card>
  );
}
