import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';

import { CONSOLE_BASE } from '../ConsoleLayout';
import { PlanModeBadge, PlanStatusBadge, planPrice, trialLabel } from '../planDisplay';
import { usePlans } from '../queries';
import type { Plan } from '../types';

/** Prix d'une période : « — » si non renseigné, mention « période fermée » sinon. */
function PeriodPrice({
  price,
  enabled,
  plan,
}: {
  price: string | null;
  enabled: boolean;
  plan: Plan;
}) {
  const { t } = useTranslation();
  return (
    <span className={enabled ? undefined : 'sm-muted'}>
      {planPrice(price, plan.currency)}
      {!enabled && price !== null && <small> ({t('console:plans.periodClosed')})</small>}
    </span>
  );
}

export function PlansPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const plans = usePlans();
  const open = (plan: Plan) => navigate(`${CONSOLE_BASE}/plans/${encodeURIComponent(plan.code)}`);

  return (
    <>
      <PageHeader title={t('console:plans.title')} description={t('console:plans.subtitle')} />
      {plans.isError ? (
        <ErrorMessage error={plans.error} onRetry={() => void plans.refetch()} />
      ) : (
        <DataTable
          className="sm-table"
          tableStyle={{ minWidth: '64rem' }}
          value={plans.data ?? []}
          loading={plans.isFetching}
          dataKey="code"
          rowHover
          rowClassName={() => 'sm-clickable'}
          onRowClick={(e) => open(e.data as Plan)}
          emptyMessage={<EmptyState icon="pi pi-tags" title={t('console:plans.empty')} />}
        >
          <Column
            field="code"
            header={t('console:plans.code')}
            bodyClassName="sm-nowrap sm-strong"
          />
          <Column field="name" header={t('console:plans.name')} />
          <Column
            header={t('console:plans.status')}
            body={(p: Plan) => <PlanStatusBadge plan={p} />}
          />
          <Column header={t('console:plans.mode')} body={(p: Plan) => <PlanModeBadge plan={p} />} />
          <Column
            header={t('console:plans.monthly')}
            bodyClassName="sm-num sm-nowrap"
            body={(p: Plan) => (
              <PeriodPrice plan={p} price={p.monthly_price} enabled={p.monthly_price_enabled} />
            )}
          />
          <Column
            header={t('console:plans.annual')}
            bodyClassName="sm-num sm-nowrap"
            body={(p: Plan) => (
              <PeriodPrice plan={p} price={p.annual_price} enabled={p.annual_price_enabled} />
            )}
          />
          <Column header={t('console:plans.currency')} body={(p: Plan) => p.currency ?? '—'} />
          <Column
            header={t('console:plans.trial')}
            body={(p: Plan) => trialLabel(t, p.trial_days)}
          />
          <Column
            header={t('console:plans.contact')}
            body={(p: Plan) => t(p.contact_required ? 'console:plans.yes' : 'console:plans.no')}
          />
          <Column field="display_order" header={t('console:plans.order')} bodyClassName="sm-num" />
          <Column
            header={t('console:plans.description')}
            body={(p: Plan) => p.commercial_description ?? <span className="sm-muted">—</span>}
          />
          <Column
            header=""
            body={(p: Plan) => (
              <Button
                icon="pi pi-arrow-right"
                text
                rounded
                aria-label={`${t('console:plans.open')} ${p.name}`}
                onClick={(e) => {
                  e.stopPropagation();
                  open(p);
                }}
              />
            )}
          />
        </DataTable>
      )}
    </>
  );
}
