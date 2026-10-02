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

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import {
  compareQuantity,
  formatCost,
  formatMoney,
  formatQuantity,
  multiplyQuantity,
  normalizeDecimal,
} from '@/shared/lib/decimal';
import { formatPresented, toBase, type PresentationPackaging } from '@/shared/lib/presentation';
import { PresentationField } from '@/modules/catalog/PresentationField';
import { formatDate, formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';
import { DocumentStatusBadge } from '@/shared/ui/StatusBadge';
import { confirmAction } from '@/shared/ui/confirm';
import { COST_VIEW, type ScanResult } from '@/modules/catalog/api';
import { BarcodeScanField } from '@/modules/catalog/BarcodeScanField';

import { transferLotsPath, type DocumentLine } from './api';
import { ArticlePicker, toArticleOption, type ArticleOption } from './ArticlePicker';
import { LotAllocationEditor } from './LotAllocationEditor';
import {
  TRANSFERS_FEATURE,
  useAvailableStock,
  useTransfer,
  useTransferMutations,
  type StockTransfer,
  type TransferInput,
} from './transferApi';
import { LineLotsList, stockError } from './ui';

const quantity = z.string().refine((v) => {
  const n = normalizeDecimal(v, 3);
  return n !== null && /[1-9]/.test(n);
}, 'quantity');

const schema = z
  .object({
    source_site_id: z
      .string()
      .nullable()
      .refine((v) => Boolean(v), 'required'),
    destination_site_id: z
      .string()
      .nullable()
      .refine((v) => Boolean(v), 'required'),
    operation_date: z.string(),
    comment: z.string().max(500),
    lines: z
      .array(
        z.object({
          article: z.custom<ArticleOption | null>().refine((v) => Boolean(v), 'required'),
          packaging: z.custom<PresentationPackaging | null>(),
          quantity,
          /** Lot 3-H-B1 : répartition manuelle par lot (unité de base). */
          lots: z.array(z.object({ lot_id: z.string(), quantity: z.string() })),
        }),
      )
      .min(1, 'empty'),
  })
  .superRefine((values, ctx) => {
    // Ergonomie seulement : le serveur refait tous les contrôles (sites, articles, stock).
    if (values.source_site_id === values.destination_site_id) {
      ctx.addIssue({ code: 'custom', path: ['destination_site_id'], message: 'same' });
    }
    // Une ligne par présentation (Lot 3-C).
    const ids = values.lines.map((l) =>
      l.article ? `${l.article.id}:${l.packaging?.id ?? 'base'}` : undefined,
    );
    ids.forEach((id, index) => {
      if (id && ids.indexOf(id) !== index) {
        ctx.addIssue({ code: 'custom', path: ['lines', index, 'article'], message: 'duplicate' });
      }
    });
    // Lot 3-H-B1 : quantités par lot bien formées ; la somme exacte est exigée par le serveur
    // à la validation seulement (brouillon incomplet admis).
    values.lines.forEach((line, index) => {
      if (line.lots.some((l) => normalizeDecimal(l.quantity, 3) === null)) {
        ctx.addIssue({ code: 'custom', path: ['lines', index, 'lots'], message: 'quantity' });
      }
    });
  });
type FormValues = z.infer<typeof schema>;

function defaults(transfer: StockTransfer | undefined, source: string | null): FormValues {
  return {
    source_site_id: transfer?.source_site_id ?? source,
    destination_site_id: transfer?.destination_site_id ?? null,
    operation_date: transfer?.operation_date ?? '',
    comment: transfer?.comment ?? '',
    lines: (transfer?.lines ?? []).map((line) => ({
      article: toArticleOption({
        id: line.article_id,
        reference: line.article_reference,
        designation: line.article_designation,
        unit: line.unit,
      }),
      packaging:
        line.packaging_id && line.packaging_name && line.packaging_conversion
          ? {
              id: line.packaging_id,
              name: line.packaging_name,
              conversion: line.packaging_conversion,
            }
          : null,
      quantity: line.quantity,
      lots: (line.lots ?? []).map((l) => ({ lot_id: l.lot_id, quantity: l.quantity })),
    })),
  };
}

/** Quantité de la ligne en unité de base (indicative, pour « Demandé / Réparti / Reste »). */
function requestedBase(line: FormValues['lines'][number] | undefined): string | null {
  const quantity = normalizeDecimal(line?.quantity ?? '', 3);
  if (quantity === null) return null;
  return line?.packaging ? multiplyQuantity(quantity, line.packaging.conversion) : quantity;
}

function toInput(values: FormValues, isNew: boolean): TransferInput {
  return {
    ...(isNew ? { source_site_id: values.source_site_id } : {}),
    destination_site_id: values.destination_site_id ?? '',
    operation_date: values.operation_date || null,
    comment: values.comment.trim() || null,
    lines: values.lines.map((line) => ({
      article_id: line.article?.id ?? '',
      packaging_id: line.packaging?.id ?? null,
      quantity: normalizeDecimal(line.quantity, 3) ?? '0',
      // Lot 3-H-B1 : choix des lots envoyés tels quels (le serveur les contrôle tous).
      lots: line.lots.map((l) => ({
        lot_id: l.lot_id,
        quantity: normalizeDecimal(l.quantity, 3) ?? '0',
      })),
    })),
  };
}

/**
 * Quantité saisie (convertie en unité de base, Lot 3-C) supérieure au stock affiché
 * (indicatif : le serveur contrôle à la validation).
 */
function exceeds(
  requested: string | undefined,
  packaging: PresentationPackaging | null | undefined,
  available: string | undefined,
): boolean {
  const q = normalizeDecimal(requested ?? '', 3);
  if (q === null || available === undefined) return false;
  const base = packaging ? toBase(q, packaging.conversion) : q;
  return base !== null && compareQuantity(base, available) > 0;
}

// --- Saisie d'un brouillon ----------------------------------------------------------------------

function TransferForm({ transfer }: { transfer: StockTransfer | undefined }) {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { can, capabilities, siteId } = useCapabilities();
  const { save, validate } = useTransferMutations();
  const isNew = transfer === undefined;
  const defaultSource =
    siteId ?? (capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: defaults(transfer, defaultSource),
  });
  const lines = useFieldArray({ control: form.control, name: 'lines' });
  const watched = useWatch({ control: form.control, name: 'lines' });
  const source = useWatch({ control: form.control, name: 'source_site_id' });
  const errors = form.formState.errors;
  const { locale } = capabilities.tenant;
  const available = useAvailableStock(
    source,
    watched.map((line) => line?.article?.id).filter((id): id is string => Boolean(id)),
    can('stock.level.view'),
  );
  const siteOptions = capabilities.sites.map((s) => ({ value: s.id, label: s.name }));
  // Lot 3-H-B1 : lots déjà enregistrés sur les lignes (libellé d'un lot sans solde restant).
  const knownLots = (transfer?.lines ?? []).flatMap((l) => l.lots ?? []);

  /** Lot 3-D : le scan présélectionne article + présentation ; la quantité reste à saisir. */
  const onScan = (scan: ScanResult) => {
    const article = toArticleOption(scan.article);
    const packaging = scan.packaging
      ? { id: scan.packaging.id, name: scan.packaging.name, conversion: scan.packaging.conversion }
      : null;
    const existing = (form.getValues('lines') ?? []).findIndex(
      (l) => l.article?.id === article.id && (l.packaging?.id ?? null) === (packaging?.id ?? null),
    );
    if (existing >= 0) {
      form.setFocus(`lines.${existing}.quantity`);
      return;
    }
    lines.append(
      { article, packaging, quantity: '', lots: [] },
      { focusName: `lines.${lines.fields.length}.quantity` },
    );
  };

  const persist = async (values: FormValues): Promise<StockTransfer> => {
    const saved = await save.mutateAsync({ id: transfer?.id, input: toInput(values, isNew) });
    form.reset(defaults(saved, defaultSource));
    if (isNew) void navigate(`/stock/transfers/${saved.id}`, { replace: true });
    return saved;
  };

  const onSave = form.handleSubmit(async (values) => {
    try {
      await persist(values);
      toast.success(t('stock.draftSaved'));
    } catch (error) {
      toast.error(stockError(t, error, locale));
    }
  });

  const onValidate = form.handleSubmit((values) =>
    confirmAction(t, {
      header: t('transfers.validate'),
      message: t('transfers.confirmValidate'),
      acceptLabel: t('transfers.validate'),
      onAccept: async () => {
        try {
          const saved = form.formState.isDirty || !transfer ? await persist(values) : transfer;
          const validated = await validate.mutateAsync(saved.id);
          toast.success(t('transfers.validated', { number: validated.number }));
        } catch (error) {
          toast.error(stockError(t, error, locale));
        }
      },
    }),
  );

  const destinationError = errors.destination_site_id?.message;
  return (
    <form onSubmit={onSave} className="sm-form" noValidate>
      <div className="sm-form-grid">
        <FormField
          id="transfer-source"
          label={t('transfers.source')}
          required
          error={errors.source_site_id && t('validation.required')}
        >
          <Controller
            control={form.control}
            name="source_site_id"
            render={({ field }) => (
              <Dropdown
                inputId="transfer-source"
                value={field.value}
                onChange={(e) => {
                  field.onChange(e.value);
                  // Autre site source : les lots choisis ne s'y trouvent plus.
                  form.getValues('lines').forEach((_, i) => form.setValue(`lines.${i}.lots`, []));
                }}
                options={siteOptions}
                placeholder={t('stock.chooseSite')}
                disabled={!isNew}
              />
            )}
          />
        </FormField>
        <FormField
          id="transfer-destination"
          label={t('transfers.destination')}
          required
          error={
            destinationError &&
            t(destinationError === 'same' ? 'errors:same_site_transfer' : 'validation.required')
          }
        >
          <Controller
            control={form.control}
            name="destination_site_id"
            render={({ field }) => (
              <Dropdown
                inputId="transfer-destination"
                value={field.value}
                onChange={(e) => field.onChange(e.value)}
                options={siteOptions.filter((o) => o.value !== source)}
                placeholder={t('stock.chooseSite')}
              />
            )}
          />
        </FormField>
        <FormField id="transfer-date" label={t('stock.date')} help={t('stock.dateHelp')}>
          <InputText id="transfer-date" type="date" {...form.register('operation_date')} />
        </FormField>
      </div>

      <fieldset className="sm-fieldset">
        <legend>{t('transfers.lines')}</legend>
        <BarcodeScanField
          id="transfer-scan"
          onScan={onScan}
          reject={(scan) =>
            scan.article.stock_managed
              ? null
              : t('scan.notStockManaged', { article: scan.article.reference })
          }
        />
        {lines.fields.length === 0 && (
          <p className={errors.lines ? 'p-error' : 'sm-muted'}>{t('transfers.noLines')}</p>
        )}
        {lines.fields.map((field, index) => {
          const lineErrors = errors.lines?.[index];
          const line = watched[index];
          const article = line?.article;
          const stock = article ? available.data?.get(article.id) : undefined;
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
                      stockManagedOnly
                      value={f.value}
                      onChange={(value) => {
                        f.onChange(value);
                        // Autre article : retour à l'unité de base, lots à rechoisir.
                        form.setValue(`lines.${index}.packaging`, null);
                        form.setValue(`lines.${index}.lots`, []);
                      }}
                      invalid={Boolean(lineErrors?.article)}
                    />
                  )}
                />
              </FormField>
              {article && (
                <FormField id={`line-${index}-packaging`} label={t('presentation.label')}>
                  <Controller
                    control={form.control}
                    name={`lines.${index}.packaging`}
                    render={({ field: f }) => (
                      <PresentationField
                        id={`line-${index}-packaging`}
                        articleId={article.id}
                        unit={article.unit}
                        decimalAllowed={article.decimal_quantity_allowed}
                        value={f.value}
                        quantity={line?.quantity ?? ''}
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
                    ? `${t('stock.quantity')} (${line?.packaging?.name ?? article.unit})`
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
                {stock !== undefined && (
                  <small
                    className={
                      exceeds(line?.quantity, line?.packaging, stock) ? 'p-error' : 'sm-muted'
                    }
                    data-testid={`available-${index}`}
                  >
                    {t('transfers.available', {
                      quantity: formatQuantity(stock, locale),
                      unit: article?.unit ?? '',
                    })}
                  </small>
                )}
              </div>
              <Button
                type="button"
                icon="pi pi-trash"
                text
                severity="danger"
                aria-label={t('stock.removeLine')}
                onClick={() => lines.remove(index)}
              />
              {article && source && (
                <Controller
                  control={form.control}
                  name={`lines.${index}.lots`}
                  render={({ field: f }) => (
                    <LotAllocationEditor
                      id={`line-${index}-lots`}
                      articleId={article.id}
                      siteId={source}
                      unit={article.unit}
                      requested={requestedBase(line)}
                      value={f.value}
                      known={knownLots}
                      locale={locale}
                      onChange={f.onChange}
                      lotsPath={transferLotsPath}
                      blockExpired
                      help={t('lotAllocation.transferHelp')}
                    />
                  )}
                />
              )}
            </div>
          );
        })}
        <div>
          <Button
            type="button"
            icon="pi pi-plus"
            outlined
            label={t('stock.addLine')}
            onClick={() =>
              lines.append({ article: null, packaging: null, quantity: '1', lots: [] })
            }
          />
        </div>
      </fieldset>
      <FormField id="transfer-comment" label={t('stock.comment')}>
        <InputTextarea id="transfer-comment" rows={2} {...form.register('comment')} />
      </FormField>
      <small className="sm-help">{t('transfers.costHelp')}</small>
      <div className="sm-dialog-actions">
        <Button
          type="button"
          label={t('actions.back')}
          text
          onClick={() => void navigate('/stock/transfers')}
        />
        <Button type="submit" label={t('stock.saveDraft')} outlined loading={save.isPending} />
        {can('stock.transfer.validate') && (
          <Button
            type="button"
            icon="pi pi-check"
            label={t('transfers.validate')}
            disabled={save.isPending || validate.isPending}
            loading={validate.isPending}
            onClick={() => void onValidate()}
          />
        )}
      </div>
    </form>
  );
}

// --- Consultation ------------------------------------------------------------------------------

function TransferSummary({ transfer }: { transfer: StockTransfer }) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  // Coûts internes : affichés seulement avec cost_view (absents des réponses sinon).
  const costs = can(COST_VIEW);
  const at = (name: string | null, date: string | null) =>
    date ? `${name ?? ''} · ${formatDateTime(date, locale, timezone)}` : null;
  const rows: [string, string | null][] = [
    [t('transfers.source'), transfer.source_site_name],
    [t('transfers.destination'), transfer.destination_site_name],
    [t('stock.date'), formatDate(transfer.operation_date, locale, 'UTC')],
    [t('stock.comment'), transfer.comment],
    [t('stock.createdBy'), at(transfer.created_by_name, transfer.created_at)],
    [t('stock.validatedBy'), at(transfer.validated_by_name, transfer.validated_at)],
    [t('stock.cancelledBy'), at(transfer.cancelled_by_name, transfer.cancelled_at)],
    [t('stock.cancellationReason'), transfer.cancellation_reason],
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
      <DataTable value={transfer.lines} dataKey="id" emptyMessage={t('stock.noLines')}>
        <Column
          header={t('stock.article')}
          body={(l: DocumentLine) => (
            <div className="sm-cell-stack">
              <span>{`${l.article_reference} — ${l.article_designation}`}</span>
              {/* Lot 3-H-B1 : lots transférés (le même lot sur les deux sites). */}
              <LineLotsList lots={l.lots ?? []} unit={l.unit} locale={locale} />
            </div>
          )}
        />
        <Column
          header={t('stock.quantity')}
          body={(l: DocumentLine) =>
            formatPresented(l.quantity, l.packaging_name, l.base_quantity, l.unit, locale)
          }
        />
        {costs && (
          <Column
            header={t('transfers.unitCost')}
            body={(l: DocumentLine) => formatCost(l.unit_cost, currency, locale)}
          />
        )}
        {costs && (
          <Column
            header={t('stock.amount')}
            body={(l: DocumentLine) => formatMoney(l.amount, currency, locale)}
          />
        )}
      </DataTable>
      {costs && transfer.total_amount != null && (
        <p className="sm-total">
          {t('transfers.value')} :{' '}
          <strong>{formatMoney(transfer.total_amount, currency, locale)}</strong>
        </p>
      )}
    </>
  );
}

function CancelDialog({ transfer, onClose }: { transfer: StockTransfer; onClose: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { cancel } = useTransferMutations();
  const [reason, setReason] = useState('');
  const submit = () =>
    cancel.mutate(
      { id: transfer.id, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success(t('transfers.cancelled', { number: transfer.number }));
          onClose();
        },
        onError: (error) => toast.error(stockError(t, error, capabilities.tenant.locale)),
      },
    );
  return (
    <Dialog header={t('transfers.cancel')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <Message
          severity="warn"
          text={t(
            transfer.status === 'VALIDATED'
              ? 'transfers.cancelWarning'
              : 'transfers.cancelDraftInfo',
          )}
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
            label={t('transfers.cancel')}
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

// --- Page --------------------------------------------------------------------------------------

export default function TransferPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { id } = useParams();
  const isNew = id === undefined || id === 'new';
  const query = useTransfer(isNew ? undefined : id);
  // Plan sans la fonctionnalité : consultation seule (le serveur refuse toute opération).
  const featureActive = capabilities.features.includes(TRANSFERS_FEATURE);
  const [cancelling, setCancelling] = useState(false);

  if (!isNew && query.isPending) {
    return <LoadingState />;
  }
  if (!isNew && query.isError) {
    return <ErrorMessage error={query.error} onRetry={() => void query.refetch()} />;
  }
  const transfer = isNew ? undefined : query.data;
  const editable =
    featureActive &&
    (transfer === undefined
      ? can('stock.transfer.create')
      : transfer.status === 'DRAFT' && can('stock.transfer.update'));
  const canCancel =
    featureActive &&
    transfer !== undefined &&
    transfer.status !== 'CANCELLED' &&
    can('stock.transfer.cancel');

  return (
    <>
      <PageHeader
        title={transfer ? `${t('transfers.one')} ${transfer.number}` : t('transfers.new')}
        breadcrumbs={[
          { label: t('transfers.title'), to: '/stock/transfers' },
          { label: transfer ? transfer.number : t('transfers.new') },
        ]}
        actions={
          <div className="sm-tags">
            {transfer && <DocumentStatusBadge status={transfer.status} />}
            {canCancel && (
              <Button
                icon="pi pi-undo"
                label={t('transfers.cancel')}
                severity="danger"
                outlined
                onClick={() => setCancelling(true)}
              />
            )}
          </div>
        }
      />
      {!featureActive && (
        <Message severity="info" className="sm-block" text={t('transfers.readOnlyPlan')} />
      )}
      {editable ? (
        <TransferForm key={transfer?.id ?? 'new'} transfer={transfer} />
      ) : transfer ? (
        <>
          <TransferSummary transfer={transfer} />
          <div className="sm-dialog-actions">
            <Button
              label={t('actions.back')}
              text
              onClick={() => void navigate('/stock/transfers')}
            />
          </div>
        </>
      ) : (
        <Message severity="error" text={t('errors.forbidden')} />
      )}
      {cancelling && transfer && (
        <CancelDialog transfer={transfer} onClose={() => setCancelling(false)} />
      )}
    </>
  );
}
