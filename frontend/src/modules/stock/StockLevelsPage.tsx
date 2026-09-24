import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { DataTable, type DataTableStateEvent } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Tag } from 'primereact/tag';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useCategories } from '@/modules/catalog/api';
import { formatCost, formatMoney, formatQuantity, normalizeDecimal } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import {
  INITIAL_TABLE,
  toQueryString,
  useDebouncedValue,
  type TableState,
} from '@/shared/lib/serverTable';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SearchInput } from '@/shared/ui/SearchInput';
import { useToast } from '@/shared/ui/toast';

import { useSetThresholds, useStockLevels, type LevelStateFilter, type StockLevel } from './api';
import { LevelStateTag, thresholdText } from './ui';

const STATE_FILTERS: LevelStateFilter[] = ['all', 'alerts', 'out', 'low', 'ok', 'not_stocked'];

const optionalQuantity = z
  .string()
  .refine((v) => v.trim() === '' || normalizeDecimal(v, 3) !== null, 'quantity');
const schema = z.object({ min_stock: optionalQuantity, max_stock: optionalQuantity });
type FormValues = z.infer<typeof schema>;

/** Surcharges de seuils du site ; vide = seuil par défaut de l'article (Q2). */
function ThresholdDialog({ level, onClose }: { level: StockLevel; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSetThresholds();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      min_stock: level.min_override ?? '',
      max_stock: level.max_override ?? '',
    },
  });
  const errors = form.formState.errors;

  const onSubmit = form.handleSubmit((values) =>
    save.mutate(
      {
        siteId: level.site_id,
        articleId: level.article_id,
        min_stock: normalizeDecimal(values.min_stock, 3),
        max_stock: normalizeDecimal(values.max_stock, 3),
      },
      {
        onSuccess: () => {
          toast.success(t('stock.thresholdsSaved'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={`${t('stock.thresholds')} — ${level.reference} · ${level.site_name}`}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <p className="sm-help">{t('stock.thresholdsHelp')}</p>
        <div className="sm-form-grid">
          <FormField
            id="threshold-min"
            label={t('articles.minStock')}
            error={errors.min_stock && t('articles.invalidQuantity')}
          >
            <InputText id="threshold-min" inputMode="decimal" {...form.register('min_stock')} />
          </FormField>
          <FormField
            id="threshold-max"
            label={t('articles.maxStock')}
            error={errors.max_stock && t('articles.invalidQuantity')}
          >
            <InputText id="threshold-max" inputMode="decimal" {...form.register('max_stock')} />
          </FormField>
        </div>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}

export default function StockLevelsPage() {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const [table, setTable] = useState<TableState>({
    ...INITIAL_TABLE,
    sortField: 'reference',
    sortOrder: 1,
  });
  const [search, setSearch] = useState('');
  const [state, setState] = useState<LevelStateFilter>('all');
  const [categoryId, setCategoryId] = useState<string | null>(null);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<StockLevel | null>(null);
  const debounced = useDebouncedValue(search);
  const showCategories = can('catalog.category.view');
  const categories = useCategories('limit=200&status=active&sort=name', showCategories);
  const levels = useStockLevels(
    toQueryString(table, {
      search: debounced,
      state,
      category_id: categoryId,
      include_inactive: includeInactive ? 'true' : null,
    }),
  );
  const { currency, locale } = capabilities.tenant;
  const showSite = capabilities.site === null && capabilities.sites.length > 1;
  const canManage = can('stock.threshold.manage');

  const onPage = (e: DataTableStateEvent) =>
    setTable({ first: e.first, rows: e.rows, sortField: e.sortField, sortOrder: e.sortOrder });
  const resetPage = () => setTable((s) => ({ ...s, first: 0 }));

  return (
    <>
      <PageHeader title={t('stock.levelsTitle')} />
      <div className="sm-toolbar">
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            resetPage();
          }}
        />
        {showCategories && (
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
        )}
        <Dropdown
          value={state}
          onChange={(e) => {
            setState(e.value as LevelStateFilter);
            resetPage();
          }}
          options={STATE_FILTERS.map((v) => ({ value: v, label: t(`stock.stateFilter.${v}`) }))}
          aria-label={t('stock.state')}
        />
        <div className="sm-checkbox">
          <Checkbox
            inputId="include-inactive"
            checked={includeInactive}
            onChange={(e) => {
              setIncludeInactive(Boolean(e.checked));
              resetPage();
            }}
          />
          <label htmlFor="include-inactive">{t('stock.includeInactive')}</label>
        </div>
      </div>
      {levels.isError ? (
        <ErrorMessage error={levels.error} onRetry={() => void levels.refetch()} />
      ) : (
        <DataTable
          value={levels.data?.items ?? []}
          loading={levels.isFetching}
          dataKey={(l: StockLevel) => `${l.site_id}:${l.article_id}`}
          lazy
          paginator
          first={table.first}
          rows={table.rows}
          rowsPerPageOptions={[10, 25, 50, 100]}
          totalRecords={levels.data?.total ?? 0}
          sortField={table.sortField}
          sortOrder={table.sortOrder}
          onPage={onPage}
          onSort={onPage}
          emptyMessage={t('common.noData')}
        >
          {showSite && (
            <Column field="site_name" sortField="site" header={t('layout.site')} sortable />
          )}
          <Column field="reference" header={t('articles.reference')} sortable />
          <Column
            field="designation"
            header={t('articles.designation')}
            sortable
            body={(l: StockLevel) => (
              <div className="sm-tags">
                <span>{l.designation}</span>
                {!l.article_active && <Tag severity="secondary" value={t('common.inactive')} />}
              </div>
            )}
          />
          <Column
            field="category_name"
            sortField="category"
            header={t('articles.category')}
            sortable
          />
          <Column
            field="quantity"
            header={t('stock.quantity')}
            sortable
            body={(l: StockLevel) => `${formatQuantity(l.quantity, locale)} ${l.unit}`}
          />
          <Column
            header={t('stock.averageCost')}
            body={(l: StockLevel) => formatCost(l.average_cost, currency, locale)}
          />
          <Column
            header={t('stock.value')}
            body={(l: StockLevel) => formatMoney(l.stock_value, currency, locale)}
          />
          <Column
            header={t('stock.minMax')}
            body={(l: StockLevel) =>
              `${thresholdText(l.min_stock, l.min_override, locale)} / ${thresholdText(
                l.max_stock,
                l.max_override,
                locale,
              )}`
            }
          />
          <Column
            header={t('stock.state')}
            body={(l: StockLevel) => <LevelStateTag state={l.state} />}
          />
          {canManage && (
            <Column
              header={t('common.actions')}
              body={(l: StockLevel) => (
                <Button
                  icon="pi pi-sliders-h"
                  text
                  aria-label={t('stock.editThresholds')}
                  tooltip={t('stock.editThresholds')}
                  onClick={() => setEditing(l)}
                />
              )}
            />
          )}
        </DataTable>
      )}
      <small className="sm-help">{t('stock.overrideLegend')}</small>
      {editing && <ThresholdDialog level={editing} onClose={() => setEditing(null)} />}
    </>
  );
}
