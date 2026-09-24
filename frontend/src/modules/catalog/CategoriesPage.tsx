import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
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

import { useCategories, useSaveCategory, useSetCategoryActive, type Category } from './api';

const schema = z.object({ name: z.string().trim().min(1).max(100) });

function CategoryDialog({ category, onClose }: { category: Category | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveCategory();
  const form = useForm<{ name: string }>({
    resolver: zodResolver(schema),
    defaultValues: { name: category?.name ?? '' },
  });

  const onSubmit = form.handleSubmit(({ name }) =>
    save.mutate(
      { id: category?.id, name },
      {
        onSuccess: () => {
          toast.success(t(category ? 'categories.updated' : 'categories.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={t(category ? 'categories.edit' : 'categories.new')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="category-name"
          label={t('categories.name')}
          error={form.formState.errors.name && t('validation.required')}
        >
          <InputText id="category-name" {...form.register('name')} autoFocus />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export default function CategoriesPage() {
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
  const [editing, setEditing] = useState<Category | null | undefined>(undefined);
  const debounced = useDebouncedValue(search);
  const categories = useCategories(toQueryString(table, { search: debounced, status }));
  const setActive = useSetCategoryActive();

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  const toggle = (c: Category) =>
    setActive.mutate(
      { id: c.id, active: !c.is_active },
      {
        onSuccess: () => toast.success(t('categories.statusChanged')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <>
      <PageHeader
        title={t('categories.title')}
        actions={
          can('catalog.category.create') && (
            <Button
              icon="pi pi-plus"
              label={t('categories.new')}
              onClick={() => setEditing(null)}
            />
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
      {categories.isError ? (
        <ErrorMessage error={categories.error} onRetry={() => void categories.refetch()} />
      ) : (
        <DataTable
          value={categories.data?.items ?? []}
          loading={categories.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={categories.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
        >
          <Column field="name" header={t('categories.name')} sortable />
          <Column
            header={t('categories.status')}
            body={(c: Category) => <ActiveTag active={c.is_active} />}
          />
          <Column
            header={t('common.actions')}
            body={(c: Category) => (
              <div className="sm-row-actions">
                {can('catalog.category.update') && (
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label={t('actions.edit')}
                    onClick={() => setEditing(c)}
                  />
                )}
                {can('catalog.category.status') && (
                  <Button
                    icon={c.is_active ? 'pi pi-ban' : 'pi pi-check'}
                    text
                    aria-label={t(c.is_active ? 'actions.deactivate' : 'actions.activate')}
                    onClick={() => toggle(c)}
                  />
                )}
              </div>
            )}
          />
        </DataTable>
      )}
      {editing !== undefined && (
        <CategoryDialog category={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
