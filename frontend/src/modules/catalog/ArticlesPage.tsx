import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { Dropdown } from 'primereact/dropdown';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type StatusFilterValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { useCreateRequest } from '@/shared/lib/useCreateRequest';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { StatusFilter } from '@/shared/ui/StatusFilter';
import { ActiveBadge, StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';
import { ServerTable } from '@/shared/ui/ServerTable';
import { FilterBar } from '@/shared/ui/FilterBar';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { RowActions } from '@/shared/ui/RowActions';
import { confirmAction } from '@/shared/ui/confirm';
import { SupplierFilter } from '@/modules/suppliers/SupplierFilter';

import {
  ARTICLE_UPDATE,
  PRICE_UPDATE,
  useArticles,
  useCategories,
  useSetArticleActive,
  type Article,
} from './api';
import { ArticleDialog, OPTIONS_QUERY } from './ArticleDialog';

export default function ArticlesPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'reference',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [categoryId, setCategoryId] = useState<string | null>(null);
  // Lot 3-E : filtre « fournisseur principal ».
  const [supplierId, setSupplierId] = useState<string | null>(null);
  const [createRequested, clearCreate] = useCreateRequest(can('catalog.article.create'));
  const [editing, setEditing] = useState<Article | null | undefined>(
    createRequested ? null : undefined,
  );
  const debounced = useDebouncedValue(search);
  const categories = useCategories(OPTIONS_QUERY);
  const articles = useArticles(
    toQueryString(table, {
      search: debounced,
      status,
      category_id: categoryId,
      supplier_id: supplierId,
    }),
  );
  const setActive = useSetArticleActive();
  const { currency, locale } = capabilities.tenant;

  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));
  const filtered = search !== '' || status !== 'all' || categoryId !== null || supplierId !== null;
  const resetFilters = () => {
    setSearch('');
    setStatus('all');
    setCategoryId(null);
    setSupplierId(null);
    resetPage();
  };

  // Désactivation : action sensible, confirmée ; réactivation directe.
  const toggle = (a: Article) => {
    const run = () =>
      setActive.mutate(
        { id: a.id, active: !a.is_active },
        {
          onSuccess: () => toast.success(t('articles.statusChanged')),
          onError: (error) => toast.error(translateError(t, error)),
        },
      );
    if (!a.is_active) return run();
    confirmAction(t, {
      header: t('articles.deactivateTitle'),
      message: t('articles.deactivateConfirm', { name: a.designation }),
      acceptLabel: t('actions.deactivate'),
      danger: true,
      onAccept: run,
    });
  };

  return (
    <>
      <PageHeader
        title={t('articles.title')}
        description={t('articles.subtitle')}
        actions={
          can('catalog.article.create') && (
            <Button icon="pi pi-plus" label={t('articles.new')} onClick={() => setEditing(null)} />
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
        <Dropdown
          value={categoryId}
          onChange={(e) => {
            setCategoryId((e.value as string | undefined) ?? null);
            resetPage();
          }}
          options={(categories.data?.items ?? []).map((c) => ({ value: c.id, label: c.name }))}
          placeholder={t('articles.allCategories')}
          showClear
          aria-label={t('articles.category')}
          filter
        />
        <SupplierFilter
          value={supplierId}
          label={t('articles.supplier')}
          onChange={(value) => {
            setSupplierId(value);
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
        query={articles}
        table={table}
        onTableChange={setTable}
        onRowClick={(a: Article) => void navigate(`/catalog/articles/${a.id}`)}
        empty={
          <ListEmpty
            filtered={filtered}
            title={t('articles.empty')}
            action={
              can('catalog.article.create') && (
                <Button
                  icon="pi pi-plus"
                  label={t('articles.new')}
                  outlined
                  onClick={() => setEditing(null)}
                />
              )
            }
          />
        }
      >
        <Column field="reference" header={t('articles.reference')} sortable />
        <Column field="designation" header={t('articles.designation')} sortable />
        <Column
          field="category_name"
          sortField="category"
          header={t('articles.category')}
          sortable
        />
        <Column field="unit" header={t('articles.unit')} />
        <Column
          field="sale_price"
          header={t('articles.salePrice')}
          sortable
          body={(a: Article) => formatMoney(a.sale_price, currency, locale)}
        />
        <Column
          header={t('articles.stock')}
          body={(a: Article) =>
            a.stock_managed ? (
              t('articles.stockManagedShort')
            ) : (
              <StatusBadge tone="info" label={t('articles.notStockManaged')} />
            )
          }
        />
        <Column
          header={t('articles.status')}
          body={(a: Article) => <ActiveBadge active={a.is_active} />}
        />
        <Column
          header={t('common.actions')}
          body={(a: Article) => (
            <RowActions
              actions={[
                {
                  key: 'open',
                  label: t('articles.open'),
                  icon: 'pi pi-eye',
                  onClick: () => void navigate(`/catalog/articles/${a.id}`),
                },
                {
                  key: 'edit',
                  label: t('actions.edit'),
                  icon: 'pi pi-pencil',
                  onClick: () => setEditing(a),
                  hidden: !can(ARTICLE_UPDATE) && !can(PRICE_UPDATE),
                },
                {
                  key: 'status',
                  label: t(a.is_active ? 'actions.deactivate' : 'actions.activate'),
                  icon: a.is_active ? 'pi pi-ban' : 'pi pi-check-circle',
                  danger: a.is_active,
                  onClick: () => toggle(a),
                  hidden: !can('catalog.article.status'),
                },
              ]}
            />
          )}
        />
      </ServerTable>
      {editing !== undefined && (
        <ArticleDialog
          article={editing}
          onClose={() => {
            setEditing(undefined);
            clearCreate();
          }}
        />
      )}
    </>
  );
}
