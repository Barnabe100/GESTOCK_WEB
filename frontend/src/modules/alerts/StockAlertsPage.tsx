import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import type { StockLevel } from '@/modules/stock/api';
import { LevelStateTag, thresholdText } from '@/modules/stock/ui';
import { formatQuantity } from '@/shared/lib/decimal';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { MetricCard } from '@/shared/ui/MetricCard';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';
import { FilterBar } from '@/shared/ui/FilterBar';
import { EmptyState, ListEmpty } from '@/shared/ui/EmptyState';

import { useStockAlerts, useStockAlertSummary, type AlertStateFilter } from './api';

const FILTERS: AlertStateFilter[] = ['alerts', 'out', 'low'];

export default function StockAlertsPage() {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'quantity',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [state, setState] = useState<AlertStateFilter>('alerts');
  const debounced = useDebouncedValue(search);
  const summary = useStockAlertSummary();
  const alerts = useStockAlerts(toQueryString(table, { search: debounced, state }));
  const { locale } = capabilities.tenant;
  const showSite = capabilities.site === null && capabilities.sites.length > 1;

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || state !== 'alerts';
  const resetFilters = () => {
    setSearch('');
    setState('alerts');
    resetPage();
  };
  const count = (value: number | undefined) => (value === undefined ? '…' : value);

  return (
    <>
      <PageHeader title={t('alerts.title')} description={t('alerts.subtitle')} />
      <div className="sm-metrics">
        <MetricCard
          icon="pi pi-times-circle"
          tone="danger"
          value={count(summary.data?.out)}
          label={t('stock.states.out')}
          hint={t('alerts.outHint')}
        />
        <MetricCard
          icon="pi pi-exclamation-triangle"
          tone="warning"
          value={count(summary.data?.low)}
          label={t('stock.states.low')}
          hint={t('alerts.lowHint')}
        />
      </div>
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={state}
          onChange={(e) => {
            setState(e.value as AlertStateFilter);
            resetPage();
          }}
          options={FILTERS.map((v) => ({ value: v, label: t(`stock.stateFilter.${v}`) }))}
          aria-label={t('stock.state')}
        />
      </FilterBar>
      <ServerTable
        query={alerts}
        table={table}
        onTableChange={setTable}
        dataKey={(l: StockLevel) => `${l.site_id}:${l.article_id}`}
        empty={
          filtered ? (
            <ListEmpty filtered title={t('alerts.none')} />
          ) : (
            <EmptyState icon="pi pi-check-circle" title={t('alerts.none')} />
          )
        }
      >
        {showSite && (
          <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
        )}
        <Column field="reference" header={t('articles.reference')} sortable />
        <Column field="designation" header={t('articles.designation')} sortable />
        <Column
          field="quantity"
          header={t('stock.quantity')}
          sortable
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(l: StockLevel) => `${formatQuantity(l.quantity, locale)} ${l.unit}`}
        />
        <Column
          header={t('articles.minStock')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(l: StockLevel) => thresholdText(l.min_stock, l.min_override, locale)}
        />
        <Column
          header={t('stock.state')}
          body={(l: StockLevel) => <LevelStateTag state={l.state} />}
        />
      </ServerTable>
    </>
  );
}
