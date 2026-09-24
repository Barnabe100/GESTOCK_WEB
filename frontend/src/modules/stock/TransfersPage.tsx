import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
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
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { DocumentStatusBadge } from '@/shared/ui/StatusBadge';
import { ServerTable } from '@/shared/ui/ServerTable';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';

import type { DocumentStatus } from './api';
import { TRANSFERS_FEATURE, useTransfers, type StockTransfer } from './transferApi';

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

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered =
    search !== '' ||
    status !== null ||
    source !== null ||
    destination !== null ||
    dateFrom !== '' ||
    dateTo !== '';
  const resetFilters = () => {
    setSearch('');
    setStatus(null);
    setSource(null);
    setDestination(null);
    setDateFrom('');
    setDateTo('');
    resetPage();
  };
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
        description={t('transfers.subtitle')}
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
      <FilterBar onReset={resetFilters} active={filtered}>
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
        query={transfers}
        table={table}
        onTableChange={setTable}
        onRowClick={(r: StockTransfer) => void navigate(`/stock/transfers/${r.id}`)}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('transfers.empty')}
            action={
              featureActive &&
              can('stock.transfer.create') && (
                <Button
                  icon="pi pi-plus"
                  label={t('transfers.new')}
                  outlined
                  onClick={() => void navigate('/stock/transfers/new')}
                />
              )
            }
          />
        }
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
        <Column
          field="line_count"
          headerClassName="sm-num"
          bodyClassName="sm-num"
          header={t('transfers.articleCount')}
        />
        <Column
          header={t('stock.status')}
          body={(r: StockTransfer) => <DocumentStatusBadge status={r.status} />}
        />
        <Column field="created_by_name" header={t('stock.createdBy')} />
        <Column
          header={t('common.actions')}
          body={(r: StockTransfer) => {
            const editable = r.status === 'DRAFT' && featureActive && can('stock.transfer.update');
            return (
              <RowActions
                actions={[
                  {
                    key: 'open',
                    label: t(editable ? 'actions.edit' : 'transfers.open'),
                    icon: editable ? 'pi pi-pencil' : 'pi pi-eye',
                    onClick: () => void navigate(`/stock/transfers/${r.id}`),
                  },
                ]}
              />
            );
          }}
        />
      </ServerTable>
    </>
  );
}
