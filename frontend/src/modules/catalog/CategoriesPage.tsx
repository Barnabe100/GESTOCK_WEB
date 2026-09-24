import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
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
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { ActiveBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';
import { ServerTable } from '@/shared/ui/ServerTable';
import { FilterBar } from '@/shared/ui/FilterBar';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';
import { confirmAction } from '@/shared/ui/confirm';

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
          required
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

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || status !== 'all';
  const resetFilters = () => {
    setSearch('');
    setStatus('all');
    resetPage();
  };

  // Désactivation : action sensible, confirmée ; réactivation directe.
  const toggle = (c: Category) => {
    const run = () =>
      setActive.mutate(
        { id: c.id, active: !c.is_active },
        {
          onSuccess: () => toast.success(t('categories.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!c.is_active) return run();
    confirmAction(t, {
      header: t('categories.deactivateTitle'),
      message: t('categories.deactivateConfirm', { name: c.name }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <>
      <PageHeader
        title={t('categories.title')}
        description={t('categories.subtitle')}
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
        query={categories}
        table={table}
        onTableChange={setTable}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('categories.empty')}
            action={
              can('catalog.category.create') && (
                <Button
                  icon="pi pi-plus"
                  label={t('categories.new')}
                  outlined
                  onClick={() => setEditing(null)}
                />
              )
            }
          />
        }
      >
        <Column field="name" header={t('categories.name')} sortable />
        <Column
          header={t('categories.status')}
          body={(c: Category) => <ActiveBadge active={c.is_active} />}
        />
        <Column
          header={t('common.actions')}
          body={(c: Category) => (
            <RowActions
              actions={[
                {
                  key: 'edit',
                  label: t('actions.edit'),
                  icon: 'pi pi-pencil',
                  onClick: () => setEditing(c),
                  hidden: !can('catalog.category.update'),
                },
                {
                  key: 'status',
                  label: t(c.is_active ? 'actions.deactivate' : 'actions.activate'),
                  icon: c.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                  danger: c.is_active,
                  onClick: () => toggle(c),
                  hidden: !can('catalog.category.status'),
                },
              ]}
            />
          )}
        />
      </ServerTable>
      {editing !== undefined && (
        <CategoryDialog category={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
