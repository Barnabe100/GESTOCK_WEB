import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { MetricCard } from '@/shared/ui/MetricCard';
import { PageHeader } from '@/shared/ui/PageHeader';

import { CONSOLE_BASE } from '../ConsoleLayout';
import { useDashboard } from '../queries';

export function ConsoleDashboardPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const dashboard = useDashboard();

  if (dashboard.isLoading) return <LoadingState />;
  if (dashboard.isError || !dashboard.data) {
    return <ErrorMessage error={dashboard.error} onRetry={() => void dashboard.refetch()} />;
  }
  const d = dashboard.data;
  const plans = `${CONSOLE_BASE}/plans`;
  const catalog = `${CONSOLE_BASE}/catalog`;
  const tenants = `${CONSOLE_BASE}/tenants`;
  return (
    <>
      <PageHeader
        title={t('console:dashboard.title')}
        description={t('console:dashboard.subtitle')}
      />
      <p className="sm-muted" data-testid="console-identity">
        {t('console:dashboard.connectedAs', { name: d.admin.full_name, email: d.admin.email })}
      </p>
      <h2 className="sm-section-title">{t('console:dashboard.tenantsSection')}</h2>
      <div className="sm-metrics" data-testid="dashboard-tenants">
        <MetricCard
          icon="pi pi-building"
          label={t('console:dashboard.tenantsTotal')}
          value={d.tenants.tenants_total}
          to={tenants}
        />
        <MetricCard
          icon="pi pi-check-circle"
          tone="success"
          label={t('console:dashboard.tenantsActive')}
          value={d.tenants.tenants_active}
        />
        <MetricCard
          icon="pi pi-ban"
          tone="danger"
          label={t('console:dashboard.tenantsSuspended')}
          value={d.tenants.tenants_suspended}
        />
        <MetricCard
          icon="pi pi-verified"
          tone="success"
          label={t('console:dashboard.subscriptionsActive')}
          value={d.tenants.subscriptions_active}
        />
        <MetricCard
          icon="pi pi-hourglass"
          tone="warning"
          label={t('console:dashboard.subscriptionsPending')}
          value={d.tenants.subscriptions_pending_activation}
        />
        <MetricCard
          icon="pi pi-gift"
          tone="info"
          label={t('console:dashboard.subscriptionsTrial')}
          value={d.tenants.subscriptions_trial}
        />
        <MetricCard
          icon="pi pi-calendar"
          tone="warning"
          label={t('console:dashboard.subscriptionsRenewal')}
          value={d.tenants.subscriptions_renewal_due}
        />
        <MetricCard
          icon="pi pi-exclamation-triangle"
          tone="warning"
          label={t('console:dashboard.subscriptionsPastDue')}
          value={d.tenants.subscriptions_past_due}
        />
        <MetricCard
          icon="pi pi-times-circle"
          tone="danger"
          label={t('console:dashboard.subscriptionsExpired')}
          value={d.tenants.subscriptions_expired}
        />
      </div>
      <h2 className="sm-section-title">{t('console:dashboard.offers')}</h2>
      <div className="sm-metrics">
        <MetricCard
          icon="pi pi-tags"
          label={t('console:dashboard.plansActive')}
          value={d.plans_active}
          to={plans}
        />
        <MetricCard
          icon="pi pi-eye"
          tone="success"
          label={t('console:dashboard.plansListed')}
          value={d.plans_listed}
          to={plans}
        />
        <MetricCard
          icon="pi pi-shopping-cart"
          tone="info"
          label={t('console:dashboard.plansSelfService')}
          value={d.plans_self_service}
        />
        <MetricCard
          icon="pi pi-phone"
          tone="warning"
          label={t('console:dashboard.plansContact')}
          value={d.plans_contact_required}
        />
      </div>
      <h2 className="sm-section-title">{t('console:dashboard.catalog')}</h2>
      <div className="sm-metrics">
        <MetricCard
          icon="pi pi-th-large"
          label={t('console:dashboard.modulesAvailable')}
          value={d.modules_available}
          to={catalog}
        />
        <MetricCard
          icon="pi pi-clock"
          label={t('console:dashboard.modulesPlanned')}
          value={d.modules_planned}
        />
        <MetricCard
          icon="pi pi-key"
          label={t('console:dashboard.permissions')}
          value={d.permissions}
        />
        <MetricCard
          icon="pi pi-briefcase"
          label={t('console:dashboard.profilesActive')}
          value={d.profiles_active}
        />
        <MetricCard
          icon="pi pi-globe"
          label={t('console:dashboard.countries')}
          value={d.active_countries}
        />
      </div>
      <Card title={t('console:dashboard.shortcuts')}>
        <div className="sm-quick-actions">
          <Button
            icon="pi pi-tags"
            label={t('console:dashboard.openPlans')}
            onClick={() => navigate(plans)}
          />
          <Button
            icon="pi pi-history"
            outlined
            label={t('console:dashboard.openAudit')}
            onClick={() => navigate(`${CONSOLE_BASE}/audit`)}
          />
          <Button
            icon="pi pi-book"
            outlined
            label={t('console:dashboard.openCatalog')}
            onClick={() => navigate(catalog)}
          />
        </div>
      </Card>
    </>
  );
}
