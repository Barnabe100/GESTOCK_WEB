import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';

import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SubscriptionStatusBadge } from '@/shared/ui/StatusBadge';

import { AuditChanges } from '../auditDisplay';
import { CONSOLE_BASE } from '../ConsoleLayout';
import { planPrice } from '../planDisplay';
import { useAudit, useTenant } from '../queries';
import { TenantActionDialog, type ActionKind } from '../TenantActionDialog';
import { Details, TenantStatusBadge, tenantDate } from '../tenantDisplay';
import type { PlatformAuditEntry, TenantDetail } from '../types';

function TenantHistory({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation();
  const history = useAudit(20, 0, { tenant_id: tenantId });
  if (history.isError) {
    return <ErrorMessage error={history.error} onRetry={() => void history.refetch()} />;
  }
  return (
    <Card title={t('console:tenant.historyTitle')}>
      <DataTable
        className="sm-table"
        tableStyle={{ minWidth: '48rem' }}
        value={history.data?.items ?? []}
        loading={history.isFetching}
        dataKey="id"
        emptyMessage={<EmptyState icon="pi pi-history" title={t('console:tenant.historyEmpty')} />}
      >
        <Column
          header={t('console:audit.date')}
          bodyClassName="sm-nowrap"
          body={(e: PlatformAuditEntry) => formatDateTime(e.occurred_at)}
        />
        <Column field="actor_label" header={t('console:audit.actor')} />
        <Column field="action" header={t('console:audit.action')} bodyClassName="sm-nowrap" />
        <Column
          header={t('console:audit.changes')}
          body={(e: PlatformAuditEntry) => <AuditChanges entry={e} />}
        />
        <Column field="reason" header={t('console:audit.reason')} />
      </DataTable>
    </Card>
  );
}

function Actions({
  tenant,
  onAction,
}: {
  tenant: TenantDetail;
  onAction: (k: ActionKind) => void;
}) {
  const { t } = useTranslation();
  const a = tenant.actions;
  const buttons: [ActionKind, boolean, string, boolean][] = [
    ['activate', a.can_activate, 'pi pi-check-circle', false],
    ['extend', a.can_extend, 'pi pi-calendar-plus', false],
    ['change-plan', a.can_change_plan, 'pi pi-sync', false],
    ['reactivate', a.can_reactivate, 'pi pi-play', false],
    ['suspend', a.can_suspend, 'pi pi-ban', true],
  ];
  const available = buttons.filter(([, allowed]) => allowed);
  return (
    <Card title={t('console:tenant.actionsTitle')}>
      <p className="sm-help">{t('console:tenant.actionsHelp')}</p>
      {available.length === 0 ? (
        <p className="sm-muted">{t('console:tenant.noAction')}</p>
      ) : (
        <div className="sm-quick-actions" data-testid="tenant-actions">
          {available.map(([kind, , icon, danger]) => (
            <Button
              key={kind}
              icon={icon}
              label={t(`console:tenant.action.${kind}`)}
              severity={danger ? 'danger' : undefined}
              outlined={danger}
              onClick={() => onAction(kind)}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

export function TenantDetailPage() {
  const { t } = useTranslation();
  const { id = '' } = useParams();
  const tenant = useTenant(id);
  const [action, setAction] = useState<ActionKind | null>(null);

  if (tenant.isLoading) return <LoadingState />;
  if (tenant.isError || !tenant.data) {
    return <ErrorMessage error={tenant.error} onRetry={() => void tenant.refetch()} />;
  }
  const d = tenant.data;
  const s = d.subscription;
  const date = (iso: string) => tenantDate(iso, d.timezone);
  return (
    <>
      <PageHeader
        title={d.name}
        description={d.trade_name ?? undefined}
        breadcrumbs={[
          { label: t('console:tenant.breadcrumb'), to: `${CONSOLE_BASE}/tenants` },
          { label: d.name },
        ]}
        actions={
          <>
            <span className="sm-tags" data-testid="tenant-badges">
              <TenantStatusBadge status={d.status} />
              <SubscriptionStatusBadge status={s.effective_status} />
            </span>
            <Link
              className="p-button p-button-outlined"
              to={`${CONSOLE_BASE}/payments?tenant_id=${encodeURIComponent(d.id)}`}
            >
              <i className="pi pi-wallet" aria-hidden />
              <span>{t('console:payments.ofTenant')}</span>
            </Link>
          </>
        }
      />
      <div className="sm-dashboard-grid">
        <Card title={t('console:tenant.identity')}>
          <Details
            items={[
              [t('console:tenant.name'), d.name],
              [t('console:tenant.tradeName'), d.trade_name],
              [t('console:tenant.profile'), d.business_profile_name],
              [t('console:tenant.country'), d.country_name ?? d.country_code],
              [t('console:tenant.currency'), d.currency],
              [t('console:tenant.timezone'), d.timezone],
              [t('console:tenant.slug'), <span className="sm-code">{d.slug}</span>],
              [t('console:tenant.createdAt'), date(d.created_at)],
            ]}
          />
        </Card>
        <Card title={t('console:tenant.usage')}>
          <Details
            items={Object.entries(d.usage).map(([code, u]) => [
              t(`console:limits.${code}`, { defaultValue: code }),
              <>
                {t('console:tenant.usageOf', {
                  used: u.used,
                  limit: u.limit ?? t('console:tenant.unlimited'),
                })}
                {u.limit !== null && u.used > u.limit && (
                  <small className="sm-muted"> — {t('console:tenant.overLimit')}</small>
                )}
              </>,
              `usage-${code}`,
            ])}
          />
        </Card>
        <Card title={t('console:tenant.subscriptionTitle')}>
          <Details
            items={[
              [t('console:tenant.plan'), `${s.plan_name} (${s.plan_code})`, 'subscription-plan'],
              [
                t('console:tenant.effectiveStatus'),
                <SubscriptionStatusBadge status={s.effective_status} />,
              ],
              [t('console:tenant.subscriptionStatus'), t(`subscriptionStatus.${s.status}`)],
              [t('console:tenant.billingPeriod'), t(`billingPeriod.${s.billing_period}`)],
              [t('console:tenant.startedAt'), date(s.started_at)],
              [t('console:tenant.periodStart'), date(s.current_period_start)],
              [t('console:tenant.periodEnd'), date(s.current_period_end), 'subscription-end'],
              [
                t('console:tenant.graceDays'),
                t('console:plans.trialDays', { count: s.grace_days }),
              ],
              [
                t('console:tenant.price'),
                s.price_at_subscription === null
                  ? t('console:tenant.noPrice')
                  : planPrice(s.price_at_subscription, s.currency_at_subscription),
                'subscription-price',
              ],
            ]}
          />
        </Card>
        <Card title={t('console:tenant.statusTitle')}>
          <p className="sm-help">{t('console:tenant.statusHelp')}</p>
          <TenantStatusBadge status={d.status} />
        </Card>
      </div>
      <Actions tenant={d} onAction={setAction} />
      <TenantHistory tenantId={d.id} />
      {action && <TenantActionDialog tenant={d} kind={action} onClose={() => setAction(null)} />}
    </>
  );
}
