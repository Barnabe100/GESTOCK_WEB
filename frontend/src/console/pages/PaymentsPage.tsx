import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';

import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { SubscriptionPaymentStatusBadge } from '@/shared/ui/StatusBadge';

import { CONSOLE_BASE } from '../ConsoleLayout';
import { usePayments } from '../queries';
import { paymentDay } from '../tenantDisplay';
import type { ConsolePayment, SubscriptionPaymentStatus } from '../types';

const STATUSES: SubscriptionPaymentStatus[] = ['PENDING', 'CONFIRMED', 'REJECTED'];

/**
 * Paiements d'abonnement déclarés par les entreprises (filtres : statut, référence,
 * entreprise ; pagination et tri côté serveur, plus récents d'abord).
 */
export function PaymentsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tenantId = params.get('tenant_id');
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'created_at',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<SubscriptionPaymentStatus | null>(null);
  const term = useDebouncedValue(search.trim());
  const payments = usePayments(toQueryString(table, { search: term, status, tenant_id: tenantId }));
  const filtered = Boolean(term || status || tenantId);
  const firstPage = () => setTable((s) => ({ ...s, first: 0 }));
  const clearTenant = () => {
    setParams({});
    firstPage();
  };
  const open = (payment: ConsolePayment) => navigate(`${CONSOLE_BASE}/payments/${payment.id}`);

  return (
    <>
      <PageHeader
        title={t('console:payments.title')}
        description={t('console:payments.subtitle')}
        actions={
          <Button
            icon="pi pi-refresh"
            label={t('console:payments.refresh')}
            outlined
            loading={payments.isFetching}
            onClick={() => void payments.refetch()}
          />
        }
      />
      <FilterBar
        active={filtered}
        onReset={() => {
          setSearch('');
          setStatus(null);
          clearTenant();
        }}
      >
        <SearchInput
          value={search}
          onChange={(value) => {
            setSearch(value);
            firstPage();
          }}
          placeholder={t('console:payments.search')}
        />
        <Dropdown
          aria-label={t('console:payments.status')}
          data-testid="filter-payment-status"
          value={status}
          options={[
            { label: t('console:payments.allStatuses'), value: null },
            ...STATUSES.map((s) => ({ label: t(`subscriptionPaymentStatus.${s}`), value: s })),
          ]}
          onChange={(e) => {
            setStatus(e.value as SubscriptionPaymentStatus | null);
            firstPage();
          }}
        />
        {tenantId && (
          <Button
            type="button"
            icon="pi pi-times"
            iconPos="right"
            outlined
            label={t('console:payments.tenantFilter')}
            aria-label={t('console:payments.clearTenant')}
            onClick={clearTenant}
            data-testid="tenant-filter"
          />
        )}
      </FilterBar>
      <ServerTable
        query={payments}
        table={table}
        onTableChange={setTable}
        minWidth="64rem"
        onRowClick={open}
        empty={
          <ListEmpty filtered={filtered} icon="pi pi-wallet" title={t('console:payments.empty')} />
        }
      >
        <Column
          field="created_at"
          sortable
          header={t('console:payments.date')}
          bodyClassName="sm-nowrap"
          body={(p: ConsolePayment) => formatDateTime(p.created_at, 'fr')}
        />
        <Column
          header={t('console:payments.company')}
          body={(p: ConsolePayment) => (
            <span>
              <span className="sm-strong">{p.tenant_name}</span>
              <br />
              <small className="sm-muted">{p.plan_code}</small>
            </span>
          )}
        />
        <Column
          field="amount"
          sortable
          header={t('console:payments.amount')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(p: ConsolePayment) => formatMoney(p.amount, p.currency)}
        />
        <Column
          header={t('console:payments.period')}
          bodyClassName="sm-nowrap"
          body={(p: ConsolePayment) =>
            t('console:payments.periodValue', {
              start: paymentDay(p.period_start),
              end: paymentDay(p.period_end),
            })
          }
        />
        <Column
          header={t('console:payments.method')}
          body={(p: ConsolePayment) => t(`subscriptionPaymentMethod.${p.payment_method}`)}
        />
        <Column field="declared_reference" header={t('console:payments.reference')} />
        <Column
          field="status"
          sortable
          header={t('console:payments.status')}
          body={(p: ConsolePayment) => <SubscriptionPaymentStatusBadge status={p.status} />}
        />
        <Column
          header=""
          body={(p: ConsolePayment) => (
            <Button
              icon="pi pi-arrow-right"
              text
              rounded
              aria-label={`${t('console:payments.open')} ${p.declared_reference}`}
              onClick={(e) => {
                e.stopPropagation();
                open(p);
              }}
            />
          )}
        />
      </ServerTable>
    </>
  );
}
