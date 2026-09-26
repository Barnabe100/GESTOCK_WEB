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
import { LicenseStateBadge, SubscriptionStatusBadge } from '@/shared/ui/StatusBadge';

import { AuditChanges } from '../auditDisplay';
import { CONSOLE_BASE } from '../ConsoleLayout';
import { planPrice } from '../planDisplay';
import { useAudit, useTenant } from '../queries';
import { TenantActionDialog, type ActionKind } from '../TenantActionDialog';
import { Details, TenantStatusBadge, tenantDate } from '../tenantDisplay';
import type { PlatformAuditEntry, TenantDetail, TenantSubscription } from '../types';

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

/** Abonnement d'un site (1 site = 1 abonnement, ADR-0033) et ses actions. */
function SubscriptionCard({
  tenant,
  subscription: s,
  onAction,
}: {
  tenant: TenantDetail;
  subscription: TenantSubscription;
  onAction: (kind: ActionKind, subscription: TenantSubscription) => void;
}) {
  const { t } = useTranslation();
  const date = (iso: string) => tenantDate(iso, tenant.timezone);
  const code = s.site?.code ?? 'pending';
  const buttons: [ActionKind, boolean, string][] = [
    ['activate', s.actions.can_activate, 'pi pi-check-circle'],
    ['extend', s.actions.can_extend, 'pi pi-calendar-plus'],
    ['change-plan', s.actions.can_change_plan, 'pi pi-sync'],
  ];
  const available = buttons.filter(([, allowed]) => allowed);
  return (
    <Card
      title={s.site ? `${s.site.name} (${s.site.code})` : t('console:tenant.unattached')}
      data-testid={`subscription-${code}`}
    >
      <Details
        items={[
          [
            t('console:tenant.plan'),
            `${s.plan_name} (${s.plan_code})`,
            `subscription-plan-${code}`,
          ],
          [
            t('console:tenant.effectiveStatus'),
            <SubscriptionStatusBadge status={s.effective_status} />,
            `subscription-status-${code}`,
          ],
          [t('console:tenant.subscriptionStatus'), t(`subscriptionStatus.${s.status}`)],
          [t('console:tenant.billingPeriod'), t(`billingPeriod.${s.billing_period}`)],
          [t('console:tenant.periodStart'), date(s.current_period_start)],
          [t('console:tenant.periodEnd'), date(s.current_period_end), `subscription-end-${code}`],
          [t('console:tenant.graceDays'), t('console:plans.trialDays', { count: s.grace_days })],
          [
            t('console:tenant.price'),
            s.price_at_subscription === null
              ? t('console:tenant.noPrice')
              : planPrice(s.price_at_subscription, s.currency_at_subscription),
            `subscription-price-${code}`,
          ],
          [t('console:tenant.requestedActivations'), String(s.requested_activations)],
          [
            t('console:license.cardTitle'),
            s.license ? (
              <Link to={`${CONSOLE_BASE}/licenses/${s.license.id}`}>
                {s.license.license_number} · <LicenseStateBadge state={s.license.state} /> ·{' '}
                {t('console:license.activationsCount', { count: s.license.max_activations })}
              </Link>
            ) : (
              t('console:license.none')
            ),
            `subscription-license-${code}`,
          ],
          ...Object.entries(s.usage).map(
            ([limit, u]) =>
              [
                t(`console:limits.${limit}`, { defaultValue: limit }),
                t('console:tenant.usageOf', {
                  used: u.used,
                  limit: u.limit ?? t('console:tenant.unlimited'),
                }),
                `usage-${limit}-${code}`,
              ] as [string, string, string],
          ),
        ]}
      />
      {available.length > 0 && (
        <div className="sm-quick-actions" data-testid={`subscription-actions-${code}`}>
          {available.map(([kind, , icon]) => (
            <Button
              key={kind}
              icon={icon}
              label={t(`console:tenant.action.${kind}`)}
              onClick={() => onAction(kind, s)}
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
  const [action, setAction] = useState<{
    kind: ActionKind;
    subscription?: TenantSubscription;
  } | null>(null);

  if (tenant.isLoading) return <LoadingState />;
  if (tenant.isError || !tenant.data) {
    return <ErrorMessage error={tenant.error} onRetry={() => void tenant.refetch()} />;
  }
  const d = tenant.data;
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
              [t('console:tenant.sitesCount'), String(d.sites), 'usage-sites'],
              [t('console:tenant.usersCount'), String(d.users), 'usage-users'],
            ]}
          />
        </Card>
        <Card title={t('console:tenant.statusTitle')}>
          <p className="sm-help">{t('console:tenant.statusHelp')}</p>
          <TenantStatusBadge status={d.status} />
        </Card>
      </div>
      <Actions tenant={d} onAction={(kind) => setAction({ kind })} />
      <section className="sm-block" aria-labelledby="subscriptions-title">
        <h2 id="subscriptions-title">{t('console:tenant.subscriptionsTitle')}</h2>
        <p className="sm-help">{t('console:tenant.subscriptionsHelp')}</p>
        <div className="sm-dashboard-grid">
          {d.subscriptions.map((s) => (
            <SubscriptionCard
              key={s.id}
              tenant={d}
              subscription={s}
              onAction={(kind, subscription) => setAction({ kind, subscription })}
            />
          ))}
        </div>
      </section>
      <TenantHistory tenantId={d.id} />
      {action && (
        <TenantActionDialog
          tenant={d}
          kind={action.kind}
          subscription={action.subscription}
          onClose={() => setAction(null)}
        />
      )}
    </>
  );
}
