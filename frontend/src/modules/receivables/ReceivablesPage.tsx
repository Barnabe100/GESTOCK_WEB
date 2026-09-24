import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { CustomerPicker, type CustomerOption } from '@/modules/sales/CustomerPicker';
import { formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { MetricCard } from '@/shared/ui/MetricCard';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';

import {
  RECEIVABLE_STATUSES,
  useReceivableSummary,
  useReceivables,
  type ReceivableStatus,
} from './api';
import { ReceivablesTable } from './ReceivablesTable';

/**
 * Créances ouvertes : ventes validées dont le reste dû est positif, calculées par le serveur à
 * partir des ventes et des paiements. Consultation seule : l'encaissement se fait sur la fiche
 * de la vente.
 */
export default function ReceivablesPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const multiSite = capabilities.site === null && capabilities.sites.length > 1;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [search, setSearch] = useState('');
  const [customer, setCustomer] = useState<CustomerOption | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [status, setStatus] = useState<ReceivableStatus | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [minAmount, setMinAmount] = useState('');
  const debouncedSearch = useDebouncedValue(search);
  const debouncedMin = useDebouncedValue(minAmount);
  const filters = {
    search: debouncedSearch,
    customer_id: customer?.id,
    site_id: siteId,
    status,
    date_from: dateFrom,
    date_to: dateTo,
    min_amount: normalizeDecimal(debouncedMin, 2),
  };
  const receivables = useReceivables(toQueryString(table, filters));
  const summary = useReceivableSummary(toQueryString({ first: 0, rows: 1 }, filters));

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered =
    search !== '' ||
    customer !== null ||
    siteId !== null ||
    status !== null ||
    dateFrom !== '' ||
    dateTo !== '' ||
    minAmount !== '';
  const resetFilters = () => {
    setSearch('');
    setCustomer(null);
    setSiteId(null);
    setStatus(null);
    setDateFrom('');
    setDateTo('');
    setMinAmount('');
    resetPage();
  };
  const totals = summary.data;

  return (
    <>
      <PageHeader title={t('receivables.title')} description={t('receivables.subtitle')} />
      <div className="sm-metrics" role="group" aria-label={t('receivables.indicators')}>
        <MetricCard
          icon="pi pi-wallet"
          tone="warning"
          value={totals ? formatMoney(totals.total_receivables, currency, locale) : '—'}
          label={t('receivables.totalDue')}
        />
        <MetricCard
          icon="pi pi-file"
          value={totals ? totals.receivables_count : '—'}
          label={t('receivables.openCount')}
        />
        <MetricCard
          icon="pi pi-users"
          value={totals ? totals.debtor_customers_count : '—'}
          label={t('receivables.debtors')}
        />
      </div>
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('receivables.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <CustomerPicker
          id="receivables-customer"
          value={customer}
          includeInactive
          className="sm-article-picker sm-filter-picker"
          placeholder={t('receivables.allCustomers')}
          ariaLabel={t('receivables.customer')}
          onChange={(value) => {
            setCustomer(value);
            resetPage();
          }}
        />
        {multiSite && (
          <Dropdown
            value={siteId}
            onChange={(e) => {
              setSiteId((e.value as string | undefined) ?? null);
              resetPage();
            }}
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            placeholder={t('sales.allSites')}
            showClear
            aria-label={t('layout.site')}
          />
        )}
        <Dropdown
          value={status}
          onChange={(e) => {
            setStatus((e.value as ReceivableStatus | undefined) ?? null);
            resetPage();
          }}
          options={RECEIVABLE_STATUSES.map((v) => ({
            value: v,
            label: t(`sales.paymentStatus.${v}`),
          }))}
          placeholder={t('receivables.allStates')}
          showClear
          aria-label={t('receivables.state')}
        />
        <InputText
          className="sm-filter-amount"
          value={minAmount}
          inputMode="decimal"
          placeholder={t('receivables.minAmount', { currency })}
          aria-label={t('receivables.minAmountLabel')}
          onChange={(e) => {
            setMinAmount(e.target.value);
            resetPage();
          }}
        />
        <DateRangeFilter
          from={dateFrom}
          to={dateTo}
          onChange={({ from, to }) => {
            setDateFrom(from);
            setDateTo(to);
            resetPage();
          }}
        />
      </FilterBar>
      <ReceivablesTable
        query={receivables}
        table={table}
        onTableChange={setTable}
        empty={<ListEmpty filtered={filtered} icon="pi pi-wallet" title={t('receivables.empty')} />}
      />
    </>
  );
}
