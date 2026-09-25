import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dropdown } from 'primereact/dropdown';
import { InputSwitch } from 'primereact/inputswitch';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useEffect, useMemo, useState } from 'react';
import { Controller, useForm, type Control } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router';
import { z } from 'zod';

import { normalizeDecimal } from '@/shared/lib/decimal';
import { invalidFields, translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { confirmAction } from '@/shared/ui/confirm';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { FormSection } from '@/shared/ui/FormSection';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import { AuditChanges } from '../auditDisplay';
import { CONSOLE_BASE } from '../ConsoleLayout';
import { PlanModeBadge, PlanStatusBadge, planPrice, trialLabel } from '../planDisplay';
import { useAudit, useCatalog, usePlan, useUpdatePlanCommercial } from '../queries';
import type {
  PlanCommercial,
  PlanCommercialUpdate,
  PlanDetail,
  PlanStructure,
  PlatformAuditEntry,
} from '../types';

const amount = z
  .string()
  .refine((v) => v.trim() === '' || normalizeDecimal(v, 2) !== null, 'console:plan.priceInvalid');
const integer = z
  .string()
  .trim()
  .regex(/^\d{1,4}$/, 'console:plan.integerInvalid');

const schema = z.object({
  listed: z.boolean(),
  price_display_enabled: z.boolean(),
  monthly_price: amount,
  monthly_price_enabled: z.boolean(),
  annual_price: amount,
  annual_price_enabled: z.boolean(),
  currency: z.string().nullable(),
  contact_required: z.boolean(),
  commercial_description: z.string().max(2000),
  display_order: integer,
  trial_days: integer,
  reason: z.string().trim().min(1, 'console:plan.reasonRequired').max(500),
});
type FormValues = z.infer<typeof schema>;
type Field = keyof PlanCommercial;

/** Ordre d'affichage des champs dans le récapitulatif de confirmation. */
const FIELDS: Field[] = [
  'listed',
  'monthly_price_enabled',
  'monthly_price',
  'annual_price_enabled',
  'annual_price',
  'currency',
  'price_display_enabled',
  'contact_required',
  'commercial_description',
  'display_order',
  'trial_days',
];
const PRICES = new Set<Field>(['monthly_price', 'annual_price']);

function toForm(plan: PlanCommercial): FormValues {
  return {
    listed: plan.listed,
    price_display_enabled: plan.price_display_enabled,
    monthly_price: plan.monthly_price ?? '',
    monthly_price_enabled: plan.monthly_price_enabled,
    annual_price: plan.annual_price ?? '',
    annual_price_enabled: plan.annual_price_enabled,
    currency: plan.currency,
    contact_required: plan.contact_required,
    commercial_description: plan.commercial_description ?? '',
    display_order: String(plan.display_order),
    trial_days: String(plan.trial_days),
    reason: '',
  };
}

/** Montant canonique pour comparer deux saisies (« 10000 » = « 10000.00 ») sans calcul. */
function canonical(value: string | null): string | null {
  if (value === null) return null;
  return value.includes('.') ? value.replace(/0+$/, '').replace(/\.$/, '') : value;
}

/** Valeurs saisies → champs commerciaux (types de l'API). */
function fromForm(values: FormValues): PlanCommercial {
  const price = (v: string) => (v.trim() === '' ? null : normalizeDecimal(v, 2));
  return {
    listed: values.listed,
    price_display_enabled: values.price_display_enabled,
    monthly_price: price(values.monthly_price),
    monthly_price_enabled: values.monthly_price_enabled,
    annual_price: price(values.annual_price),
    annual_price_enabled: values.annual_price_enabled,
    currency: values.currency,
    contact_required: values.contact_required,
    commercial_description: values.commercial_description.trim() || null,
    display_order: Number(values.display_order),
    trial_days: Number(values.trial_days),
  };
}

/** Champs réellement modifiés ; le serveur revalide tout (aucune règle métier ici). */
function changedFields(plan: PlanCommercial, next: PlanCommercial): Field[] {
  return FIELDS.filter((field) =>
    PRICES.has(field)
      ? canonical(plan[field] as string | null) !== canonical(next[field] as string | null)
      : plan[field] !== next[field],
  );
}

function SwitchField({
  control,
  name,
  label,
}: {
  control: Control<FormValues>;
  name: Extract<
    Field,
    | 'listed'
    | 'price_display_enabled'
    | 'monthly_price_enabled'
    | 'annual_price_enabled'
    | 'contact_required'
  >;
  label: string;
}) {
  return (
    <Controller
      control={control}
      name={name}
      render={({ field }) => (
        <label className="sm-checkbox" htmlFor={name}>
          <InputSwitch
            inputId={name}
            checked={field.value}
            onChange={(e) => field.onChange(Boolean(e.value))}
          />
          <span>{label}</span>
        </label>
      )}
    />
  );
}

function CommercialForm({ plan }: { plan: PlanDetail }) {
  const { t } = useTranslation();
  const toast = useToast();
  const update = useUpdatePlanCommercial(plan.code);
  const catalog = useCatalog();
  const [serverError, setServerError] = useState<unknown>(null);
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: toForm(plan) });
  const errors = form.formState.errors;

  useEffect(() => form.reset(toForm(plan)), [plan, form]);

  const currencies = useMemo(() => {
    const codes = new Set(catalog.data?.currencies ?? []);
    if (plan.currency) codes.add(plan.currency);
    return [...codes].sort().map((code) => ({ label: code, value: code }));
  }, [catalog.data, plan.currency]);

  const rejected = invalidFields(serverError);
  const errorOf = (name: keyof FormValues): string | undefined => {
    const message = errors[name]?.message;
    if (message) return t(message);
    return rejected.includes(name) ? t('errors:validation_error') : undefined;
  };

  const show = (field: Field, values: PlanCommercial): string => {
    const value = values[field];
    if (typeof value === 'boolean') return t(value ? 'console:plans.yes' : 'console:plans.no');
    if (PRICES.has(field)) return planPrice(value as string | null, values.currency);
    if (field === 'trial_days') return trialLabel(t, value as number);
    return value === null || value === '' ? t('console:plans.none') : String(value);
  };

  const onSubmit = form.handleSubmit((values) => {
    setServerError(null);
    const next = fromForm(values);
    const changed = changedFields(plan, next);
    if (changed.length === 0) {
      toast.error(t('console:plan.noChanges'));
      return;
    }
    const lines = changed.map(
      (f) => `• ${t(`console:plan.fields.${f}`)} : ${show(f, plan)} → ${show(f, next)}`,
    );
    confirmAction(t, {
      header: t('console:plan.confirmHeader'),
      message: [t('console:plan.confirmMessage', { plan: plan.name }), ...lines].join('\n'),
      acceptLabel: t('console:plan.confirmAccept'),
      onAccept: async () => {
        const body: PlanCommercialUpdate = { reason: values.reason.trim() };
        for (const field of changed) Object.assign(body, { [field]: next[field] });
        try {
          await update.mutateAsync(body);
          toast.success(t('console:plan.saved'));
        } catch (error) {
          setServerError(error);
        }
      },
    });
  });

  return (
    <form
      onSubmit={onSubmit}
      className="sm-form"
      noValidate
      aria-label={t('console:plan.commercialTitle')}
    >
      <p className="sm-help">{t('console:plan.commercialHelp')}</p>
      {serverError !== null && (
        <Message severity="error" className="sm-block" text={translateError(t, serverError)} />
      )}
      <FormSection
        title={t('console:plan.publication')}
        description={t('console:plan.publicationHelp')}
      >
        <SwitchField control={form.control} name="listed" label={t('console:plan.fields.listed')} />
      </FormSection>
      <FormSection title={t('console:plan.pricing')} description={t('console:plan.pricingHelp')}>
        <div className="sm-form-grid">
          <div className="sm-stack">
            <SwitchField
              control={form.control}
              name="monthly_price_enabled"
              label={t('console:plan.fields.monthly_price_enabled')}
            />
            <FormField
              id="monthly_price"
              label={t('console:plan.fields.monthly_price')}
              error={errorOf('monthly_price')}
            >
              <InputText
                id="monthly_price"
                inputMode="decimal"
                {...form.register('monthly_price')}
              />
            </FormField>
          </div>
          <div className="sm-stack">
            <SwitchField
              control={form.control}
              name="annual_price_enabled"
              label={t('console:plan.fields.annual_price_enabled')}
            />
            <FormField
              id="annual_price"
              label={t('console:plan.fields.annual_price')}
              error={errorOf('annual_price')}
            >
              <InputText id="annual_price" inputMode="decimal" {...form.register('annual_price')} />
            </FormField>
          </div>
        </div>
        <FormField
          id="currency"
          label={t('console:plan.fields.currency')}
          error={errorOf('currency')}
        >
          <Controller
            control={form.control}
            name="currency"
            render={({ field }) => (
              <Dropdown
                inputId="currency"
                value={field.value}
                options={currencies}
                filter
                showClear
                placeholder={t('console:plan.chooseCurrency')}
                onChange={(e) => field.onChange((e.value as string | undefined) ?? null)}
              />
            )}
          />
        </FormField>
      </FormSection>
      <FormSection title={t('console:plan.commercial')}>
        <SwitchField
          control={form.control}
          name="price_display_enabled"
          label={t('console:plan.fields.price_display_enabled')}
        />
        <SwitchField
          control={form.control}
          name="contact_required"
          label={t('console:plan.fields.contact_required')}
        />
        <FormField
          id="commercial_description"
          label={t('console:plan.fields.commercial_description')}
          help={t('console:plan.descriptionHelp')}
          error={errorOf('commercial_description')}
        >
          <InputTextarea
            id="commercial_description"
            rows={3}
            autoResize
            {...form.register('commercial_description')}
          />
        </FormField>
        <FormField
          id="display_order"
          label={t('console:plan.fields.display_order')}
          help={t('console:plan.orderHelp')}
          error={errorOf('display_order')}
        >
          <InputText id="display_order" inputMode="numeric" {...form.register('display_order')} />
        </FormField>
      </FormSection>
      <FormSection title={t('console:plan.trial')}>
        <FormField
          id="trial_days"
          label={t('console:plan.fields.trial_days')}
          help={t('console:plan.trialHelp')}
          error={errorOf('trial_days')}
        >
          <InputText id="trial_days" inputMode="numeric" {...form.register('trial_days')} />
        </FormField>
      </FormSection>
      <FormField
        id="reason"
        label={t('console:plan.reason')}
        required
        help={t('console:plan.reasonHelp')}
        error={errorOf('reason')}
      >
        <InputTextarea
          id="reason"
          rows={2}
          autoResize
          invalid={Boolean(errors.reason)}
          {...form.register('reason')}
        />
      </FormField>
      <div className="sm-form-actions">
        <Button
          type="button"
          text
          label={t('console:plan.reset')}
          onClick={() => {
            setServerError(null);
            form.reset(toForm(plan));
          }}
        />
        <Button
          type="submit"
          icon="pi pi-check"
          label={t('console:plan.save')}
          loading={update.isPending}
        />
      </div>
    </form>
  );
}

function StructureCard({ structure }: { structure: PlanStructure }) {
  const { t } = useTranslation();
  const byModule = useMemo(() => {
    const groups = new Map<string, PlanStructure['permissions']>();
    for (const p of structure.permissions)
      groups.set(p.module, [...(groups.get(p.module) ?? []), p]);
    return [...groups.entries()];
  }, [structure.permissions]);
  return (
    <Card
      title={
        <span className="sm-tags">
          {t('console:plan.structureTitle')}
          <StatusBadge tone="neutral" icon="pi pi-lock" label={t('console:plan.readOnly')} />
        </span>
      }
    >
      <div className="sm-stack" data-testid="plan-structure">
        <p className="sm-help">{t('console:plan.structureHelp')}</p>
        <h3 className="sm-section-title">{t('console:plan.modules')}</h3>
        <ul className="sm-chips">
          {structure.modules.map((m) => (
            <li key={m.code}>
              {t(`modules.${m.code}`, { defaultValue: m.code })}
              {m.core && <small className="sm-muted">({t('console:plan.coreModule')})</small>}
              {m.status === 'planned' && (
                <small className="sm-muted">({t('console:plan.plannedModule')})</small>
              )}
            </li>
          ))}
        </ul>
        <h3 className="sm-section-title">{t('console:plan.features')}</h3>
        {structure.features.length ? (
          <ul className="sm-chips">
            {structure.features.map((f) => (
              <li key={f} className="sm-code">
                {f}
              </li>
            ))}
          </ul>
        ) : (
          <p className="sm-muted">{t('console:plan.noFeatures')}</p>
        )}
        <dl className="sm-details">
          {Object.entries(structure.limits).map(([code, value]) => (
            <div key={code}>
              <dt>
                {t('console:plan.limits')} · {t(`console:limits.${code}`, { defaultValue: code })}
              </dt>
              <dd data-testid={`limit-${code}`}>{value ?? t('console:plan.unlimited')}</dd>
            </div>
          ))}
          <div>
            <dt>{t('console:plan.graceDays')}</dt>
            <dd>{t('console:plans.trialDays', { count: structure.grace_days })}</dd>
          </div>
        </dl>
        <details>
          <summary>
            {t('console:plan.permissions', { count: structure.permissions.length })}
          </summary>
          {byModule.map(([module, permissions]) => (
            <div key={module} className="sm-permission-group">
              <h4>{t(`modules.${module}`, { defaultValue: module })}</h4>
              <ul className="sm-plain-list">
                {permissions.map((p) => (
                  <li key={p.code}>
                    <span>{t(`permissions.${p.code}`, { defaultValue: p.code })}</span>
                    <small className="sm-muted">
                      {p.code} · {t(`console:accessKind.${p.access}`)}
                    </small>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </details>
      </div>
    </Card>
  );
}

function PlanHistory({ code }: { code: string }) {
  const { t } = useTranslation();
  const history = useAudit(20, 0, { target_type: 'plan', target_id: code });
  if (history.isError)
    return <ErrorMessage error={history.error} onRetry={() => void history.refetch()} />;
  return (
    <Card title={t('console:plan.historyTitle')}>
      <DataTable
        className="sm-table"
        tableStyle={{ minWidth: '48rem' }}
        value={history.data?.items ?? []}
        loading={history.isFetching}
        dataKey="id"
        emptyMessage={<EmptyState icon="pi pi-history" title={t('console:plan.historyEmpty')} />}
      >
        <Column
          header={t('console:audit.date')}
          bodyClassName="sm-nowrap"
          body={(e: PlatformAuditEntry) => formatDateTime(e.occurred_at)}
        />
        <Column field="actor_label" header={t('console:audit.actor')} />
        <Column field="action" header={t('console:audit.action')} bodyClassName="sm-nowrap" />
        <Column
          header={t('console:audit.changes')}
          body={(e: PlatformAuditEntry) => <AuditChanges entry={e} />}
        />
        <Column field="reason" header={t('console:audit.reason')} />
      </DataTable>
    </Card>
  );
}

export function PlanDetailPage() {
  const { t } = useTranslation();
  const { code = '' } = useParams();
  const plan = usePlan(code);

  if (plan.isLoading) return <LoadingState />;
  if (plan.isError || !plan.data) {
    return <ErrorMessage error={plan.error} onRetry={() => void plan.refetch()} />;
  }
  const p = plan.data;
  return (
    <>
      <PageHeader
        title={`${p.name} (${p.code})`}
        description={p.description ?? undefined}
        breadcrumbs={[
          { label: t('console:plan.breadcrumb'), to: `${CONSOLE_BASE}/plans` },
          { label: p.name },
        ]}
        actions={
          <span className="sm-tags" data-testid="plan-badges">
            <PlanStatusBadge plan={p} />
            <PlanModeBadge plan={p} />
          </span>
        }
      />
      <div className="sm-company-layout">
        <Card title={t('console:plan.commercialTitle')} className="sm-form-card">
          <CommercialForm plan={p} />
        </Card>
        <StructureCard structure={p.structure} />
      </div>
      <PlanHistory code={p.code} />
    </>
  );
}
