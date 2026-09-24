import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Message } from 'primereact/message';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import type { Sale } from '@/modules/sales/api';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { MetricCard } from '@/shared/ui/MetricCard';
import { PageHeader } from '@/shared/ui/PageHeader';
import { DocumentStatusBadge, StatusBadge, SubscriptionStatusBadge } from '@/shared/ui/StatusBadge';

import { useAlertSummary, useDocumentCount, useRecentSales } from './api';

const TRANSFERS_FEATURE = 'stock.transfers';

interface Shortcut {
  key: string;
  label: string;
  icon: string;
  to: string;
  visible: boolean;
}

/** Valeur d'un indicateur : tiret pendant le chargement ou en cas d'erreur. */
function metricValue(value: number | undefined): string | number {
  return value ?? '—';
}

export default function DashboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities: caps } = useCapabilities();
  const { currency, locale } = caps.tenant;

  // Tout est piloté par les permissions et fonctionnalités effectives (jamais par le plan,
  // le rôle ou le secteur d'activité).
  const canAlerts = can('alerts.stock.view');
  const canSales = can('sales.sale.view');
  const canTransfers = can('stock.transfer.view');
  const transfersActive = caps.features.includes(TRANSFERS_FEATURE);

  const alerts = useAlertSummary(canAlerts);
  const draftSales = useDocumentCount('/sales', 'status=DRAFT', canSales);
  const draftTransfers = useDocumentCount('/stock/transfers', 'status=DRAFT', canTransfers);
  const recentSales = useRecentSales(canSales);

  const shortcuts: Shortcut[] = [
    {
      key: 'sale',
      label: t('sales.new'),
      icon: 'pi pi-shopping-cart',
      to: '/sales/new',
      visible: can('sales.sale.create'),
    },
    {
      key: 'entry',
      label: t('entries.new'),
      icon: 'pi pi-download',
      to: '/stock/entries/new',
      visible: can('stock.entry.create'),
    },
    {
      key: 'exit',
      label: t('exits.new'),
      icon: 'pi pi-upload',
      to: '/stock/exits/new',
      visible: can('stock.exit.create'),
    },
    {
      key: 'transfer',
      label: t('transfers.new'),
      icon: 'pi pi-arrow-right-arrow-left',
      to: '/stock/transfers/new',
      visible: transfersActive && can('stock.transfer.create'),
    },
  ].filter((s) => s.visible);

  const metrics: ReactNode[] = [];
  if (canAlerts) {
    const out = alerts.data?.out;
    const low = alerts.data?.low;
    metrics.push(
      <MetricCard
        key="out"
        icon="pi pi-times-circle"
        tone={out ? 'danger' : 'success'}
        value={metricValue(out)}
        label={t('dashboard.outOfStock')}
        hint={t('dashboard.seeAlerts')}
        to="/alerts/stock"
      />,
      <MetricCard
        key="low"
        icon="pi pi-exclamation-triangle"
        tone={low ? 'warning' : 'success'}
        value={metricValue(low)}
        label={t('dashboard.lowStock')}
        hint={t('dashboard.seeAlerts')}
        to="/alerts/stock"
      />,
    );
  }
  if (canSales) {
    metrics.push(
      <MetricCard
        key="sales"
        icon="pi pi-file-edit"
        tone="info"
        value={metricValue(draftSales.data)}
        label={t('dashboard.draftSales')}
        to="/sales"
      />,
    );
  }
  if (canTransfers) {
    metrics.push(
      <MetricCard
        key="transfers"
        icon="pi pi-arrow-right-arrow-left"
        tone="info"
        value={metricValue(draftTransfers.data)}
        label={t('dashboard.draftTransfers')}
        to="/stock/transfers"
      />,
    );
  }

  const offer = caps.modules.filter((m) => !m.core);

  return (
    <>
      <PageHeader
        title={t('dashboard.welcome', { name: caps.user.full_name })}
        description={t('dashboard.subtitle', {
          tenant: caps.tenant.name,
          site: caps.site?.name ?? t('layout.allSites'),
        })}
      />
      {caps.restricted_permissions.length > 0 && (
        <Message severity="warn" text={t('dashboard.restricted')} className="sm-block" />
      )}
      {metrics.length > 0 && (
        <section className="sm-metrics" aria-label={t('dashboard.indicators')}>
          {metrics}
        </section>
      )}
      <div className="sm-dashboard-grid">
        <div className="sm-stack">
          {canSales && (
            <Card title={t('dashboard.recentSales')}>
              {recentSales.isError ? (
                <ErrorMessage
                  error={recentSales.error}
                  onRetry={() => void recentSales.refetch()}
                />
              ) : (
                <DataTable
                  className="sm-table sm-table--compact"
                  value={recentSales.data?.items ?? []}
                  loading={recentSales.isPending}
                  dataKey="id"
                  rowHover
                  rowClassName={() => 'sm-row-clickable'}
                  onRowClick={(e) => void navigate(`/sales/${(e.data as Sale).id}`)}
                  tableStyle={{ minWidth: '30rem' }}
                  emptyMessage={<EmptyState icon="pi pi-shopping-cart" title={t('sales.empty')} />}
                >
                  <Column field="number" header={t('sales.number')} />
                  <Column
                    header={t('sales.date')}
                    body={(s: Sale) => formatDate(s.sale_date, locale)}
                  />
                  <Column
                    header={t('sales.customer')}
                    body={(s: Sale) => s.customer_name ?? t('sales.anonymousShort')}
                  />
                  <Column
                    header={t('sales.total')}
                    headerClassName="sm-num"
                    bodyClassName="sm-num"
                    body={(s: Sale) => formatMoney(s.total, currency, locale)}
                  />
                  <Column
                    header={t('sales.status')}
                    body={(s: Sale) => (
                      <DocumentStatusBadge labels="sales.statuses" status={s.status} />
                    )}
                  />
                </DataTable>
              )}
              <div className="sm-form-actions">
                <Button
                  label={t('dashboard.allSales')}
                  icon="pi pi-arrow-right"
                  iconPos="right"
                  text
                  onClick={() => void navigate('/sales')}
                />
              </div>
            </Card>
          )}
          <Card title={t('dashboard.offer')}>
            <ul className="sm-chips" aria-label={t('dashboard.offer')}>
              {offer.map((module) => (
                <li key={module.code}>
                  <span>{t(`modules.${module.code}`)}</span>
                  {module.status === 'planned' && (
                    <StatusBadge tone="neutral" label={t('common.comingSoon')} />
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="sm-stack">
          {shortcuts.length > 0 && (
            <Card title={t('dashboard.shortcuts')}>
              <nav className="sm-quick-actions" aria-label={t('dashboard.shortcuts')}>
                {shortcuts.map((s) => (
                  <Button
                    key={s.key}
                    icon={s.icon}
                    label={s.label}
                    outlined
                    onClick={() => void navigate(s.to)}
                  />
                ))}
              </nav>
            </Card>
          )}
          <Card title={t('dashboard.subscription')}>
            <dl className="sm-details">
              <div>
                <dt>{t('dashboard.plan')}</dt>
                <dd>
                  {caps.plan.name} · {t(`billingPeriod.${caps.subscription.billing_period}`)}
                </dd>
              </div>
              <div>
                <dt>{t('subscriptionPage.status')}</dt>
                <dd>
                  <SubscriptionStatusBadge status={caps.subscription.status} />
                </dd>
              </div>
              <div>
                <dt>{t('dashboard.periodEnd')}</dt>
                <dd>{formatDate(caps.subscription.current_period_end, locale)}</dd>
              </div>
              <div>
                <dt>{t('dashboard.profile')}</dt>
                <dd>{caps.profile.name}</dd>
              </div>
            </dl>
          </Card>
        </div>
      </div>
    </>
  );
}
