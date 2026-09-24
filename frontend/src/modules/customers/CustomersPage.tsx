import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
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

import { CUSTOMER_TYPES, useCustomers, type Customer, type CustomerType } from './api';
import { CustomerDialog } from './CustomerDialog';
import { useCustomerStatus } from './useCustomerStatus';

export default function CustomersPage() {
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
  const [type, setType] = useState<CustomerType | null>(null);
  const [editing, setEditing] = useState<Customer | null | undefined>(undefined);
  const debounced = useDebouncedValue(search);
  const customers = useCustomers(toQueryString(table, { search: debounced, status, type }));
  const { toggle } = useCustomerStatus();

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || status !== 'all' || type !== null;
  const resetFilters = () => {
    setSearch('');
    setStatus('all');
    setType(null);
    resetPage();
  };

  return (
    <>
      <PageHeader
        title={t('customers.title')}
        description={t('customers.subtitle')}
        actions={
          can('customers.customer.create') && (
            <Button icon="pi pi-plus" label={t('customers.new')} onClick={() => setEditing(null)} />
          )
        }
      />
      <FilterBar onReset={resetFilters} active={filtered}>
        <SearchInput
          value={search}
          placeholder={t('customers.search')}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        <Dropdown
          value={type}
          onChange={(e) => {
            setType((e.value as CustomerType | undefined) ?? null);
            resetPage();
          }}
          options={CUSTOMER_TYPES.map((v) => ({ value: v, label: t(`customers.types.${v}`) }))}
          placeholder={t('customers.allTypes')}
          showClear
          aria-label={t('customers.type')}
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
        query={customers}
        table={table}
        onTableChange={setTable}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('customers.empty')}
            action={
              can('customers.customer.create') && (
                <Button
                  icon="pi pi-plus"
                  label={t('customers.new')}
                  outlined
                  onClick={() => setEditing(null)}
                />
              )
            }
          />
        }
      >
        <Column field="code" header={t('customers.code')} sortable bodyClassName="sm-nowrap" />
        <Column
          field="name"
          header={t('customers.nameOrLegal')}
          sortable
          body={(c: Customer) => (
            <div>
              <div>{c.name}</div>
              {c.legal_name && <small className="sm-muted">{c.legal_name}</small>}
            </div>
          )}
        />
        <Column
          header={t('customers.type')}
          body={(c: Customer) => t(`customers.types.${c.customer_type}`)}
        />
        <Column field="phone" header={t('customers.phone')} />
        <Column field="email" header={t('customers.email')} />
        <Column
          header={t('customers.status')}
          body={(c: Customer) => <ActiveBadge active={c.is_active} />}
        />
        <Column
          header={t('common.actions')}
          body={(c: Customer) => (
            <RowActions
              actions={[
                {
                  key: 'view',
                  label: t('customers.view'),
                  icon: 'pi pi-eye',
                  onClick: () => void navigate(`/customers/${c.id}`),
                },
                {
                  key: 'edit',
                  label: t('actions.edit'),
                  icon: 'pi pi-pencil',
                  onClick: () => setEditing(c),
                  hidden: !can('customers.customer.update'),
                },
                {
                  key: 'status',
                  label: t(c.is_active ? 'actions.deactivate' : 'actions.activate'),
                  icon: c.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                  danger: c.is_active,
                  onClick: () => toggle(c),
                  hidden: !can('customers.customer.status'),
                },
              ]}
            />
          )}
        />
      </ServerTable>
      {editing !== undefined && (
        <CustomerDialog customer={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
