import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
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
import { ListEmpty } from '@/shared/ui/EmptyState';
import { DateRangeFilter, FilterBar } from '@/shared/ui/FilterBar';
import { PageHeader } from '@/shared/ui/PageHeader';
import { RowActions } from '@/shared/ui/RowActions';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ServerTable } from '@/shared/ui/ServerTable';

import {
  INVENTORY_STATUSES,
  INVENTORY_TYPES,
  useInventories,
  type Inventory,
  type InventoryStatus,
  type InventoryType,
} from './api';
import { InventoryStatusBadge } from './ui';

export default function InventoriesPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'number',
    sortOrder: -1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<InventoryStatus | null>(null);
  const [type, setType] = useState<InventoryType | null>(null);
  const [site, setSite] = useState<string | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const debounced = useDebouncedValue(search);
  const inventories = useInventories(
    toQueryString(table, {
      search: debounced,
      status,
      inventory_type: type,
      site_id: site,
      date_from: dateFrom,
      date_to: dateTo,
    }),
  );
  const { locale, timezone } = capabilities.tenant;
  const showSites = capabilities.site === null && capabilities.sites.length > 1;
  const canCreate = can('inventory_count.inventory.create');
  const canCount = can('inventory_count.inventory.count');

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered =
    search !== '' ||
    status !== null ||
    type !== null ||
    site !== null ||
    dateFrom !== '' ||
    dateTo !== '';
  const resetFilters = () => {
    setSearch('');
    setStatus(null);
    setType(null);
    setSite(null);
    setDateFrom('');
    setDateTo('');
    resetPage();
  };
  const create = () => void navigate('/inventories/new');
  const open = (r: Inventory) => void navigate(`/inventories/${r.id}`);

  return (
    <>
      <PageHeader
        title={t('inventories.title')}
        description={t('inventories.subtitle')}
        actions={
          canCreate && <Button icon="pi pi-plus" label={t('inventories.new')} onClick={create} />
        }
      />
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('inventories.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        {showSites && (
          <Dropdown
            value={site}
            onChange={(e) => {
              setSite((e.value as string | undefined) ?? null);
              resetPage();
            }}
            options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
            placeholder={t('layout.allSites')}
            aria-label={t('layout.site')}
            showClear
          />
        )}
        <Dropdown
          value={status}
          onChange={(e) => {
            setStatus((e.value as InventoryStatus | undefined) ?? null);
            resetPage();
          }}
          options={INVENTORY_STATUSES.map((v) => ({
            value: v,
            label: t(`inventories.statuses.${v}`),
          }))}
          placeholder={t('stock.allStatuses')}
          aria-label={t('stock.status')}
          showClear
        />
        <Dropdown
          value={type}
          onChange={(e) => {
            setType((e.value as InventoryType | undefined) ?? null);
            resetPage();
          }}
          options={INVENTORY_TYPES.map((v) => ({ value: v, label: t(`inventories.types.${v}`) }))}
          placeholder={t('inventories.allTypes')}
          aria-label={t('inventories.type')}
          showClear
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
        query={inventories}
        table={table}
        onTableChange={setTable}
        minWidth="56rem"
        onRowClick={open}
        empty={
          <ListEmpty
            filtered={filtered}
            icon="pi pi-clipboard"
            title={t('inventories.empty')}
            action={
              canCreate && (
                <Button icon="pi pi-plus" label={t('inventories.new')} outlined onClick={create} />
              )
            }
          />
        }
      >
        <Column field="number" header={t('stock.number')} sortable bodyClassName="sm-nowrap" />
        {showSites && <Column field="site_name" header={t('layout.site')} />}
        <Column
          field="created_at"
          header={t('stock.date')}
          sortable
          body={(r: Inventory) => formatDate(r.created_at, locale, timezone)}
        />
        <Column
          header={t('inventories.type')}
          body={(r: Inventory) => t(`inventories.types.${r.inventory_type}`)}
        />
        <Column
          header={t('inventories.articles')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(r: Inventory) =>
            r.status === 'DRAFT' ? r.line_count : `${r.counted_count} / ${r.line_count}`
          }
        />
        <Column
          header={t('inventories.variances')}
          headerClassName="sm-num"
          bodyClassName="sm-num"
          body={(r: Inventory) =>
            r.status === 'DRAFT' || r.status === 'CANCELLED' ? '—' : r.variance_count
          }
        />
        <Column
          header={t('stock.status')}
          body={(r: Inventory) => <InventoryStatusBadge status={r.status} />}
        />
        <Column field="created_by_name" header={t('stock.createdBy')} />
        <Column
          header={t('common.actions')}
          body={(r: Inventory) => {
            const counting = r.status === 'COUNTING' && canCount;
            return (
              <RowActions
                actions={[
                  {
                    key: 'open',
                    label: t(counting ? 'inventories.count' : 'inventories.open'),
                    icon: counting ? 'pi pi-pencil' : 'pi pi-eye',
                    onClick: () => open(r),
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
