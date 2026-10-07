import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { Controller, useFieldArray, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { z } from 'zod';

import { ApiError } from '@/core/api/client';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { type ScanResult } from '@/modules/catalog/api';
import { AssortmentNotice } from '@/modules/catalog/assortment';
import { BarcodeScanField } from '@/modules/catalog/BarcodeScanField';
import { ArticlePicker, toArticleOption, type ArticleOption } from '@/modules/stock/ArticlePicker';
import { expiredShortages, LineLotsList, type ExpiredShortage } from '@/modules/stock/ui';
import {
  addQuantity,
  formatMoney,
  formatQuantity,
  multiplyMoney,
  normalizeDecimal,
  sumMoney,
} from '@/shared/lib/decimal';
import { formatDate, formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';
import { DocumentStatusBadge } from '@/shared/ui/StatusBadge';

import {
  useSale,
  useSaleMutations,
  type CreditOverride,
  type ImmediatePayment,
  type Sale,
  type SaleInput,
  type SaleLine,
  type ExpiredLotOverride,
} from './api';
import { CustomerPicker, toCustomerOption, type CustomerOption } from './CustomerPicker';
import { PaymentsPanel } from './PaymentsPanel';
import { SaleHistoryPanel, SaleMovementsPanel } from './SaleActivity';
import { PackagingSelect, type SalePackaging } from './PackagingSelect';
import { SalePaymentBadge, saleError, soldQuantity } from './ui';
import { SaleReceiptActions } from './receipt/ReceiptDialog';
import { ValidateSaleDialog } from './ValidateSaleDialog';

const quantity = z.string().refine((v) => {
  const n = normalizeDecimal(v, 3);
  return n !== null && /[1-9]/.test(n);
}, 'quantity');

const schema = z
  .object({
    site_id: z
      .string()
      .nullable()
      .refine((v) => Boolean(v), 'required'),
    sale_date: z.string(),
    customer: z.custom<CustomerOption | null>(),
    notes: z.string().max(500),
    lines: z
      .array(
        z.object({
          article: z.custom<ArticleOption | null>().refine((v) => Boolean(v), 'required'),
          packaging: z.custom<SalePackaging | null>(),
          quantity,
        }),
      )
      .min(1, 'empty'),
  })
  .superRefine((values, ctx) => {
    // Ergonomie seulement : le serveur refait tous les contrôles. Une ligne par présentation
    // (article en unité de base, ou article × conditionnement — Lot 3-B).
    const ids = values.lines.map((l) =>
      l.article ? `${l.article.id}:${l.packaging?.id ?? 'base'}` : undefined,
    );
    ids.forEach((id, index) => {
      if (id && ids.indexOf(id) !== index) {
        ctx.addIssue({ code: 'custom', path: ['lines', index, 'article'], message: 'duplicate' });
      }
    });
  });
type FormValues = z.infer<typeof schema>;

function defaults(sale: Sale | undefined, siteId: string | null): FormValues {
  return {
    site_id: sale?.site_id ?? siteId,
    sale_date: sale?.sale_date ?? '',
    customer:
      sale?.customer_id && sale.customer_code && sale.customer_name
        ? toCustomerOption({
            id: sale.customer_id,
            code: sale.customer_code,
            name: sale.customer_name,
          })
        : null,
    notes: sale?.notes ?? '',
    lines: (sale?.lines ?? []).map((line) => ({
      article: toArticleOption({
        id: line.article_id,
        reference: line.article_reference,
        designation: line.article_designation,
        unit: line.unit,
        // Prix de l'unité de base connu seulement pour une ligne en unité de base.
        sale_price: line.packaging_id ? undefined : line.unit_price,
      }),
      packaging:
        line.packaging_id && line.packaging_name && line.packaging_conversion
          ? {
              id: line.packaging_id,
              name: line.packaging_name,
              conversion: line.packaging_conversion,
              sale_price: line.unit_price,
            }
          : null,
      quantity: line.quantity,
    })),
  };
}

function toInput(values: FormValues, isNew: boolean): SaleInput {
  return {
    ...(isNew ? { site_id: values.site_id } : {}),
    sale_date: values.sale_date || null,
    customer_id: values.customer?.id ?? null,
    notes: values.notes.trim() || null,
    lines: values.lines.map((line) => ({
      article_id: line.article?.id ?? '',
      packaging_id: line.packaging?.id ?? null,
      quantity: normalizeDecimal(line.quantity, 3) ?? '0',
    })),
  };
}

/** Prix unitaire de la présentation : conditionnement, sinon article (indicatif). */
function unitPrice(line: FormValues['lines'][number] | undefined): string | undefined {
  return line?.packaging?.sale_price ?? line?.article?.sale_price;
}

/** Montant de ligne affiché en temps réel (indicatif : le serveur recalcule). */
function lineTotal(line: FormValues['lines'][number] | undefined): string | null {
  const q = normalizeDecimal(line?.quantity ?? '', 3);
  const price = unitPrice(line);
  return price && q ? multiplyMoney(q, price) : null;
}

// --- Saisie d'un brouillon ----------------------------------------------------------------------

function SaleForm({ sale }: { sale: Sale | undefined }) {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { can, capabilities, siteId } = useCapabilities();
  const { save, validate } = useSaleMutations();
  const isNew = sale === undefined;
  const defaultSite =
    siteId ?? (capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: defaults(sale, defaultSite),
  });
  const lines = useFieldArray({ control: form.control, name: 'lines' });
  const watched = useWatch({ control: form.control, name: 'lines' });
  const watchedSite = useWatch({ control: form.control, name: 'site_id' });
  // Recette, étape 1 : site de la vente (assortiment proposé par ce site).
  const saleSite = sale?.site_id ?? watchedSite ?? null;
  const errors = form.formState.errors;
  const { currency, locale } = capabilities.tenant;
  const totals = watched.map((line) => lineTotal(line));
  const displayTotal = sumMoney(totals.filter((v): v is string => v !== null));

  /**
   * Lot 3-D : le scan ajoute directement 1 présentation (article en unité de base ou
   * conditionnement) ; une ligne déjà présente pour cette présentation est incrémentée.
   */
  const onScan = (scan: ScanResult) => {
    const packaging =
      scan.packaging && scan.packaging.sale_price !== null
        ? {
            id: scan.packaging.id,
            name: scan.packaging.name,
            conversion: scan.packaging.conversion,
            sale_price: scan.packaging.sale_price,
          }
        : null;
    const current = form.getValues('lines') ?? [];
    const existing = current.findIndex(
      (l) =>
        l.article?.id === scan.article.id && (l.packaging?.id ?? null) === (packaging?.id ?? null),
    );
    if (existing >= 0) {
      const quantity = normalizeDecimal(current[existing]?.quantity ?? '', 3) ?? '0';
      form.setValue(`lines.${existing}.quantity`, addQuantity(quantity, '1'), {
        shouldDirty: true,
      });
      return;
    }
    lines.append({ article: toArticleOption(scan.article), packaging, quantity: '1' });
  };

  const persist = async (values: FormValues): Promise<Sale> => {
    const saved = await save.mutateAsync({ id: sale?.id, input: toInput(values, isNew) });
    form.reset(defaults(saved, defaultSite));
    if (isNew) void navigate(`/sales/${saved.id}`, { replace: true });
    return saved;
  };

  const onSave = form.handleSubmit(async (values) => {
    try {
      await persist(values);
      toast.success(t('sales.draftSaved'));
    } catch (error) {
      toast.error(saleError(t, error, locale));
    }
  });

  // Validation : confirmation, encaissement immédiat facultatif (le reste dû est une créance).
  const [validating, setValidating] = useState<FormValues | null>(null);
  const [validationError, setValidationError] = useState<{
    message: string;
    overrideAllowed: boolean;
    expiredShortages: ExpiredShortage[];
  } | null>(null);
  const onValidate = form.handleSubmit((values) => {
    setValidationError(null);
    setValidating(values);
  });
  const confirmValidation = async (
    values: FormValues,
    payments: ImmediatePayment[],
    creditOverride: CreditOverride | null,
    expiredLotOverride: ExpiredLotOverride | null = null,
  ) => {
    try {
      const saved = form.formState.isDirty || !sale ? await persist(values) : sale;
      const validated = await validate.mutateAsync({
        id: saved.id,
        payments,
        creditOverride,
        expiredLotOverride,
      });
      setValidating(null);
      toast.success(t('sales.validated', { number: validated.number ?? '' }));
    } catch (error) {
      // Refus (stock, prix, crédit…) : dialogue conservé pour corriger, encaisser ou, si le
      // serveur le permet à cet utilisateur, autoriser le dépassement de la limite de crédit.
      setValidationError({
        message: saleError(t, error, locale, currency),
        overrideAllowed:
          error instanceof ApiError &&
          error.code === 'credit_limit_exceeded' &&
          error.extra.override_allowed === true,
        // Lot 3-H-A (O-1) : lots périmés disponibles, dérogation explicite seulement.
        expiredShortages: expiredShortages(error),
      });
    }
  };

  return (
    <form onSubmit={onSave} className="sm-form" noValidate>
      <div className="sm-form-grid">
        {isNew && siteId === null ? (
          <FormField
            id="sale-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <Dropdown
                  inputId="sale-site"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value)}
                  options={capabilities.sites.map((s) => ({ value: s.id, label: s.name }))}
                  placeholder={t('stock.chooseSite')}
                />
              )}
            />
          </FormField>
        ) : (
          <FormField id="sale-site-ro" label={t('layout.site')}>
            <InputText
              id="sale-site-ro"
              value={sale?.site_name ?? capabilities.sites.find((s) => s.id === siteId)?.name ?? ''}
              disabled
            />
          </FormField>
        )}
        <FormField id="sale-customer" label={t('sales.customer')} help={t('sales.customerHelp')}>
          <Controller
            control={form.control}
            name="customer"
            render={({ field }) => (
              <CustomerPicker id="sale-customer" value={field.value} onChange={field.onChange} />
            )}
          />
        </FormField>
        <FormField id="sale-date" label={t('sales.date')} help={t('stock.dateHelp')}>
          <InputText id="sale-date" type="date" {...form.register('sale_date')} />
        </FormField>
      </div>

      <fieldset className="sm-fieldset">
        <legend>{t('sales.lines')}</legend>
        <BarcodeScanField
          id="sale-scan"
          onScan={onScan}
          reject={(scan) =>
            scan.packaging && scan.packaging.sale_price === null
              ? t('errors:packaging_price_not_set', { packagings: scan.packaging.name })
              : null
          }
        />
        {lines.fields.length === 0 && (
          <p className={errors.lines ? 'p-error' : 'sm-muted'}>{t('sales.noLines')}</p>
        )}
        {lines.fields.map((field, index) => {
          const lineErrors = errors.lines?.[index];
          const article = watched[index]?.article;
          const packaging = watched[index]?.packaging;
          const price = unitPrice(watched[index]);
          return (
            <div key={field.id} className="sm-line">
              <FormField
                id={`line-${index}-article`}
                label={t('stock.article')}
                required
                error={
                  lineErrors?.article &&
                  t(
                    lineErrors.article.message === 'duplicate'
                      ? 'errors:duplicate_article_line'
                      : 'validation.required',
                  )
                }
              >
                <Controller
                  control={form.control}
                  name={`lines.${index}.article`}
                  render={({ field: f }) => (
                    <ArticlePicker
                      id={`line-${index}-article`}
                      siteId={saleSite}
                      value={f.value}
                      onChange={(value) => {
                        f.onChange(value);
                        // Autre article : retour à l'unité de base.
                        form.setValue(`lines.${index}.packaging`, null);
                      }}
                      invalid={Boolean(lineErrors?.article)}
                    />
                  )}
                />
              </FormField>
              {article && saleSite && (
                <AssortmentNotice articleId={article.id} siteIds={[saleSite]} />
              )}
              {article && (
                <FormField id={`line-${index}-packaging`} label={t('sales.presentation')}>
                  <Controller
                    control={form.control}
                    name={`lines.${index}.packaging`}
                    render={({ field: f }) => (
                      <PackagingSelect
                        id={`line-${index}-packaging`}
                        articleId={article.id}
                        unit={article.unit}
                        basePrice={article.sale_price}
                        value={f.value}
                        onChange={f.onChange}
                      />
                    )}
                  />
                </FormField>
              )}
              <FormField
                id={`line-${index}-quantity`}
                label={
                  article
                    ? `${t('stock.quantity')} (${packaging?.name ?? article.unit})`
                    : t('stock.quantity')
                }
                required
                error={lineErrors?.quantity && t('stock.invalidQuantity')}
              >
                <InputText
                  id={`line-${index}-quantity`}
                  inputMode="decimal"
                  {...form.register(`lines.${index}.quantity`)}
                />
              </FormField>
              <div className="sm-line-amounts" aria-live="polite">
                <small className="sm-muted">
                  {price ? `${t('sales.unitPrice')} : ${formatMoney(price, currency, locale)}` : ''}
                </small>
                <strong>
                  {totals[index] ? formatMoney(totals[index], currency, locale) : '—'}
                </strong>
              </div>
              <Button
                type="button"
                icon="pi pi-trash"
                text
                severity="danger"
                aria-label={t('stock.removeLine')}
                onClick={() => lines.remove(index)}
              />
            </div>
          );
        })}
        <div>
          <Button
            type="button"
            icon="pi pi-plus"
            outlined
            label={t('stock.addLine')}
            onClick={() => lines.append({ article: null, packaging: null, quantity: '1' })}
          />
        </div>
      </fieldset>
      <FormField id="sale-notes" label={t('sales.notes')}>
        <InputTextarea id="sale-notes" rows={2} {...form.register('notes')} />
      </FormField>
      <p className="sm-total">
        {t('sales.total')} :{' '}
        <strong data-testid="sale-total">{formatMoney(displayTotal, currency, locale)}</strong>
        <br />
        <small className="sm-muted">{t('sales.totalHelp')}</small>
      </p>
      <div className="sm-dialog-actions">
        <Button
          type="button"
          label={t('actions.back')}
          text
          onClick={() => void navigate('/sales')}
        />
        <Button type="submit" label={t('stock.saveDraft')} outlined loading={save.isPending} />
        {can('sales.sale.validate') && (
          <Button
            type="button"
            icon="pi pi-check"
            label={t('sales.validate')}
            disabled={save.isPending || validate.isPending}
            loading={validate.isPending}
            onClick={() => void onValidate()}
          />
        )}
      </div>
      {validating && (
        <ValidateSaleDialog
          total={displayTotal}
          siteId={validating.site_id ?? sale?.site_id ?? null}
          hasCustomer={validating.customer !== null}
          pending={save.isPending || validate.isPending}
          error={validationError?.message ?? null}
          overrideAllowed={validationError?.overrideAllowed ?? false}
          expiredShortages={validationError?.expiredShortages ?? []}
          onConfirm={(payments, creditOverride, expiredLotOverride) =>
            void confirmValidation(validating, payments, creditOverride, expiredLotOverride)
          }
          onClose={() => setValidating(null)}
        />
      )}
    </form>
  );
}

// --- Consultation ----------------------------------------------------------------------------

function SaleSummary({ sale }: { sale: Sale }) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const at = (name: string | null, date: string | null) =>
    date ? `${name ?? ''} · ${formatDateTime(date, locale, timezone)}` : null;
  const rows: [string, string | null][] = [
    [t('layout.site'), sale.site_name],
    [t('sales.date'), formatDate(sale.sale_date, locale, 'UTC')],
    [
      t('sales.customer'),
      sale.customer_name
        ? `${sale.customer_name} (${sale.customer_code})`
        : t('sales.anonymousShort'),
    ],
    [t('sales.credit'), sale.credit_status ? t(`sales.creditStatus.${sale.credit_status}`) : null],
    [
      t('sales.creditOverride'),
      sale.credit_override_at
        ? t('sales.creditOverrideDetail', {
            amount: formatMoney(sale.credit_override_amount ?? '0', currency, locale),
            name: sale.credit_override_by_name ?? '',
            date: formatDateTime(sale.credit_override_at, locale, timezone),
            reason: sale.credit_override_reason ?? '',
          })
        : null,
    ],
    [
      t('expiredLots.override'),
      sale.expired_lot_override_at
        ? t('expiredLots.overrideDetail', {
            name: sale.expired_lot_override_by_name ?? '',
            date: formatDateTime(sale.expired_lot_override_at, locale, timezone),
            reason: sale.expired_lot_override_reason ?? '',
          })
        : null,
    ],
    [t('sales.notes'), sale.notes],
    [t('stock.createdBy'), at(sale.created_by_name, sale.created_at)],
    [t('stock.validatedBy'), at(sale.validated_by_name, sale.validated_at)],
    [t('stock.cancelledBy'), at(sale.cancelled_by_name, sale.cancelled_at)],
    [t('stock.cancellationReason'), sale.cancellation_reason],
  ];
  return (
    <>
      <Card className="sm-block">
        <dl className="sm-details">
          {rows
            .filter(([, value]) => value)
            .map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
        </dl>
      </Card>
      <DataTable value={sale.lines} dataKey="id">
        <Column
          header={t('stock.article')}
          body={(l: SaleLine) => (
            <div className="sm-cell-stack">
              <span>{`${l.article_reference} — ${l.article_designation}`}</span>
              {/* Lot 3-H-A : lots consommés (FEFO, ou dérogation), journal des mouvements. */}
              <LineLotsList lots={l.lots ?? []} unit={l.unit} locale={locale} />
            </div>
          )}
        />
        <Column header={t('stock.quantity')} body={(l: SaleLine) => soldQuantity(l, locale)} />
        {sale.lines.some((l) => l.packaging_id) && (
          <Column
            header={t('sales.baseQuantity')}
            body={(l: SaleLine) =>
              `${formatQuantity(l.base_quantity ?? l.quantity, locale)} ${l.unit}`
            }
          />
        )}
        <Column
          header={t('sales.unitPrice')}
          body={(l: SaleLine) => formatMoney(l.unit_price, currency, locale)}
        />
        <Column
          header={t('sales.lineTotal')}
          body={(l: SaleLine) => formatMoney(l.line_total, currency, locale)}
        />
      </DataTable>
      <p className="sm-total">
        {t('sales.total')} : <strong>{formatMoney(sale.total, currency, locale)}</strong>
      </p>
    </>
  );
}

function CancelDialog({ sale, onClose }: { sale: Sale; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { cancel } = useSaleMutations();
  const [reason, setReason] = useState('');
  const submit = () =>
    cancel.mutate(
      { id: sale.id, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success(t('sales.cancelled', { number: sale.number ?? t('sales.draftNumber') }));
          onClose();
        },
        onError: (error) => toast.error(saleError(t, error, capabilities.tenant.locale)),
      },
    );
  return (
    <Dialog header={t('sales.cancel')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <Message
          severity="warn"
          text={t(sale.status === 'VALIDATED' ? 'sales.cancelWarning' : 'sales.cancelDraftInfo')}
        />
        <FormField
          id="cancel-reason"
          label={t('stock.cancellationReason')}
          help={t('stock.cancellationReasonHelp')}
          required
        >
          <InputTextarea
            id="cancel-reason"
            rows={3}
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            autoFocus
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.back')} text onClick={onClose} />
          <Button
            label={t('sales.cancel')}
            severity="danger"
            disabled={reason.trim().length < 5}
            loading={cancel.isPending}
            onClick={submit}
          />
        </div>
      </div>
    </Dialog>
  );
}

// --- Page ----------------------------------------------------------------------------------

export default function SalePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can } = useCapabilities();
  const { id } = useParams();
  const isNew = id === undefined || id === 'new';
  const query = useSale(isNew ? undefined : id);
  const [cancelling, setCancelling] = useState(false);

  if (!isNew && query.isPending) {
    return <LoadingState />;
  }
  if (!isNew && query.isError) {
    return <ErrorMessage error={query.error} onRetry={() => void query.refetch()} />;
  }
  const sale = isNew ? undefined : query.data;
  const editable =
    sale === undefined
      ? can('sales.sale.create')
      : sale.status === 'DRAFT' && can('sales.sale.update');
  const canCancel = sale !== undefined && sale.status !== 'CANCELLED' && can('sales.sale.cancel');

  return (
    <>
      <PageHeader
        title={sale ? `${t('sales.one')} ${sale.number ?? t('sales.draftNumber')}` : t('sales.new')}
        breadcrumbs={[
          { label: t('sales.title'), to: '/sales' },
          { label: sale ? (sale.number ?? t('sales.draftNumber')) : t('sales.new') },
        ]}
        actions={
          <div className="sm-tags">
            {sale && <DocumentStatusBadge labels="sales.statuses" status={sale.status} />}
            {sale?.payment_status && <SalePaymentBadge status={sale.payment_status} />}
            {/* Reçu 80 mm (palier POS) : relu depuis la vente persistée ; portée de la vente. */}
            {sale?.status === 'VALIDATED' && <SaleReceiptActions saleId={sale.id} />}
            {canCancel && (
              <Button
                icon="pi pi-undo"
                label={t('sales.cancel')}
                severity="danger"
                outlined
                onClick={() => setCancelling(true)}
              />
            )}
          </div>
        }
      />
      {editable ? (
        <SaleForm key={sale?.id ?? 'new'} sale={sale} />
      ) : sale ? (
        <>
          <SaleSummary sale={sale} />
          {/* Encaissement : ventes validées (et historique d'une vente annulée). */}
          {sale.status !== 'DRAFT' && can('sales.payment.view') && <PaymentsPanel sale={sale} />}
          {/* Lot 2 : sections visibles seulement avec la permission correspondante. */}
          {sale.status !== 'DRAFT' && can('stock.movement.view') && (
            <SaleMovementsPanel sale={sale} />
          )}
          {can('audit.log.view') && <SaleHistoryPanel sale={sale} />}
          <div className="sm-dialog-actions">
            <Button label={t('actions.back')} text onClick={() => void navigate('/sales')} />
          </div>
        </>
      ) : (
        <Message severity="error" text={t('errors.forbidden')} />
      )}
      {cancelling && sale && <CancelDialog sale={sale} onClose={() => setCancelling(false)} />}
    </>
  );
}
