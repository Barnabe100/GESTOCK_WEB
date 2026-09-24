import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type StatusFilterValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { ActiveTag, StatusFilter } from '@/shared/ui/StatusFilter';
import { useToast } from '@/shared/ui/toast';

import { useSaveSupplier, useSetSupplierActive, useSuppliers, type Supplier } from './api';

const optional = (max: number) => z.string().max(max);
const schema = z.object({
  name: z.string().trim().min(1).max(150),
  contact_name: optional(150),
  phone: optional(30),
  email: z.union([z.literal(''), z.string().trim().email().max(150)]),
  address: optional(255),
  city: optional(100),
  country: optional(100),
  notes: optional(500),
});
type FormValues = z.infer<typeof schema>;
const FIELDS = ['contact_name', 'phone', 'email', 'address', 'city', 'country'] as const;
const LABELS: Record<(typeof FIELDS)[number], string> = {
  contact_name: 'suppliers.contact',
  phone: 'suppliers.phone',
  email: 'suppliers.email',
  address: 'suppliers.address',
  city: 'suppliers.city',
  country: 'suppliers.country',
};

function SupplierDialog({ supplier, onClose }: { supplier: Supplier | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveSupplier();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: supplier?.name ?? '',
      contact_name: supplier?.contact_name ?? '',
      phone: supplier?.phone ?? '',
      email: supplier?.email ?? '',
      address: supplier?.address ?? '',
      city: supplier?.city ?? '',
      country: supplier?.country ?? '',
      notes: supplier?.notes ?? '',
    },
  });
  const errors = form.formState.errors;

  // Chaîne vide = champ effacé côté serveur.
  const onSubmit = form.handleSubmit((values) =>
    save.mutate(
      { id: supplier?.id, input: values },
      {
        onSuccess: () => {
          toast.success(t(supplier ? 'suppliers.updated' : 'suppliers.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={t(supplier ? 'suppliers.edit' : 'suppliers.new')}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="supplier-name"
          label={t('suppliers.name')}
          error={errors.name && t('validation.required')}
        >
          <InputText id="supplier-name" {...form.register('name')} autoFocus />
        </FormField>
        <div className="sm-form-grid">
          {FIELDS.map((field) => (
            <FormField
              key={field}
              id={`supplier-${field}`}
              label={t(LABELS[field])}
              error={
                errors[field] && t(field === 'email' ? 'validation.email' : 'validation.invalid')
              }
            >
              <InputText id={`supplier-${field}`} {...form.register(field)} />
            </FormField>
          ))}
        </div>
        <FormField
          id="supplier-notes"
          label={t('suppliers.notes')}
          error={errors.notes && t('validation.invalid')}
        >
          <InputTextarea id="supplier-notes" rows={3} {...form.register('notes')} />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export default function SuppliersPage() {
  const { t } = useTranslation();
  const toast = useToast();
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
  const setActive = useSetSupplierActive();

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  const toggle = (s: Supplier) =>
    setActive.mutate(
      { id: s.id, active: !s.is_active },
      {
        onSuccess: () => toast.success(t('suppliers.statusChanged')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <>
      <PageHeader
        title={t('suppliers.title')}
        actions={
          can('suppliers.supplier.create') && (
            <Button icon="pi pi-plus" label={t('suppliers.new')} onClick={() => setEditing(null)} />
          )
        }
      />
      <div className="sm-toolbar">
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
      </div>
      {suppliers.isError ? (
        <ErrorMessage error={suppliers.error} onRetry={() => void suppliers.refetch()} />
      ) : (
        <DataTable
          value={suppliers.data?.items ?? []}
          loading={suppliers.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={suppliers.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
        >
          <Column field="name" header={t('suppliers.name')} sortable />
          <Column field="contact_name" header={t('suppliers.contact')} />
          <Column field="phone" header={t('suppliers.phone')} />
          <Column field="city" header={t('suppliers.city')} sortable />
          <Column
            header={t('suppliers.status')}
            body={(s: Supplier) => <ActiveTag active={s.is_active} />}
          />
          <Column
            header={t('common.actions')}
            body={(s: Supplier) => (
              <div className="sm-row-actions">
                {can('suppliers.supplier.update') && (
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label={t('actions.edit')}
                    onClick={() => setEditing(s)}
                  />
                )}
                {can('suppliers.supplier.status') && (
                  <Button
                    icon={s.is_active ? 'pi pi-ban' : 'pi pi-check'}
                    text
                    aria-label={t(s.is_active ? 'actions.deactivate' : 'actions.activate')}
                    onClick={() => toggle(s)}
                  />
                )}
              </div>
            )}
          />
        </DataTable>
      )}
      {editing !== undefined && (
        <SupplierDialog supplier={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
