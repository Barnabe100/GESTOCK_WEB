import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type StatusFilterValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { ActiveBadge } from '@/shared/ui/StatusBadge';
import { ServerTable } from '@/shared/ui/ServerTable';
import { FilterBar } from '@/shared/ui/FilterBar';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';

import { useSuppliers, type Supplier } from './api';
import { SupplierDialog } from './SupplierDialog';
import { useSupplierStatus } from './useSupplierStatus';

export default function SuppliersPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'name',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [editing, setEditing] = useState<Supplier | null | undefined>(undefined);
  const debounced = useDebouncedValue(search);
  const suppliers = useSuppliers(toQueryString(table, { search: debounced, status }));
  const { toggle } = useSupplierStatus();

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || status !== 'all';
  const resetFilters = () => {
    setSearch('');
    setStatus('all');
    resetPage();
  };
  const open = (s: Supplier) => void navigate(`/suppliers/${s.id}`);

  return (
    <>
      <PageHeader
        title={t('suppliers.title')}
        description={t('suppliers.subtitle')}
        actions={
          can('suppliers.supplier.create') && (
            <Button icon="pi pi-plus" label={t('suppliers.new')} onClick={() => setEditing(null)} />
          )
        }
      />
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <StatusFilter
          value={status}
          onChange={(v) => {
            setStatus(v);
            resetPage();
          }}
        />
      </FilterBar>
      <ServerTable
        query={suppliers}
        table={table}
        onTableChange={setTable}
        onRowClick={open}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('suppliers.empty')}
            action={
              can('suppliers.supplier.create') && (
                <Button
                  icon="pi pi-plus"
                  label={t('suppliers.new')}
                  outlined
                  onClick={() => setEditing(null)}
                />
              )
            }
          />
        }
      >
        <Column field="name" header={t('suppliers.name')} sortable />
        <Column field="contact_name" header={t('suppliers.contact')} />
        <Column field="phone" header={t('suppliers.phone')} />
        <Column field="city" header={t('suppliers.city')} sortable />
        <Column
          header={t('suppliers.status')}
          body={(s: Supplier) => <ActiveBadge active={s.is_active} />}
        />
        <Column
          header={t('common.actions')}
          body={(s: Supplier) => (
            <RowActions
              actions={[
                {
                  key: 'view',
                  label: t('suppliers.view'),
                  icon: 'pi pi-eye',
                  onClick: () => open(s),
                },
                {
                  key: 'edit',
                  label: t('actions.edit'),
                  icon: 'pi pi-pencil',
                  onClick: () => setEditing(s),
                  hidden: !can('suppliers.supplier.update'),
                },
                {
                  key: 'status',
                  label: t(s.is_active ? 'actions.deactivate' : 'actions.activate'),
                  icon: s.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                  danger: s.is_active,
                  onClick: () => toggle(s),
                  hidden: !can('suppliers.supplier.status'),
                },
              ]}
            />
          )}
        />
      </ServerTable>
      {editing !== undefined && (
        <SupplierDialog supplier={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
