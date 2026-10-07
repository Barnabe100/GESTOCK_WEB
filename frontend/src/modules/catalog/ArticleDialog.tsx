import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { MultiSelect } from 'primereact/multiselect';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { useSuppliers } from '@/modules/suppliers/api';
import { normalizeDecimal } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import {
  ARTICLE_UPDATE,
  ASSORTMENT_MANAGE,
  COST_VIEW,
  PRICE_UPDATE,
  useCategories,
  useLotTracking,
  useSaveArticle,
  type Article,
  type ArticleInput,
} from './api';

export const OPTIONS_QUERY = 'limit=200&status=active&sort=name';

const money = z.string().refine((v) => normalizeDecimal(v, 2) !== null, 'money');
const optionalMoney = z.string().refine((v) => v.trim() === '' || normalizeDecimal(v, 2) !== null);
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
  purchase_price: optionalMoney,
  sale_price: money,
  min_stock: quantity,
  max_stock: optionalQuantity,
  barcode: z.string().max(50),
  description: z.string().max(1000),
  stock_managed: z.boolean(),
  decimal_quantity_allowed: z.boolean(),
  lot_tracked: z.boolean(),
  expiry_tracked: z.boolean(),
  site_ids: z.array(z.string()),
});
type FormValues = z.infer<typeof schema>;

/**
 * Création / modification d'un article. Lot 3-A (ADR-0039) : informations générales
 * (`catalog.article.update`), prix (`catalog.article.price_update`) et prix d'achat visible
 * seulement avec `catalog.article.cost_view` — l'interface n'envoie que ce que l'utilisateur
 * peut modifier ; le serveur revérifie chaque champ.
 */
export function ArticleDialog({
  article,
  onClose,
}: {
  article: Article | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { hasModule, can, capabilities } = useCapabilities();
  const creating = article === null;
  // Recette, étape 1 (D6) : sites dont l'assortiment reçoit l'article dès sa création —
  // facultatif, vide par défaut ; permission revérifiée par le serveur pour chaque site.
  const chooseSites = creating && can(ASSORTMENT_MANAGE) && capabilities.sites.length > 0;
  const canGeneral = creating || can(ARTICLE_UPDATE);
  const canPrices = can(PRICE_UPDATE);
  const canCosts = can(COST_VIEW);
  const showSupplier = hasModule('suppliers') && can('suppliers.supplier.view');
  const categories = useCategories(OPTIONS_QUERY);
  const suppliers = useSuppliers(OPTIONS_QUERY, showSupplier);
  const save = useSaveArticle();
  // Lot 3-G (P1-b) : réglages de suivi proposés seulement s'ils sont activables, ou pour un
  // article déjà suivi (désactivation possible à stock nul).
  const lotTracking = useLotTracking();
  const showLots = lotTracking.data?.available === true || article?.lot_tracked === true;
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      reference: article?.reference ?? '',
      designation: article?.designation ?? '',
      category_id: article?.category_id ?? '',
      unit: article?.unit ?? '',
      main_supplier_id: article?.main_supplier_id ?? null,
      purchase_price: article?.purchase_price ?? '',
      sale_price: article?.sale_price ?? '0',
      min_stock: article?.min_stock ?? '0',
      max_stock: article?.max_stock ?? '',
      barcode: article?.barcode ?? '',
      description: article?.description ?? '',
      stock_managed: article?.stock_managed ?? true,
      decimal_quantity_allowed: article?.decimal_quantity_allowed ?? false,
      lot_tracked: article?.lot_tracked ?? false,
      expiry_tracked: article?.expiry_tracked ?? false,
      site_ids: [],
    },
  });
  const errors = form.formState.errors;
  const stockManaged = useWatch({ control: form.control, name: 'stock_managed' });
  const lotTracked = useWatch({ control: form.control, name: 'lot_tracked' });

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
    const input: ArticleInput = {};
    if (canGeneral) {
      Object.assign(input, {
        reference: values.reference,
        designation: values.designation,
        category_id: values.category_id,
        unit: values.unit,
        min_stock: normalizeDecimal(values.min_stock, 3) ?? '0',
        max_stock: normalizeDecimal(values.max_stock, 3),
        barcode: values.barcode,
        description: values.description,
        stock_managed: values.stock_managed,
        decimal_quantity_allowed: values.decimal_quantity_allowed,
      });
      if (showLots) {
        input.lot_tracked = values.stock_managed && values.lot_tracked;
        input.expiry_tracked = input.lot_tracked && values.expiry_tracked;
      }
      // Sans accès aux fournisseurs, le lien existant n'est pas modifié.
      if (showSupplier) input.main_supplier_id = values.main_supplier_id;
    }
    if (chooseSites && values.site_ids.length > 0) input.site_ids = values.site_ids;
    if (canPrices) {
      input.sale_price = normalizeDecimal(values.sale_price, 2) ?? '0';
      // Prix d'achat : jamais envoyé par qui ne peut pas le voir.
      if (canCosts && values.purchase_price.trim() !== '') {
        input.purchase_price = normalizeDecimal(values.purchase_price, 2) ?? '0';
      }
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
      required={name !== 'barcode'}
      help={help}
      error={errors[name] && t('validation.required')}
    >
      <InputText id={`article-${name}`} readOnly={!canGeneral} {...form.register(name)} />
    </FormField>
  );
  const decimal = (
    name: 'purchase_price' | 'sale_price' | 'min_stock' | 'max_stock',
    label: string,
  ) => {
    const price = name === 'purchase_price' || name === 'sale_price';
    return (
      <FormField
        id={`article-${name}`}
        label={t(label)}
        required={name === 'sale_price' || name === 'min_stock'}
        help={price && !canPrices ? t('articles.priceLocked') : undefined}
        error={errors[name] && t(price ? 'articles.invalidMoney' : 'articles.invalidQuantity')}
      >
        <InputText
          id={`article-${name}`}
          inputMode="decimal"
          readOnly={price ? !canPrices : !canGeneral}
          {...form.register(name)}
        />
      </FormField>
    );
  };

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
            required
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
                  disabled={!canGeneral}
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
                    disabled={!canGeneral}
                    showClear
                    filter
                  />
                )}
              />
            </FormField>
          )}
          {text('barcode', 'articles.barcode')}
          {canCosts && decimal('purchase_price', 'articles.purchasePrice')}
          {decimal('sale_price', 'articles.salePrice')}
        </div>
        <div className="sm-checkbox">
          <Controller
            control={form.control}
            name="stock_managed"
            render={({ field }) => (
              <Checkbox
                inputId="article-stock_managed"
                checked={field.value}
                disabled={!canGeneral}
                onChange={(e) => field.onChange(Boolean(e.checked))}
              />
            )}
          />
          <label htmlFor="article-stock_managed">{t('articles.stockManaged')}</label>
        </div>
        <small className="sm-help">
          {t(stockManaged ? 'articles.stockManagedHelp' : 'articles.notStockManagedHelp')}
        </small>
        <div className="sm-checkbox">
          <Controller
            control={form.control}
            name="decimal_quantity_allowed"
            render={({ field }) => (
              <Checkbox
                inputId="article-decimal_quantity_allowed"
                checked={field.value}
                disabled={!canGeneral}
                onChange={(e) => field.onChange(Boolean(e.checked))}
              />
            )}
          />
          <label htmlFor="article-decimal_quantity_allowed">{t('articles.decimalQuantity')}</label>
        </div>
        <small className="sm-help">{t('articles.decimalQuantityHelp')}</small>
        {stockManaged && showLots && (
          <>
            <div className="sm-checkbox">
              <Controller
                control={form.control}
                name="lot_tracked"
                render={({ field }) => (
                  <Checkbox
                    inputId="article-lot_tracked"
                    checked={field.value}
                    disabled={!canGeneral}
                    onChange={(e) => {
                      field.onChange(Boolean(e.checked));
                      if (!e.checked) form.setValue('expiry_tracked', false);
                    }}
                  />
                )}
              />
              <label htmlFor="article-lot_tracked">{t('lots.lotTracked')}</label>
            </div>
            <div className="sm-checkbox">
              <Controller
                control={form.control}
                name="expiry_tracked"
                render={({ field }) => (
                  <Checkbox
                    inputId="article-expiry_tracked"
                    checked={field.value}
                    disabled={!canGeneral || !lotTracked}
                    onChange={(e) => field.onChange(Boolean(e.checked))}
                  />
                )}
              />
              <label htmlFor="article-expiry_tracked">{t('lots.expiryTracked')}</label>
            </div>
            <small className="sm-help">{t('lots.trackingHelp')}</small>
          </>
        )}
        {stockManaged && !showLots && lotTracking.isSuccess && (
          <small className="sm-help" data-testid="lot-tracking-unavailable">
            {t('lots.trackingUnavailable')}
          </small>
        )}
        {stockManaged && (
          <>
            <div className="sm-form-grid">
              {decimal('min_stock', 'articles.minStock')}
              {decimal('max_stock', 'articles.maxStock')}
            </div>
            <small className="sm-help">{t('articles.thresholdsHelp')}</small>
          </>
        )}
        {chooseSites && (
          <FormField
            id="article-sites"
            label={t('assortment.createSites')}
            help={t('assortment.createSitesHelp')}
          >
            <Controller
              control={form.control}
              name="site_ids"
              render={({ field }) => (
                <MultiSelect
                  inputId="article-sites"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value as string[])}
                  options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
                  placeholder={t('assortment.createSitesNone')}
                  display="chip"
                  showClear
                />
              )}
            />
          </FormField>
        )}
        <FormField id="article-description" label={t('articles.description')}>
          <InputTextarea
            id="article-description"
            rows={3}
            readOnly={!canGeneral}
            {...form.register('description')}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}
