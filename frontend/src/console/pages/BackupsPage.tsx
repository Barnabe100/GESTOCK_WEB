import { Card } from 'primereact/card';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';

import { formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import { useBackupStatus } from '../queries';

/**
 * Sauvegardes (lecture seule) : archivage continu des WAL et dernière sauvegarde de base
 * vérifiée, évalués par le serveur (RPO, fraîcheur). Aucune action : sauvegarder et restaurer
 * restent des opérations d'exploitation documentées et tracées.
 */
export function BackupsPage() {
  const { t } = useTranslation();
  const status = useBackupStatus();
  if (status.isLoading) return <LoadingState />;
  if (status.isError || !status.data) {
    return <ErrorMessage error={status.error} onRetry={() => void status.refetch()} />;
  }
  const { archiving, base_backups: base, rpo_seconds } = status.data;
  const when = (iso: string | null) =>
    iso ? formatDateTime(iso, 'fr') : t('console:backups.never');
  const badge = (ok: boolean) => (
    <StatusBadge
      tone={ok ? 'success' : 'danger'}
      icon={ok ? 'pi pi-check' : 'pi pi-exclamation-triangle'}
      label={t(ok ? 'console:backups.ok' : 'console:backups.alert')}
    />
  );
  return (
    <>
      <PageHeader title={t('console:backups.title')} description={t('console:backups.subtitle')} />
      <div className="sm-dashboard-grid">
        <Card title={t('console:backups.archiving')} subTitle={badge(archiving.ok)}>
          <dl className="sm-details" data-testid="backups-archiving">
            <div>
              <dt>{t('console:backups.mode')}</dt>
              <dd>{archiving.archive_mode ?? '—'}</dd>
            </div>
            <div>
              <dt>{t('console:backups.timeout')}</dt>
              <dd>{t('console:backups.seconds', { count: archiving.archive_timeout_seconds })}</dd>
            </div>
            <div>
              <dt>{t('console:backups.lastArchived')}</dt>
              <dd>
                {when(archiving.last_archived_at)}
                {archiving.last_archived_age_seconds !== null &&
                  ` (${t('console:backups.age', {
                    value: t('console:backups.seconds', {
                      count: archiving.last_archived_age_seconds,
                    }),
                  })})`}
              </dd>
            </div>
            <div>
              <dt>{t('console:backups.failed')}</dt>
              <dd>
                {archiving.failed_count}
                {archiving.last_failed_at &&
                  ` — ${t('console:backups.lastFailed')} : ${when(archiving.last_failed_at)}`}
              </dd>
            </div>
          </dl>
          <p className="sm-help">
            {t('console:backups.rpo', { minutes: Math.round(rpo_seconds / 60) })}
          </p>
        </Card>
        <Card title={t('console:backups.base')} subTitle={base.configured ? badge(base.ok) : null}>
          {!base.configured ? (
            <Message severity="warn" text={t('console:backups.notConfigured')} />
          ) : (
            <dl className="sm-details" data-testid="backups-base">
              <div>
                <dt>{t('console:backups.latest')}</dt>
                <dd>
                  {base.latest_label ?? t('console:backups.never')}
                  {base.latest_finished_at && ` — ${when(base.latest_finished_at)}`}
                  {base.latest_age_hours !== null &&
                    ` (${t('console:backups.age', {
                      value: t('console:backups.hours', { count: base.latest_age_hours }),
                    })})`}
                </dd>
              </div>
              <div>
                <dt>{t('console:backups.count')}</dt>
                <dd>{base.count}</dd>
              </div>
              <div>
                <dt>{t('console:backups.unverified')}</dt>
                <dd>{base.unverified_count}</dd>
              </div>
            </dl>
          )}
          <p className="sm-help">
            {t('console:backups.maxAge', { hours: base.max_age_hours })} —{' '}
            {t('console:backups.procedure')}
          </p>
        </Card>
      </div>
    </>
  );
}
