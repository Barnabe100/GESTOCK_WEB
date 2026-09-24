import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { confirmDialog, ConfirmDialog } from 'primereact/confirmdialog';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { ProgressSpinner } from 'primereact/progressspinner';
import { useState } from 'react';
import { Controller, useFieldArray, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatCost, formatMoney, formatQuantity, normalizeDecimal } from '@/shared/lib/decimal';
import { formatDate, formatDateTime } from '@/shared/lib/format';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import type { DocumentLine } from './api';
import { ArticlePicker, toArticleOption, type ArticleOption } from './ArticlePicker';
import {
  useAvailableStock,
  useTransfer,
  useTransferMutations,
  type StockTransfer,
  type TransferInput,
} from './transferApi';
import { DocumentStatusTag, stockError } from './ui';

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
          quantity,
        }),
      )
      .min(1, 'empty'),
  })
  .superRefine((values, ctx) => {
    // Ergonomie seulement : le serveur refait tous les contrôles (sites, articles, stock).
    if (values.source_site_id === values.destination_site_id) {
      ctx.addIssue({ code: 'custom', path: ['destination_site_id'], message: 'same' });
    }
    const ids = values.lines.map((l) => l.article?.id);
    ids.forEach((id, index) => {
      if (id && ids.indexOf(id) !== index) {
        ctx.addIssue({ code: 'custom', path: ['lines', index, 'article'], message: 'duplicate' });
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
      quantity: line.quantity,
    })),
  };
}

function toInput(values: FormValues, isNew: boolean): TransferInput {
  return {
    ...(isNew ? { source_site_id: values.source_site_id } : {}),
    destination_site_id: values.destination_site_id ?? '',
    operation_date: values.operation_date || null,
    comment: values.comment.trim() || null,
    lines: values.lines.map((line) => ({
      article_id: line.article?.id ?? '',
      quantity: normalizeDecimal(line.quantity, 3) ?? '0',
    })),
  };
}

/** Quantité saisie supérieure au stock affiché (indicatif : le serveur contrôle à la validation). */
function exceeds(requested: string | undefined, available: string | undefined): boolean {
  const q = normalizeDecimal(requested ?? '', 3);
  return q !== null && available !== undefined && Number(q) > Number(available);
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
    confirmDialog({
      header: t('transfers.validate'),
      message: t('transfers.confirmValidate'),
      acceptLabel: t('transfers.validate'),
      rejectLabel: t('actions.cancel'),
      accept: async () => {
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
      <ConfirmDialog />
      <div className="sm-form-grid">
        <FormField
          id="transfer-source"
          label={t('transfers.source')}
          error={errors.source_site_id && t('validation.required')}
        >
          <Controller
            control={form.control}
            name="source_site_id"
            render={({ field }) => (
              <Dropdown
                inputId="transfer-source"
                value={field.value}
                onChange={(e) => field.onChange(e.value)}
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
                      value={f.value}
                      onChange={f.onChange}
                      invalid={Boolean(lineErrors?.article)}
                    />
                  )}
                />
              </FormField>
              <FormField
                id={`line-${index}-quantity`}
                label={article ? `${t('stock.quantity')} (${article.unit})` : t('stock.quantity')}
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
                    className={exceeds(line?.quantity, stock) ? 'p-error' : 'sm-muted'}
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
            </div>
          );
        })}
        <div>
          <Button
            type="button"
            icon="pi pi-plus"
            outlined
            label={t('stock.addLine')}
            onClick={() => lines.append({ article: null, quantity: '1' })}
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
  const { capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
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
          body={(l: DocumentLine) => `${l.article_reference} — ${l.article_designation}`}
        />
        <Column
          header={t('stock.quantity')}
          body={(l: DocumentLine) => `${formatQuantity(l.quantity, locale)} ${l.unit}`}
        />
        <Column
          header={t('transfers.unitCost')}
          body={(l: DocumentLine) => formatCost(l.unit_cost, currency, locale)}
        />
        <Column
          header={t('stock.amount')}
          body={(l: DocumentLine) => formatMoney(l.amount, currency, locale)}
        />
      </DataTable>
      {transfer.total_amount !== null && (
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
  const { can } = useCapabilities();
  const { id } = useParams();
  const isNew = id === undefined || id === 'new';
  const query = useTransfer(isNew ? undefined : id);
  const [cancelling, setCancelling] = useState(false);

  if (!isNew && query.isPending) {
    return (
      <div className="sm-center">
        <ProgressSpinner />
      </div>
    );
  }
  if (!isNew && query.isError) {
    return <ErrorMessage error={query.error} onRetry={() => void query.refetch()} />;
  }
  const transfer = isNew ? undefined : query.data;
  const editable =
    transfer === undefined
      ? can('stock.transfer.create')
      : transfer.status === 'DRAFT' && can('stock.transfer.update');
  const canCancel =
    transfer !== undefined && transfer.status !== 'CANCELLED' && can('stock.transfer.cancel');

  return (
    <>
      <PageHeader
        title={transfer ? `${t('transfers.one')} ${transfer.number}` : t('transfers.new')}
        actions={
          <div className="sm-tags">
            {transfer && <DocumentStatusTag status={transfer.status} />}
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
