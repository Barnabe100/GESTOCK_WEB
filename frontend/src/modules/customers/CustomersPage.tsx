import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
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
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ActiveTag, StatusFilter } from '@/shared/ui/StatusFilter';

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

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  return (
    <>
      <PageHeader
        title={t('customers.title')}
        actions={
          can('customers.customer.create') && (
            <Button icon="pi pi-plus" label={t('customers.new')} onClick={() => setEditing(null)} />
          )
        }
      />
      <div className="sm-toolbar">
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
      </div>
      {customers.isError ? (
        <ErrorMessage error={customers.error} onRetry={() => void customers.refetch()} />
      ) : (
        <DataTable
          value={customers.data?.items ?? []}
          loading={customers.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={customers.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
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
            body={(c: Customer) => <ActiveTag active={c.is_active} />}
          />
          <Column
            header={t('common.actions')}
            body={(c: Customer) => (
              <div className="sm-row-actions">
                <Button
                  icon="pi pi-eye"
                  text
                  aria-label={t('customers.view')}
                  tooltip={t('customers.view')}
                  onClick={() => void navigate(`/customers/${c.id}`)}
                />
                {can('customers.customer.update') && (
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label={t('actions.edit')}
                    tooltip={t('actions.edit')}
                    onClick={() => setEditing(c)}
                  />
                )}
                {can('customers.customer.status') && (
                  <Button
                    icon={c.is_active ? 'pi pi-ban' : 'pi pi-check'}
                    text
                    severity={c.is_active ? 'danger' : undefined}
                    aria-label={t(c.is_active ? 'actions.deactivate' : 'actions.activate')}
                    tooltip={t(c.is_active ? 'actions.deactivate' : 'actions.activate')}
                    onClick={() => toggle(c)}
                  />
                )}
              </div>
            )}
          />
        </DataTable>
      )}
      {editing !== undefined && (
        <CustomerDialog customer={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
