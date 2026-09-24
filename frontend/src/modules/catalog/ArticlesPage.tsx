import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useSuppliers } from '@/modules/suppliers/api';
import { formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
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

import {
  useArticles,
  useCategories,
  useSaveArticle,
  useSetArticleActive,
  type Article,
  type ArticleInput,
} from './api';

const OPTIONS_QUERY = 'limit=200&status=active&sort=name';

const money = z.string().refine((v) => normalizeDecimal(v, 2) !== null, 'money');
const quantity = z.string().refine((v) => normalizeDecimal(v, 3) !== null, 'quantity');
const optionalQuantity = z
  .string()
  .refine((v) => v.trim() === '' || normalizeDecimal(v, 3) !== null, 'quantity');

const schema = z.object({
  reference: z.string().trim().min(1).max(50),
  designation: z.string().trim().min(1).max(255),
  category_id: z.string().min(1),
  unit: z.string().trim().min(1).max(20),
  main_supplier_id: z.string().nullable(),
  purchase_price: money,
  sale_price: money,
  min_stock: quantity,
  max_stock: optionalQuantity,
  barcode: z.string().max(50),
  description: z.string().max(1000),
});
type FormValues = z.infer<typeof schema>;

function ArticleDialog({ article, onClose }: { article: Article | null; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { hasModule, can } = useCapabilities();
  const showSupplier = hasModule('suppliers') && can('suppliers.supplier.view');
  const categories = useCategories(OPTIONS_QUERY);
  const suppliers = useSuppliers(OPTIONS_QUERY, showSupplier);
  const save = useSaveArticle();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      reference: article?.reference ?? '',
      designation: article?.designation ?? '',
      category_id: article?.category_id ?? '',
      unit: article?.unit ?? '',
      main_supplier_id: article?.main_supplier_id ?? null,
      purchase_price: article?.purchase_price ?? '',
      sale_price: article?.sale_price ?? '',
      min_stock: article?.min_stock ?? '0',
      max_stock: article?.max_stock ?? '',
      barcode: article?.barcode ?? '',
      description: article?.description ?? '',
    },
  });
  const errors = form.formState.errors;

  // Une association existante à une catégorie devenue inactive reste affichée (ART-10).
  const categoryOptions = (categories.data?.items ?? []).map((c) => ({
    value: c.id,
    label: c.name,
  }));
  if (article && !categoryOptions.some((o) => o.value === article.category_id)) {
    categoryOptions.push({
      value: article.category_id,
      label: `${article.category_name} ${t('articles.inactiveCategory')}`,
    });
  }
  const supplierOptions = (suppliers.data?.items ?? []).map((s) => ({
    value: s.id,
    label: s.name,
  }));
  if (
    article?.main_supplier_id &&
    !supplierOptions.some((o) => o.value === article.main_supplier_id)
  ) {
    supplierOptions.push({
      value: article.main_supplier_id,
      label: article.main_supplier_name ?? '…',
    });
  }

  const onSubmit = form.handleSubmit((values) => {
    const input: ArticleInput = {
      ...values,
      purchase_price: normalizeDecimal(values.purchase_price, 2) ?? '0',
      sale_price: normalizeDecimal(values.sale_price, 2) ?? '0',
      min_stock: normalizeDecimal(values.min_stock, 3) ?? '0',
      max_stock: normalizeDecimal(values.max_stock, 3),
    };
    if (!showSupplier) {
      // Sans accès aux fournisseurs, le lien existant n'est pas modifié.
      delete (input as Partial<ArticleInput>).main_supplier_id;
    }
    save.mutate(
      { id: article?.id, input },
      {
        onSuccess: () => {
          toast.success(t(article ? 'articles.updated' : 'articles.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  });

  const text = (
    name: 'reference' | 'designation' | 'unit' | 'barcode',
    label: string,
    help?: string,
  ) => (
    <FormField
      id={`article-${name}`}
      label={t(label)}
      help={help}
      error={errors[name] && t('validation.required')}
    >
      <InputText id={`article-${name}`} {...form.register(name)} />
    </FormField>
  );
  const decimal = (
    name: 'purchase_price' | 'sale_price' | 'min_stock' | 'max_stock',
    label: string,
  ) => (
    <FormField
      id={`article-${name}`}
      label={t(label)}
      error={
        errors[name] &&
        t(
          name === 'purchase_price' || name === 'sale_price'
            ? 'articles.invalidMoney'
            : 'articles.invalidQuantity',
        )
      }
    >
      <InputText id={`article-${name}`} inputMode="decimal" {...form.register(name)} />
    </FormField>
  );

  return (
    <Dialog
      header={article ? `${t('articles.edit')} — ${article.reference}` : t('articles.new')}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <div className="sm-form-grid">
          {text('reference', 'articles.reference')}
          {text('designation', 'articles.designation')}
          <FormField
            id="article-category"
            label={t('articles.category')}
            error={errors.category_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="category_id"
              render={({ field }) => (
                <Dropdown
                  inputId="article-category"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value)}
                  options={categoryOptions}
                  filter
                />
              )}
            />
          </FormField>
          {text('unit', 'articles.unit', t('articles.unitHelp'))}
          {showSupplier && (
            <FormField id="article-supplier" label={t('articles.supplier')}>
              <Controller
                control={form.control}
                name="main_supplier_id"
                render={({ field }) => (
                  <Dropdown
                    inputId="article-supplier"
                    value={field.value}
                    onChange={(e) => field.onChange((e.value as string | undefined) ?? null)}
                    options={supplierOptions}
                    placeholder={t('articles.noSupplier')}
                    showClear
                    filter
                  />
                )}
              />
            </FormField>
          )}
          {text('barcode', 'articles.barcode')}
          {decimal('purchase_price', 'articles.purchasePrice')}
          {decimal('sale_price', 'articles.salePrice')}
          {decimal('min_stock', 'articles.minStock')}
          {decimal('max_stock', 'articles.maxStock')}
        </div>
        <small className="sm-help">{t('articles.thresholdsHelp')}</small>
        <FormField id="article-description" label={t('articles.description')}>
          <InputTextarea id="article-description" rows={3} {...form.register('description')} />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export default function ArticlesPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can, capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'reference',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<StatusFilterValue>('all');
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Article | null | undefined>(undefined);
  const debounced = useDebouncedValue(search);
  const categories = useCategories(OPTIONS_QUERY);
  const articles = useArticles(
    toQueryString(table, { search: debounced, status, category_id: categoryId }),
  );
  const setActive = useSetArticleActive();
  const { currency, locale } = capabilities.tenant;

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  const toggle = (a: Article) =>
    setActive.mutate(
      { id: a.id, active: !a.is_active },
      {
        onSuccess: () => toast.success(t('articles.statusChanged')),
        onError: (error) => toast.error(translateError(t, error)),
      },
    );

  return (
    <>
      <PageHeader
        title={t('articles.title')}
        actions={
          can('catalog.article.create') && (
            <Button icon="pi pi-plus" label={t('articles.new')} onClick={() => setEditing(null)} />
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
        <StatusFilter
          value={status}
          onChange={(v) => {
            setStatus(v);
            resetPage();
          }}
        />
      </div>
      {articles.isError ? (
        <ErrorMessage error={articles.error} onRetry={() => void articles.refetch()} />
      ) : (
        <DataTable
          value={articles.data?.items ?? []}
          loading={articles.isFetching}
          dataKey="id"
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={articles.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
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
            header={t('articles.status')}
            body={(a: Article) => <ActiveTag active={a.is_active} />}
          />
          <Column
            header={t('common.actions')}
            body={(a: Article) => (
              <div className="sm-row-actions">
                {can('catalog.article.update') && (
                  <Button
                    icon="pi pi-pencil"
                    text
                    aria-label={t('actions.edit')}
                    onClick={() => setEditing(a)}
                  />
                )}
                {can('catalog.article.status') && (
                  <Button
                    icon={a.is_active ? 'pi pi-ban' : 'pi pi-check'}
                    text
                    aria-label={t(a.is_active ? 'actions.deactivate' : 'actions.activate')}
                    onClick={() => toggle(a)}
                  />
                )}
              </div>
            )}
          />
        </DataTable>
      )}
      {editing !== undefined && (
        <ArticleDialog article={editing} onClose={() => setEditing(undefined)} />
      )}
    </>
  );
}
