import { Card } from 'primereact/card';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { LoadingState } from '@/shared/ui/LoadingState';
import { MetricCard } from '@/shared/ui/MetricCard';

import { useCreditExposure, useCustomerReceivables } from './api';
import { ReceivablesTable } from './ReceivablesTable';

/**
 * Compte client sur sa fiche : limite de crédit, exposition actuelle (restes dus de ses ventes
 * validées), crédit disponible et créances ouvertes. Limite non configurée : aucun montant
 * disponible affiché (jamais de valeur fabriquée).
 */
export default function CustomerCreditPanel({ customerId }: { customerId: string }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const exposure = useCreditExposure(customerId);
  const receivables = useCustomerReceivables(customerId, toQueryString(table));

  if (exposure.isPending) return <LoadingState />;
  if (exposure.isError) {
    return <ErrorMessage error={exposure.error} onRetry={() => void exposure.refetch()} />;
  }
  const e = exposure.data;
  const money = (value: string) => formatMoney(value, currency, locale);

  return (
    <section className="sm-block" aria-labelledby="customer-credit-title">
      <div className="sm-section-header">
        <h2 id="customer-credit-title">{t('receivables.account')}</h2>
      </div>
      <div className="sm-metrics" role="group" aria-label={t('receivables.creditSummary')}>
        <MetricCard
          icon="pi pi-shield"
          value={e.credit_limit === null ? t('receivables.notConfigured') : money(e.credit_limit)}
          label={t('receivables.creditLimit')}
          hint={e.credit_limit === null ? t('receivables.notConfiguredHint') : undefined}
        />
        <MetricCard
          icon="pi pi-wallet"
          tone={e.over_limit ? 'danger' : e.open_receivables_count > 0 ? 'warning' : 'neutral'}
          value={money(e.current_exposure)}
          label={t('receivables.exposure')}
          hint={t('receivables.openReceivables', { count: e.open_receivables_count })}
        />
        {e.available_credit !== null && (
          <MetricCard
            icon="pi pi-check-circle"
            tone={e.over_limit ? 'danger' : 'success'}
            value={money(e.available_credit)}
            label={t('receivables.available')}
          />
        )}
      </div>
      {e.over_limit && <Message severity="error" text={t('receivables.overLimit')} />}
      {!e.consolidated && <Message severity="info" text={t('receivables.partialView')} />}
      <Card title={t('receivables.customerReceivables')}>
        <ReceivablesTable
          query={receivables}
          table={table}
          onTableChange={setTable}
          showCustomer={false}
          empty={<EmptyState icon="pi pi-check-circle" title={t('receivables.customerEmpty')} />}
        />
      </Card>
    </section>
  );
}
