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

import { SALE_STATUSES, useSales, type Sale, type SaleStatus } from './api';

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

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered =
    search !== '' || status !== null || siteId !== null || dateFrom !== '' || dateTo !== '';
  const resetFilters = () => {
    setSearch('');
    setStatus(null);
    setSiteId(null);
    setDateFrom('');
    setDateTo('');
    resetPage();
  };

  return (
    <>
      <PageHeader
        title={t('sales.title')}
        description={t('sales.subtitle')}
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
      <FilterBar onReset={resetFilters} active={filtered}>
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
        query={sales}
        table={table}
        onTableChange={setTable}
        onRowClick={(s: Sale) => void navigate(`/sales/${s.id}`)}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('sales.empty')}
            action={
              can('sales.sale.create') && (
                <Button
                  icon="pi pi-plus"
                  label={t('sales.new')}
                  outlined
                  onClick={() => void navigate('/sales/new')}
                />
              )
            }
          />
        }
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
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(s: Sale) => formatMoney(s.total, currency, locale)}
        />
        <Column
          header={t('sales.status')}
          body={(s: Sale) => <DocumentStatusBadge labels="sales.statuses" status={s.status} />}
        />
        <Column field="created_by_name" header={t('sales.seller')} />
        <Column
          header={t('common.actions')}
          body={(s: Sale) => {
            const editable = s.status === 'DRAFT' && can('sales.sale.update');
            return (
              <RowActions
                actions={[
                  {
                    key: 'open',
                    label: t(editable ? 'actions.edit' : 'sales.open'),
                    icon: editable ? 'pi pi-pencil' : 'pi pi-eye',
                    onClick: () => void navigate(`/sales/${s.id}`),
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
