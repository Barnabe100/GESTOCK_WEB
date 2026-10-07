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
import { useSuppliers } from '@/modules/suppliers/api';
import { formatCost, formatMoney, multiplyQuantity, normalizeDecimal } from '@/shared/lib/decimal';
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

import {
  DOCUMENT_CONFIG,
  useDocument,
  useDocumentMutations,
  useExitReasons,
  type DocumentKind,
  type DocumentLine,
  type EntryInput,
  type EntryKind,
  type ExitInput,
  type StockDocument,
  type StockEntry,
  type StockExit,
} from './api';
import { AssortmentNotice } from '@/modules/catalog/assortment';
import { PresentationField } from '@/modules/catalog/PresentationField';
import { formatPresented, type PresentationPackaging } from '@/shared/lib/presentation';

import { ArticlePicker, toArticleOption, type ArticleOption } from './ArticlePicker';
import { LotAllocationEditor } from './LotAllocationEditor';
import { useDraftSubmission } from './useDraftSubmission';
import { ArticleLocationHint, LocationLabel } from './LocationAssignDialog';
import { LineLotsList, LotLabel, stockError } from './ui';

const OPTIONS_QUERY = 'limit=200&status=active&sort=name';

const lineSchema = z.object({
  article: z.custom<ArticleOption | null>().refine((v) => Boolean(v), 'required'),
  /** Lot 3-C : présentation saisie (nul = unité de base). */
  packaging: z.custom<PresentationPackaging | null>(),
  quantity: z.string().refine((v) => {
    const n = normalizeDecimal(v, 3);
    return n !== null && /[1-9]/.test(n);
  }, 'quantity'),
  unit_cost: z.string(),
  /** Lot 3-G (réceptions) : lot saisi pour un article suivi par lot. */
  lot_number: z.string().max(50),
  lot_expiry_date: z.string(),
  lot_manufacturing_date: z.string(),
  /** Lot 3-H-A (sorties) : répartition manuelle par lot, en unité de base. */
  lots: z.array(z.object({ lot_id: z.string(), quantity: z.string() })),
});

function buildSchema(kind: DocumentKind) {
  return z
    .object({
      site_id: z.string().nullable(),
      operation_date: z.string(),
      comment: z.string().max(500),
      entry_kind: z.enum(['PURCHASE', 'INITIAL_STOCK']),
      supplier_id: z.string().nullable(),
      document_reference: z.string().max(100),
      reason_id: z.string().nullable(),
      beneficiary: z.string().max(150),
      reference: z.string().max(100),
      lines: z.array(lineSchema),
    })
    .superRefine((values, ctx) => {
      // Ergonomie seulement : le backend applique les mêmes règles (ENT-01, SOR-02).
      if (!values.site_id) {
        ctx.addIssue({ code: 'custom', path: ['site_id'], message: 'required' });
      }
      if (kind === 'exits' && !values.reason_id) {
        ctx.addIssue({ code: 'custom', path: ['reason_id'], message: 'required' });
      }
      if (kind === 'exits') {
        values.lines.forEach((line, index) => {
          // Lot 3-H-A : quantités par lot bien formées ; la somme exacte est exigée par le
          // serveur à la validation seulement (brouillon incomplet admis).
          if (line.lots.some((l) => normalizeDecimal(l.quantity, 3) === null)) {
            ctx.addIssue({ code: 'custom', path: ['lines', index, 'lots'], message: 'quantity' });
          }
        });
      }
      if (kind === 'entries') {
        values.lines.forEach((line, index) => {
          if (normalizeDecimal(line.unit_cost, 2) === null) {
            ctx.addIssue({ code: 'custom', path: ['lines', index, 'unit_cost'], message: 'money' });
          }
          // Lot 3-G : lot obligatoire pour un article suivi, péremption s'il est suivi en
          // péremption, fabrication ≤ péremption (le serveur applique les mêmes règles).
          if (line.article?.lot_tracked && !line.lot_number.trim()) {
            ctx.addIssue({
              code: 'custom',
              path: ['lines', index, 'lot_number'],
              message: 'required',
            });
          }
          if (line.article?.expiry_tracked && !line.lot_expiry_date) {
            ctx.addIssue({
              code: 'custom',
              path: ['lines', index, 'lot_expiry_date'],
              message: 'required',
            });
          }
          if (
            line.lot_expiry_date &&
            line.lot_manufacturing_date &&
            line.lot_manufacturing_date > line.lot_expiry_date
          ) {
            ctx.addIssue({
              code: 'custom',
              path: ['lines', index, 'lot_manufacturing_date'],
              message: 'lotDates',
            });
          }
        });
      }
    });
}
type FormValues = z.infer<ReturnType<typeof buildSchema>>;

function defaults(document: StockDocument | undefined, siteId: string | null): FormValues {
  const entry = document as StockEntry | undefined;
  const exit = document as StockExit | undefined;
  return {
    site_id: document?.site_id ?? siteId,
    operation_date: document?.operation_date ?? '',
    comment: document?.comment ?? '',
    entry_kind: entry?.kind ?? 'PURCHASE',
    supplier_id: entry?.supplier_id ?? null,
    document_reference: entry?.document_reference ?? '',
    reason_id: exit?.reason_id ?? null,
    beneficiary: exit?.beneficiary ?? '',
    reference: exit?.reference ?? '',
    lines: (document?.lines ?? []).map((line) => ({
      article: toArticleOption({
        id: line.article_id,
        reference: line.article_reference,
        designation: line.article_designation,
        unit: line.unit,
        // Ligne enregistrée avec un lot : l'article est suivi par lot (guidage seulement).
        lot_tracked: Boolean(line.lot_number),
        expiry_tracked: Boolean(line.lot_expiry_date),
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
      unit_cost: line.unit_cost ?? '',
      lot_number: line.lot_number ?? '',
      lot_expiry_date: line.lot_expiry_date ?? '',
      lot_manufacturing_date: line.lot_manufacturing_date ?? '',
      lots: (line.lots ?? []).map((l) => ({ lot_id: l.lot_id, quantity: l.quantity })),
    })),
  };
}

/** Quantité de la ligne en unité de base, indicative (le serveur la recalcule). */
function requestedBase(line: FormValues['lines'][number] | undefined): string | null {
  const quantity = normalizeDecimal(line?.quantity ?? '', 3);
  if (quantity === null) return null;
  return line?.packaging ? multiplyQuantity(quantity, line.packaging.conversion) : quantity;
}

const EMPTY_LINE = {
  packaging: null,
  quantity: '',
  unit_cost: '',
  lot_number: '',
  lot_expiry_date: '',
  lot_manufacturing_date: '',
  lots: [],
};

function toInput(kind: DocumentKind, values: FormValues, isNew: boolean): EntryInput | ExitInput {
  const common = {
    ...(isNew ? { site_id: values.site_id } : {}),
    operation_date: values.operation_date || null,
    comment: values.comment.trim() || null,
  };
  const lines = values.lines.map((line) => ({
    article_id: line.article?.id ?? '',
    packaging_id: line.packaging?.id ?? null,
    quantity: normalizeDecimal(line.quantity, 3) ?? '0',
  }));
  if (kind === 'entries') {
    const initial = values.entry_kind === 'INITIAL_STOCK';
    return {
      ...common,
      kind: values.entry_kind,
      supplier_id: initial ? null : values.supplier_id,
      document_reference: values.document_reference.trim() || null,
      lines: lines.map((line, index) => {
        const source = values.lines[index];
        const lot = source?.lot_number.trim() ?? '';
        return {
          ...line,
          unit_cost: normalizeDecimal(source?.unit_cost ?? '', 2) ?? '0',
          // Lot 3-G : lot envoyé seulement s'il est saisi (le serveur l'exige ou le refuse).
          ...(lot
            ? {
                lot_number: lot,
                lot_expiry_date: source?.lot_expiry_date || null,
                lot_manufacturing_date: source?.lot_manufacturing_date || null,
              }
            : {}),
        };
      }),
    };
  }
  return {
    ...common,
    reason_id: values.reason_id ?? '',
    beneficiary: values.beneficiary.trim() || null,
    reference: values.reference.trim() || null,
    // Lot 3-H-A : choix des lots envoyés tels quels (le serveur les contrôle tous).
    lines: lines.map((line, index) => ({
      ...line,
      lots: (values.lines[index]?.lots ?? []).map((l) => ({
        lot_id: l.lot_id,
        quantity: normalizeDecimal(l.quantity, 3) ?? '0',
      })),
    })),
  };
}

// --- Consultation (document validé, annulé, ou sans droit de modification) --------------------

function DocumentSummary({ kind, document }: { kind: DocumentKind; document: StockDocument }) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  // Coûts internes : affichés seulement avec cost_view (absents des réponses sinon).
  const costs = can(COST_VIEW);
  const entry = kind === 'entries' ? (document as StockEntry) : null;
  const exit = kind === 'exits' ? (document as StockExit) : null;
  const rows: [string, string | null][] = [
    [t('layout.site'), document.site_name],
    [t('stock.date'), formatDate(document.operation_date, locale, 'UTC')],
    ...(entry
      ? ([
          [t('entries.kind'), t(`entries.kinds.${entry.kind}`)],
          [t('entries.supplier'), entry.supplier_name],
          [t('entries.documentReference'), entry.document_reference],
        ] as [string, string | null][])
      : []),
    ...(exit
      ? ([
          [t('exits.reason'), exit.reason_label],
          [t('exits.beneficiary'), exit.beneficiary],
          [t('exits.reference'), exit.reference],
        ] as [string, string | null][])
      : []),
    [t('stock.comment'), document.comment],
    [
      t('stock.createdBy'),
      `${document.created_by_name ?? ''} · ${formatDateTime(document.created_at, locale, timezone)}`,
    ],
    [
      t('stock.validatedBy'),
      document.validated_at
        ? `${document.validated_by_name ?? ''} · ${formatDateTime(document.validated_at, locale, timezone)}`
        : null,
    ],
    [
      t('stock.cancelledBy'),
      document.cancelled_at
        ? `${document.cancelled_by_name ?? ''} · ${formatDateTime(document.cancelled_at, locale, timezone)}`
        : null,
    ],
    [t('stock.cancellationReason'), document.cancellation_reason],
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
      <DataTable value={document.lines} dataKey="id" emptyMessage={t('stock.noLines')}>
        <Column
          header={t('stock.article')}
          body={(l: DocumentLine) => (
            <div className="sm-cell-stack">
              <span>{`${l.article_reference} — ${l.article_designation}`}</span>
              {/* Lot 3-G : lot reçu, péremption et état (calculé par le serveur). */}
              <LotLabel
                number={l.lot_number}
                expiry={l.lot_expiry_date}
                state={l.lot_state}
                locale={locale}
              />
              {/* Lot 3-H-A : répartition par lot d'une sortie (journal des mouvements). */}
              <LineLotsList lots={l.lots ?? []} unit={l.unit} locale={locale} />
            </div>
          )}
        />
        {/* Lot 3-F : emplacement COURANT sur le site du document (indicatif, jamais figé). */}
        <Column
          header={t('locations.location')}
          body={(l: DocumentLine) => <LocationLabel name={l.location_name} />}
        />
        <Column
          header={t('stock.quantity')}
          body={(l: DocumentLine) =>
            formatPresented(l.quantity, l.packaging_name, l.base_quantity, l.unit, locale)
          }
        />
        {costs && (
          <Column
            header={t('stock.unitCost')}
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
      {costs && document.total_amount != null && (
        <p className="sm-total">
          {t('stock.total')} :{' '}
          <strong>{formatMoney(document.total_amount, currency, locale)}</strong>
        </p>
      )}
    </>
  );
}

function CancelDialog({
  onConfirm,
  onClose,
  loading,
}: {
  onConfirm: (reason: string) => void;
  onClose: () => void;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState('');
  const valid = reason.trim().length >= 5;
  return (
    <Dialog header={t('stock.cancelDocument')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <Message severity="warn" text={t('stock.cancelWarning')} />
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
            label={t('stock.cancelDocument')}
            severity="danger"
            disabled={!valid}
            loading={loading}
            onClick={() => onConfirm(reason.trim())}
          />
        </div>
      </div>
    </Dialog>
  );
}

// --- Saisie d'un brouillon --------------------------------------------------------------------

function DocumentForm({
  kind,
  document,
}: {
  kind: DocumentKind;
  document: StockDocument | undefined;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { can, hasModule, capabilities, siteId } = useCapabilities();
  const config = DOCUMENT_CONFIG[kind];
  const { save, validate } = useDocumentMutations<StockDocument>(kind);
  const isNew = document === undefined;
  const defaultSite =
    siteId ?? (capabilities.sites.length === 1 ? (capabilities.sites[0]?.id ?? null) : null);
  const form = useForm<FormValues>({
    resolver: zodResolver(buildSchema(kind)),
    defaultValues: defaults(document, defaultSite),
  });
  const lines = useFieldArray({ control: form.control, name: 'lines' });
  const errors = form.formState.errors;
  const entryKind = useWatch({ control: form.control, name: 'entry_kind' });
  const watchedLines = useWatch({ control: form.control, name: 'lines' });
  // Lot 3-F : emplacement courant des articles sur le site du document (aide à la saisie).
  const watchedSite = useWatch({ control: form.control, name: 'site_id' });
  const hintSite = document?.site_id ?? watchedSite ?? null;
  const { locale } = capabilities.tenant;

  const showSuppliers =
    kind === 'entries' && hasModule('suppliers') && can('suppliers.supplier.view');
  const suppliers = useSuppliers(OPTIONS_QUERY, showSuppliers);
  const reasons = useExitReasons('limit=200&status=active&sort=label', kind === 'exits');
  const supplierOptions = (suppliers.data?.items ?? []).map((s) => ({
    value: s.id,
    label: s.name,
  }));
  const entry = document as StockEntry | undefined;
  if (entry?.supplier_id && !supplierOptions.some((o) => o.value === entry.supplier_id)) {
    supplierOptions.push({ value: entry.supplier_id, label: entry.supplier_name ?? '…' });
  }
  const reasonOptions = (reasons.data?.items ?? []).map((r) => ({ value: r.id, label: r.label }));
  const exit = document as StockExit | undefined;
  if (exit && !reasonOptions.some((o) => o.value === exit.reason_id)) {
    reasonOptions.push({ value: exit.reason_id, label: exit.reason_label });
  }

  // Brouillon : un seul créé (identifiant conservé dès la création), modification détectée sur
  // la saisie elle-même, une seule soumission à la fois.
  const draft = useDraftSubmission({
    initial: document,
    valuesOf: (saved: StockDocument) => defaults(saved, defaultSite),
    toInput: (values: FormValues, create: boolean) => toInput(kind, values, create),
    save: (id, input) => save.mutateAsync({ id, input }),
    onSaved: (saved, created) => {
      form.reset(defaults(saved, defaultSite));
      if (created) void navigate(`/stock/${kind}/${saved.id}`, { replace: true });
    },
  });

  const onSave = form.handleSubmit((values) =>
    draft.exclusive(async () => {
      try {
        await draft.persist(values);
        toast.success(t('stock.draftSaved'));
      } catch (error) {
        toast.error(stockError(t, error, locale));
      }
    }),
  );

  const onValidate = form.handleSubmit((values) =>
    confirmAction(t, {
      header: t('stock.validate'),
      message: t(`${config.i18n}.confirmValidate`),
      acceptLabel: t('stock.validate'),
      onAccept: () =>
        draft.exclusive(async () => {
          try {
            // Version validée = version enregistrée de la saisie courante.
            const saved = await draft.ensureSaved(values);
            const validated = await validate.mutateAsync(saved.id);
            toast.success(t('stock.validated', { number: validated.number }));
          } catch (error) {
            toast.error(stockError(t, error, locale));
          }
        }),
    }),
  );

  const busy = draft.pending || save.isPending || validate.isPending;
  // Lot 3-H-A : lots déjà enregistrés sur les lignes (libellé d'un lot sans solde restant).
  const knownLots = (document?.lines ?? []).flatMap((l) => l.lots ?? []);
  const siteOptions = capabilities.sites.map((s) => ({ value: s.id, label: s.name }));

  /** Lot 3-D : le scan présélectionne article + présentation ; quantité (et coût) à saisir. */
  const onScan = (scan: ScanResult) => {
    const article = toArticleOption(scan.article);
    const packaging = scan.packaging
      ? { id: scan.packaging.id, name: scan.packaging.name, conversion: scan.packaging.conversion }
      : null;
    // Lot 3-G : un article suivi par lot peut figurer sur plusieurs lignes (un lot chacune).
    const existing = article.lot_tracked
      ? -1
      : (form.getValues('lines') ?? []).findIndex(
          (l) =>
            l.article?.id === article.id && (l.packaging?.id ?? null) === (packaging?.id ?? null),
        );
    if (existing >= 0) {
      form.setFocus(`lines.${existing}.quantity`);
      return;
    }
    lines.append(
      { ...EMPTY_LINE, article, packaging },
      { focusName: `lines.${lines.fields.length}.quantity` },
    );
  };

  return (
    <form onSubmit={onSave} className="sm-form" noValidate>
      <div className="sm-form-grid">
        {isNew && siteId === null && (
          <FormField
            id="doc-site"
            label={t('layout.site')}
            required
            error={errors.site_id && t('validation.required')}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <Dropdown
                  inputId="doc-site"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value)}
                  options={siteOptions}
                  placeholder={t('stock.chooseSite')}
                />
              )}
            />
          </FormField>
        )}
        {!isNew && (
          <FormField id="doc-site-ro" label={t('layout.site')}>
            <InputText id="doc-site-ro" value={document.site_name} disabled />
          </FormField>
        )}
        <FormField id="doc-date" label={t('stock.date')} help={t('stock.dateHelp')}>
          <InputText id="doc-date" type="date" {...form.register('operation_date')} />
        </FormField>
        {kind === 'entries' && (
          <>
            <FormField id="doc-kind" label={t('entries.kind')}>
              <Controller
                control={form.control}
                name="entry_kind"
                render={({ field }) => (
                  <Dropdown
                    inputId="doc-kind"
                    value={field.value}
                    onChange={(e) => field.onChange(e.value as EntryKind)}
                    options={(['PURCHASE', 'INITIAL_STOCK'] as const).map((v) => ({
                      value: v,
                      label: t(`entries.kinds.${v}`),
                    }))}
                  />
                )}
              />
            </FormField>
            {entryKind === 'PURCHASE' && showSuppliers && (
              <FormField id="doc-supplier" label={t('entries.supplier')}>
                <Controller
                  control={form.control}
                  name="supplier_id"
                  render={({ field }) => (
                    <Dropdown
                      inputId="doc-supplier"
                      value={field.value}
                      onChange={(e) => field.onChange((e.value as string | undefined) ?? null)}
                      options={supplierOptions}
                      placeholder={t('entries.chooseSupplier')}
                      showClear
                      filter
                    />
                  )}
                />
              </FormField>
            )}
            <FormField id="doc-reference" label={t('entries.documentReference')}>
              <InputText id="doc-reference" {...form.register('document_reference')} />
            </FormField>
          </>
        )}
        {kind === 'exits' && (
          <>
            <FormField
              id="doc-reason"
              label={t('exits.reason')}
              required
              error={errors.reason_id && t('validation.required')}
            >
              <Controller
                control={form.control}
                name="reason_id"
                render={({ field }) => (
                  <Dropdown
                    inputId="doc-reason"
                    value={field.value}
                    onChange={(e) => field.onChange((e.value as string | undefined) ?? null)}
                    options={reasonOptions}
                    placeholder={t('exits.chooseReason')}
                    filter
                  />
                )}
              />
            </FormField>
            <FormField id="doc-beneficiary" label={t('exits.beneficiary')}>
              <InputText id="doc-beneficiary" {...form.register('beneficiary')} />
            </FormField>
            <FormField id="doc-ext-reference" label={t('exits.reference')}>
              <InputText id="doc-ext-reference" {...form.register('reference')} />
            </FormField>
          </>
        )}
      </div>
      <FormField id="doc-comment" label={t('stock.comment')}>
        <InputTextarea id="doc-comment" rows={2} {...form.register('comment')} />
      </FormField>

      <fieldset className="sm-fieldset">
        <legend>{t('stock.lines')}</legend>
        <BarcodeScanField
          id="doc-scan"
          onScan={onScan}
          reject={(scan) =>
            scan.article.stock_managed
              ? null
              : t('scan.notStockManaged', { article: scan.article.reference })
          }
        />
        {lines.fields.length === 0 && <p className="sm-muted">{t('stock.noLines')}</p>}
        {lines.fields.map((field, index) => {
          const lineErrors = errors.lines?.[index];
          const article = watchedLines[index]?.article;
          return (
            <div key={field.id} className="sm-line">
              <FormField
                id={`line-${index}-article`}
                label={t('stock.article')}
                required
                error={lineErrors?.article && t('validation.required')}
              >
                <Controller
                  control={form.control}
                  name={`lines.${index}.article`}
                  render={({ field: f }) => (
                    <ArticlePicker
                      id={`line-${index}-article`}
                      stockManagedOnly
                      siteId={hintSite}
                      value={f.value}
                      onChange={(value) => {
                        f.onChange(value);
                        // Autre article : retour à l'unité de base, lot à ressaisir.
                        form.setValue(`lines.${index}.packaging`, null);
                        form.setValue(`lines.${index}.lots`, []);
                        if (kind === 'entries') {
                          form.setValue(`lines.${index}.lot_number`, '');
                          form.setValue(`lines.${index}.lot_expiry_date`, '');
                          form.setValue(`lines.${index}.lot_manufacturing_date`, '');
                        }
                      }}
                      invalid={Boolean(lineErrors?.article)}
                    />
                  )}
                />
              </FormField>
              {article && hintSite && (
                <ArticleLocationHint siteId={hintSite} articleId={article.id} />
              )}
              {article && hintSite && (
                <AssortmentNotice articleId={article.id} siteIds={[hintSite]} />
              )}
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
                        quantity={watchedLines[index]?.quantity ?? ''}
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
                    ? `${t('stock.quantity')} (${watchedLines[index]?.packaging?.name ?? article.unit})`
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
              {kind === 'entries' && (
                <FormField
                  id={`line-${index}-cost`}
                  label={
                    watchedLines[index]?.packaging
                      ? t('entries.unitCostPer', { name: watchedLines[index]?.packaging?.name })
                      : t('entries.unitCost')
                  }
                  required
                  error={lineErrors?.unit_cost && t('articles.invalidMoney')}
                >
                  <InputText
                    id={`line-${index}-cost`}
                    inputMode="decimal"
                    {...form.register(`lines.${index}.unit_cost`)}
                  />
                </FormField>
              )}
              <Button
                type="button"
                icon="pi pi-trash"
                text
                severity="danger"
                aria-label={t('stock.removeLine')}
                onClick={() => lines.remove(index)}
              />
              {kind === 'exits' && article && hintSite && (
                <Controller
                  control={form.control}
                  name={`lines.${index}.lots`}
                  render={({ field: f }) => (
                    <LotAllocationEditor
                      id={`line-${index}-lots`}
                      articleId={article.id}
                      siteId={hintSite}
                      unit={article.unit}
                      requested={requestedBase(watchedLines[index])}
                      value={f.value}
                      known={knownLots}
                      locale={locale}
                      onChange={f.onChange}
                    />
                  )}
                />
              )}
              {kind === 'entries' &&
                (article?.lot_tracked || Boolean(watchedLines[index]?.lot_number)) && (
                  <div className="sm-line-lot">
                    <FormField
                      id={`line-${index}-lot`}
                      label={t('lots.lotNumber')}
                      required
                      error={lineErrors?.lot_number && t('validation.required')}
                    >
                      <InputText
                        id={`line-${index}-lot`}
                        maxLength={50}
                        {...form.register(`lines.${index}.lot_number`)}
                      />
                    </FormField>
                    <FormField
                      id={`line-${index}-expiry`}
                      label={t('lots.expiryDate')}
                      required={Boolean(article?.expiry_tracked)}
                      error={lineErrors?.lot_expiry_date && t('validation.required')}
                    >
                      <InputText
                        id={`line-${index}-expiry`}
                        type="date"
                        {...form.register(`lines.${index}.lot_expiry_date`)}
                      />
                    </FormField>
                    <FormField
                      id={`line-${index}-made`}
                      label={t('lots.manufacturingDate')}
                      error={lineErrors?.lot_manufacturing_date && t('lots.datesInvalid')}
                    >
                      <InputText
                        id={`line-${index}-made`}
                        type="date"
                        {...form.register(`lines.${index}.lot_manufacturing_date`)}
                      />
                    </FormField>
                  </div>
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
            onClick={() => lines.append({ ...EMPTY_LINE, article: null })}
          />
        </div>
      </fieldset>
      {kind === 'exits' && <small className="sm-help">{t('exits.costHelp')}</small>}
      {can(COST_VIEW) && document?.total_amount && !form.formState.isDirty && (
        <p className="sm-total">
          {t('stock.total')} :{' '}
          <strong>
            {formatMoney(document.total_amount, capabilities.tenant.currency, locale)}
          </strong>
        </p>
      )}
      <div className="sm-dialog-actions">
        <Button
          type="button"
          label={t('actions.back')}
          text
          onClick={() => void navigate(`/stock/${kind}`)}
        />
        <Button
          type="submit"
          label={t('stock.saveDraft')}
          outlined
          disabled={busy}
          loading={save.isPending}
        />
        {can(`${config.permission}.validate`) && (
          <Button
            type="button"
            icon="pi pi-check"
            label={t('stock.validate')}
            disabled={busy}
            loading={validate.isPending}
            onClick={() => void onValidate()}
          />
        )}
      </div>
    </form>
  );
}

// --- Page -------------------------------------------------------------------------------------

function DocumentPage({ kind }: { kind: DocumentKind }) {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { can, capabilities } = useCapabilities();
  const { id } = useParams();
  const isNew = id === undefined || id === 'new';
  const config = DOCUMENT_CONFIG[kind];
  const query = useDocument<StockDocument>(kind, isNew ? undefined : id);
  const { cancel } = useDocumentMutations<StockDocument>(kind);
  const [cancelling, setCancelling] = useState(false);

  if (!isNew && query.isPending) {
    return <LoadingState />;
  }
  if (!isNew && query.isError) {
    return <ErrorMessage error={query.error} onRetry={() => void query.refetch()} />;
  }
  const document = isNew ? undefined : query.data;
  const editable =
    document === undefined
      ? can(`${config.permission}.create`)
      : document.status === 'DRAFT' && can(`${config.permission}.update`);
  const canCancel = document?.status === 'VALIDATED' && can(`${config.permission}.cancel`);

  const onCancel = (reason: string) =>
    document &&
    cancel.mutate(
      { id: document.id, reason },
      {
        onSuccess: () => {
          toast.success(t('stock.cancelled', { number: document.number }));
          setCancelling(false);
        },
        onError: (error) => toast.error(stockError(t, error, capabilities.tenant.locale)),
      },
    );

  return (
    <>
      <PageHeader
        title={document ? `${t(`${config.i18n}.one`)} ${document.number}` : t(`${config.i18n}.new`)}
        breadcrumbs={[
          { label: t(`${config.i18n}.title`), to: `/stock/${kind}` },
          { label: document ? document.number : t(`${config.i18n}.new`) },
        ]}
        actions={
          <div className="sm-tags">
            {document && <DocumentStatusBadge status={document.status} />}
            {canCancel && (
              <Button
                icon="pi pi-undo"
                label={t('stock.cancelDocument')}
                severity="danger"
                outlined
                onClick={() => setCancelling(true)}
              />
            )}
          </div>
        }
      />
      {editable ? (
        // Clé : nouveau formulaire après création (identifiant attribué par le serveur).
        <DocumentForm key={document?.id ?? 'new'} kind={kind} document={document} />
      ) : document ? (
        <>
          <DocumentSummary kind={kind} document={document} />
          <div className="sm-dialog-actions">
            <Button
              label={t('actions.back')}
              text
              onClick={() => void navigate(`/stock/${kind}`)}
            />
          </div>
        </>
      ) : (
        <Message severity="error" text={t('errors.forbidden')} />
      )}
      {cancelling && (
        <CancelDialog
          loading={cancel.isPending}
          onConfirm={onCancel}
          onClose={() => setCancelling(false)}
        />
      )}
    </>
  );
}

export function EntryPage() {
  return <DocumentPage kind="entries" />;
}

export function ExitPage() {
  return <DocumentPage kind="exits" />;
}
