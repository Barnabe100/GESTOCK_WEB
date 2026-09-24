import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';

import { SALE_STATUSES, useSales, type Sale, type SaleStatus } from './api';
import { SaleStatusTag } from './ui';

export default function SalesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'number',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<SaleStatus | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debounced = useDebouncedValue(search);
  const sales = useSales(
    toQueryString(table, {
      search: debounced,
      status,
      site_id: siteId,
      date_from: dateFrom,
      date_to: dateTo,
    }),
  );
  const { currency, locale } = capabilities.tenant;
  const multiSite = capabilities.site === null && capabilities.sites.length > 1;

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  return (
    <>
      <PageHeader
        title={t('sales.title')}
        actions={
          can('sales.sale.create') && (
            <Button
              icon="pi pi-plus"
              label={t('sales.new')}
              onClick={() => void navigate('/sales/new')}
            />
          )
        }
      />
      <div className="sm-toolbar">
        <SearchInput
          value={search}
          placeholder={t('sales.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={status}
          onChange={(e) => {
            setStatus((e.value as SaleStatus | undefined) ?? null);
            resetPage();
          }}
          options={SALE_STATUSES.map((v) => ({ value: v, label: t(`sales.statuses.${v}`) }))}
          placeholder={t('sales.allStatuses')}
          showClear
          aria-label={t('sales.status')}
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
        <InputText
          type="date"
          value={dateFrom}
          aria-label={t('stock.dateFrom')}
          title={t('stock.dateFrom')}
          onChange={(e) => {
            setDateFrom(e.target.value);
            resetPage();
          }}
        />
        <InputText
          type="date"
          value={dateTo}
          aria-label={t('stock.dateTo')}
          title={t('stock.dateTo')}
          onChange={(e) => {
            setDateTo(e.target.value);
            resetPage();
          }}
        />
      </div>
      {sales.isError ? (
        <ErrorMessage error={sales.error} onRetry={() => void sales.refetch()} />
      ) : (
        <DataTable
          value={sales.data?.items ?? []}
          loading={sales.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={sales.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
        >
          <Column field="number" header={t('sales.number')} sortable bodyClassName="sm-nowrap" />
          <Column
            field="sale_date"
            header={t('sales.date')}
            sortable
            body={(s: Sale) => formatDate(s.sale_date, locale, 'UTC')}
          />
          {multiSite && <Column field="site_name" header={t('layout.site')} />}
          <Column
            header={t('sales.customer')}
            body={(s: Sale) => s.customer_name ?? t('sales.anonymousShort')}
          />
          <Column
            field="total"
            header={t('sales.total')}
            sortable
            bodyClassName="sm-nowrap"
            body={(s: Sale) => formatMoney(s.total, currency, locale)}
          />
          <Column
            header={t('sales.status')}
            body={(s: Sale) => <SaleStatusTag status={s.status} />}
          />
          <Column field="created_by_name" header={t('sales.seller')} />
          <Column
            header={t('common.actions')}
            body={(s: Sale) => (
              <Button
                icon={
                  s.status === 'DRAFT' && can('sales.sale.update') ? 'pi pi-pencil' : 'pi pi-eye'
                }
                text
                aria-label={t('sales.open')}
                tooltip={t('sales.open')}
                onClick={() => void navigate(`/sales/${s.id}`)}
              />
            )}
          />
        </DataTable>
      )}
    </>
  );
}
