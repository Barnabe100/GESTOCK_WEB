import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
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

import type { DocumentStatus } from './api';
import { TRANSFERS_FEATURE, useTransfers, type StockTransfer } from './transferApi';
import { DocumentStatusTag } from './ui';

const STATUSES: DocumentStatus[] = ['DRAFT', 'VALIDATED', 'CANCELLED'];

export default function TransfersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'number',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [destination, setDestination] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debounced = useDebouncedValue(search);
  const transfers = useTransfers(
    toQueryString(table, {
      search: debounced,
      status,
      source_site_id: source,
      destination_site_id: destination,
      date_from: dateFrom,
      date_to: dateTo,
    }),
  );
  const { locale } = capabilities.tenant;
  const featureActive = capabilities.features.includes(TRANSFERS_FEATURE);
  const siteOptions = capabilities.sites.map((s) => ({ value: s.id, label: s.name }));

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const siteFilter = (
    value: string | null,
    set: (v: string | null) => void,
    placeholder: string,
  ) => (
    <Dropdown
      value={value}
      onChange={(e) => {
        set((e.value as string | undefined) ?? null);
        resetPage();
      }}
      options={siteOptions}
      placeholder={placeholder}
      aria-label={placeholder}
      showClear
    />
  );

  return (
    <>
      <PageHeader
        title={t('transfers.title')}
        actions={
          featureActive &&
          can('stock.transfer.create') && (
            <Button
              icon="pi pi-plus"
              label={t('transfers.new')}
              onClick={() => void navigate('/stock/transfers/new')}
            />
          )
        }
      />
      {!featureActive && (
        <Message severity="info" className="sm-block" text={t('transfers.readOnlyPlan')} />
      )}
      <div className="sm-toolbar">
        <SearchInput
          value={search}
          placeholder={t('transfers.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={status}
          onChange={(e) => {
            setStatus((e.value as DocumentStatus | undefined) ?? null);
            resetPage();
          }}
          options={STATUSES.map((v) => ({ value: v, label: t(`stock.documentStatus.${v}`) }))}
          placeholder={t('stock.allStatuses')}
          aria-label={t('stock.status')}
          showClear
        />
        {siteFilter(source, setSource, t('transfers.allSources'))}
        {siteFilter(destination, setDestination, t('transfers.allDestinations'))}
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
      {transfers.isError ? (
        <ErrorMessage error={transfers.error} onRetry={() => void transfers.refetch()} />
      ) : (
        <DataTable
          value={transfers.data?.items ?? []}
          loading={transfers.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={transfers.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
        >
          <Column field="number" header={t('stock.number')} sortable bodyClassName="sm-nowrap" />
          <Column
            field="operation_date"
            header={t('stock.date')}
            sortable
            body={(r: StockTransfer) => formatDate(r.operation_date, locale, 'UTC')}
          />
          <Column field="source_site_name" header={t('transfers.source')} />
          <Column field="destination_site_name" header={t('transfers.destination')} />
          <Column field="line_count" header={t('transfers.articleCount')} />
          <Column
            header={t('stock.status')}
            body={(r: StockTransfer) => <DocumentStatusTag status={r.status} />}
          />
          <Column field="created_by_name" header={t('stock.createdBy')} />
          <Column
            header={t('common.actions')}
            body={(r: StockTransfer) => (
              <Button
                icon={
                  r.status === 'DRAFT' && can('stock.transfer.update')
                    ? 'pi pi-pencil'
                    : 'pi pi-eye'
                }
                text
                aria-label={t('transfers.open')}
                tooltip={t('transfers.open')}
                onClick={() => void navigate(`/stock/transfers/${r.id}`)}
              />
            )}
          />
        </DataTable>
      )}
    </>
  );
}
