import { Card } from 'primereact/card';
import { ProgressBar } from 'primereact/progressbar';
import { useTranslation } from 'react-i18next';

import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import { useBackupOverview } from '../queries';
import type { BackupLevel } from '../types';
import { RestoreStatusBadge, RunStatusBadge, when, whenUtc } from './shared';

const TONES = { ok: 'success', warning: 'warning', critical: 'danger' } as const;

function Level({ level }: { level: BackupLevel }) {
  const { t } = useTranslation();
  return <StatusBadge tone={TONES[level]} label={t(`console:backups.level.${level}`)} />;
}

/** Agent, planification et opérations en cours (relu toutes les 10 s). */
export function OverviewPanel({ onOpenRestore }: { onOpenRestore: (id: string) => void }) {
  const { t } = useTranslation();
  const overview = useBackupOverview();
  if (overview.isLoading) return <LoadingState />;
  if (overview.isError || !overview.data) {
    return <ErrorMessage error={overview.error} onRetry={() => void overview.refetch()} />;
  }
  const o = overview.data;
  return (
    <div className="sm-dashboard-grid" data-testid="backup-overview">
      <Card title={t('console:bk.agent')} subTitle={<Level level={o.agent.level} />}>
        <p data-testid="agent-state">
          {o.agent.problem
            ? t(`console:bk.agentProblem.${o.agent.problem}`)
            : t('console:bk.agentAlive', { when: when(o.agent.last_seen_at) })}
        </p>
        {o.agent.last_error && <p className="sm-danger">{o.agent.last_error}</p>}
        <p className="sm-muted">{t('console:bk.schema', { revision: o.schema_revision ?? '—' })}</p>
      </Card>
      <Card title={t('console:bk.schedulesTitle')}>
        <ul className="sm-list" data-testid="schedules">
          {o.schedules.map((s) => (
            <li key={s.category}>
              <Level level={s.level} />{' '}
              <span className="sm-strong">{t(`console:bk.kinds.${s.category}`)}</span>
              {' — '}
              {s.enabled_frequencies.length
                ? s.enabled_frequencies.map((f) => t(`console:bk.frequencies.${f}`)).join(', ')
                : t('console:bk.notScheduled')}
              <br />
              <small className="sm-muted">
                {t('console:bk.nextRun', { when: whenUtc(s.next_run_at) })} ·{' '}
                {t('console:bk.lastSuccessAt', {
                  when: when(s.last_success_at, t('console:backups.never')),
                })}
                {s.problem && ` · ${t(`console:bk.scheduleProblem.${s.problem}`)}`}
              </small>
            </li>
          ))}
        </ul>
      </Card>
      <Card title={t('console:bk.activeTitle')}>
        {o.active_runs.length === 0 && o.active_restores.length === 0 && (
          <p className="sm-muted">{t('console:bk.nothingActive')}</p>
        )}
        <ul className="sm-list" data-testid="active-runs">
          {o.active_runs.map((r) => (
            <li key={r.id}>
              <RunStatusBadge status={r.status} /> {t(`console:bk.kinds.${r.kind}`)} —{' '}
              {r.tenant_name ?? t('console:bk.allTenants')}
              {r.status === 'running' && (
                <ProgressBar value={r.progress_percent} style={{ height: '0.4rem' }} />
              )}
            </li>
          ))}
          {o.active_restores.map((op) => (
            <li key={op.id}>
              <RestoreStatusBadge status={op.status} />{' '}
              <button type="button" className="sm-link" onClick={() => onOpenRestore(op.id)}>
                {op.scope === 'platform' ? t('console:bk.platformRestore') : op.tenant_name}
              </button>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
