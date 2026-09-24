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

import {
  DOCUMENT_CONFIG,
  useDocuments,
  type DocumentKind,
  type DocumentStatus,
  type StockDocument,
  type StockEntry,
  type StockExit,
} from './api';
import { DocumentStatusTag } from './ui';

const STATUSES: DocumentStatus[] = ['DRAFT', 'VALIDATED', 'CANCELLED'];

function DocumentsPage({ kind }: { kind: DocumentKind }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const config = DOCUMENT_CONFIG[kind];
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'number',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debounced = useDebouncedValue(search);
  const documents = useDocuments<StockDocument>(
    kind,
    toQueryString(table, { search: debounced, status, date_from: dateFrom, date_to: dateTo }),
  );
  const { currency, locale } = capabilities.tenant;
  const showSite = capabilities.site === null && capabilities.sites.length > 1;

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  return (
    <>
      <PageHeader
        title={t(`${config.i18n}.title`)}
        actions={
          can(`${config.permission}.create`) && (
            <Button
              icon="pi pi-plus"
              label={t(`${config.i18n}.new`)}
              onClick={() => navigate(`/stock/${kind}/new`)}
            />
          )
        }
      />
      <div className="sm-toolbar">
        <SearchInput
          value={search}
          placeholder={t(`${config.i18n}.search`)}
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
          showClear
          aria-label={t('stock.status')}
        />
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
      {documents.isError ? (
        <ErrorMessage error={documents.error} onRetry={() => void documents.refetch()} />
      ) : (
        <DataTable
          value={documents.data?.items ?? []}
          loading={documents.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={documents.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          selectionMode="single"
          onRowClick={(e) => navigate(`/stock/${kind}/${(e.data as StockDocument).id}`)}
          rowClassName={() => 'sm-clickable'}
          emptyMessage={t('common.noData')}
        >
          <Column field="number" header={t('stock.number')} sortable />
          <Column
            field="operation_date"
            header={t('stock.date')}
            sortable
            body={(d: StockDocument) => formatDate(d.operation_date, locale, 'UTC')}
          />
          {showSite && <Column field="site_name" header={t('layout.site')} />}
          {kind === 'entries' ? (
            <Column
              header={t('entries.supplierOrKind')}
              body={(d: StockEntry) =>
                d.kind === 'INITIAL_STOCK' ? t('entries.kinds.INITIAL_STOCK') : d.supplier_name
              }
            />
          ) : (
            <Column header={t('exits.reason')} body={(d: StockExit) => d.reason_label} />
          )}
          <Column field="line_count" header={t('stock.lines')} />
          <Column
            header={t('stock.total')}
            body={(d: StockDocument) => formatMoney(d.total_amount, currency, locale)}
          />
          <Column
            header={t('stock.status')}
            body={(d: StockDocument) => <DocumentStatusTag status={d.status} />}
          />
        </DataTable>
      )}
    </>
  );
}

export function EntriesPage() {
  return <DocumentsPage kind="entries" />;
}

export function ExitsPage() {
  return <DocumentsPage kind="exits" />;
}
