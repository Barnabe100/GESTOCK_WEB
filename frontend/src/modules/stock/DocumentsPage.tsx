import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
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
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { DocumentStatusBadge } from '@/shared/ui/StatusBadge';
import { ServerTable } from '@/shared/ui/ServerTable';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';

import {
  DOCUMENT_CONFIG,
  useDocuments,
  type DocumentKind,
  type DocumentStatus,
  type StockDocument,
  type StockEntry,
  type StockExit,
} from './api';

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

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || status !== null || dateFrom !== '' || dateTo !== '';
  const resetFilters = () => {
    setSearch('');
    setStatus(null);
    setDateFrom('');
    setDateTo('');
    resetPage();
  };
  const open = (d: StockDocument) => void navigate(`/stock/${kind}/${d.id}`);

  return (
    <>
      <PageHeader
        title={t(`${config.i18n}.title`)}
        description={t(`${config.i18n}.subtitle`)}
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
      <FilterBar onReset={resetFilters} active={filtered}>
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
      <ServerTable
        query={documents}
        table={table}
        onTableChange={setTable}
        onRowClick={open}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t(`${config.i18n}.empty`)}
            action={
              can(`${config.permission}.create`) && (
                <Button
                  icon="pi pi-plus"
                  label={t(`${config.i18n}.new`)}
                  outlined
                  onClick={() => void navigate(`/stock/${kind}/new`)}
                />
              )
            }
          />
        }
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
        <Column
          field="line_count"
          header={t('stock.lines')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
        />
        <Column
          header={t('stock.total')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(d: StockDocument) => formatMoney(d.total_amount, currency, locale)}
        />
        <Column
          header={t('stock.status')}
          body={(d: StockDocument) => <DocumentStatusBadge status={d.status} />}
        />
        <Column
          header={t('common.actions')}
          body={(d: StockDocument) => (
            <RowActions
              actions={[
                { key: 'open', label: t('stock.open'), icon: 'pi pi-eye', onClick: () => open(d) },
              ]}
            />
          )}
        />
      </ServerTable>
    </>
  );
}

export function EntriesPage() {
  return <DocumentsPage kind="entries" />;
}

export function ExitsPage() {
  return <DocumentsPage kind="exits" />;
}
