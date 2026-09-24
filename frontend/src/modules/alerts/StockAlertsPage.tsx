import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
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
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';

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

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  return (
    <>
      <PageHeader title={t('alerts.title')} />
      <div className="sm-grid sm-block">
        <Card title={t('stock.states.out')}>
          <p className="sm-kpi sm-kpi-danger">{summary.data?.out ?? '…'}</p>
        </Card>
        <Card title={t('stock.states.low')}>
          <p className="sm-kpi sm-kpi-warning">{summary.data?.low ?? '…'}</p>
        </Card>
      </div>
      <div className="sm-toolbar">
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
      </div>
      {alerts.isError ? (
        <ErrorMessage error={alerts.error} onRetry={() => void alerts.refetch()} />
      ) : (
        <DataTable
          value={alerts.data?.items ?? []}
          loading={alerts.isFetching}
          dataKey={(l: StockLevel) => `${l.site_id}:${l.article_id}`}
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={alerts.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('alerts.none')}
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
            body={(l: StockLevel) => `${formatQuantity(l.quantity, locale)} ${l.unit}`}
          />
          <Column
            header={t('articles.minStock')}
            body={(l: StockLevel) => thresholdText(l.min_stock, l.min_override, locale)}
          />
          <Column
            header={t('stock.state')}
            body={(l: StockLevel) => <LevelStateTag state={l.state} />}
          />
        </DataTable>
      )}
    </>
  );
}
