import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputNumber } from 'primereact/inputnumber';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import type { TFunction } from 'i18next';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { formatDate, formatDateTime } from '@/shared/lib/format';
import { INITIAL_TABLE, toQueryString, type TableState } from '@/shared/lib/serverTable';
import { ListEmpty } from '@/shared/ui/EmptyState';
import { FilterBar } from '@/shared/ui/FilterBar';
import { FormField } from '@/shared/ui/FormField';
import { ServerTable } from '@/shared/ui/ServerTable';
import {
  SubscriptionPaymentStatusBadge,
  type SubscriptionPaymentStatus,
} from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  SUBSCRIPTION_PAYMENT_METHODS,
  useDeclareSubscriptionPayment,
  useRenewalQuote,
  useSubscriptionPayments,
  type RenewalQuote,
  type SubscriptionDetails,
  type SubscriptionPayment,
  type SubscriptionPaymentMethod,
} from './api';

const STATUSES: SubscriptionPaymentStatus[] = ['PENDING', 'CONFIRMED', 'REJECTED'];

/** Site d'un abonnement (ou « en attente du premier site ») et son offre. */
function subscriptionLabel(t: TFunction, s: SubscriptionDetails): string {
  return `${s.site?.name ?? t('subscriptionPage.unattached')} · ${s.plan_name}`;
}
/** Jour (sans heure) du fuseau de l'entreprise : affiché tel quel, sans conversion. */
const day = (value: string, locale: string) => formatDate(value, locale, 'UTC');

const validAmount = (v: string) => {
  const normalized = normalizeDecimal(v, 2);
  return normalized !== null && /[1-9]/.test(normalized);
};

/**
 * Contrôles d'ergonomie seulement : période, postes et montant sont calculés par le serveur
 * (R3) ; il revalide tout et fixe lui-même la devise et le statut.
 */
const schema = z.object({
  amount: z.string(),
  payment_method: z.enum(SUBSCRIPTION_PAYMENT_METHODS),
  declared_reference: z.string().trim().min(1, 'required').max(100),
});
type FormValues = z.infer<typeof schema>;

/** Prochaine période du site telle que calculée par le serveur. */
function QuoteSummary({ quote, locale }: { quote: RenewalQuote; locale: string }) {
  const { t } = useTranslation();
  return (
    <dl className="sm-details" data-testid="renewal-quote">
      <div>
        <dt>{t('renewal.period')}</dt>
        <dd>
          {t('renewal.periodValue', {
            start: day(quote.valid_from, locale),
            end: day(quote.valid_until, locale),
          })}
        </dd>
      </div>
      <div>
        <dt>{t('renewal.plan')}</dt>
        <dd>
          {quote.plan.name} · {t(`billingPeriod.${quote.billing_period}`)}
        </dd>
      </div>
      <div>
        <dt>{t('renewal.postes')}</dt>
        <dd data-testid="renewal-postes">
          {t('renewal.postesValue', { count: quote.activations })}
          {quote.activations_explicit && ` · ${t('renewal.postesChanged')}`}
        </dd>
      </div>
      {quote.amount !== null && quote.currency && (
        <div>
          <dt>{t('renewal.amount')}</dt>
          <dd className="sm-strong" data-testid="renewal-amount">
            {formatMoney(quote.amount, quote.currency, locale)}
          </dd>
        </div>
      )}
    </dl>
  );
}

export function DeclarePaymentDialog({
  subscriptions,
  initialSubscriptionId,
  onClose,
}: {
  subscriptions: SubscriptionDetails[];
  initialSubscriptionId?: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const locale = capabilities.tenant.locale;
  const declare = useDeclareSubscriptionPayment();
  // Une clé par saisie : double clic ou nouvel envoi après une coupure → aucun doublon.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<unknown>(null);
  // Abonnement payé : celui d'un site (1 site = 1 abonnement, ADR-0033).
  const [subscriptionId, setSubscriptionId] = useState(
    initialSubscriptionId ?? subscriptions[0]?.id ?? '',
  );
  // R1 : les postes sont reconduits ; un autre nombre n'est demandé qu'explicitement.
  const [changePostes, setChangePostes] = useState(false);
  const current = subscriptions.find((s) => s.id === subscriptionId)?.renewal.activations ?? 1;
  const [postes, setPostes] = useState<number>(current);
  const quote = useRenewalQuote(subscriptionId, changePostes ? postes : null);
  const [amountError, setAmountError] = useState(false);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { amount: '', payment_method: 'BANK_TRANSFER', declared_reference: '' },
  });
  const errors = form.formState.errors;
  const needsAmount = quote.data !== undefined && quote.data.amount === null;
  const referenceError = errors.declared_reference
    ? errors.declared_reference.type === 'too_big'
      ? t('validation.tooLong')
      : t('validation.required')
    : undefined;

  const onSubmit = form.handleSubmit((values) => {
    if (declare.isPending || !quote.data) return;
    if (needsAmount && !validAmount(values.amount)) {
      setAmountError(true);
      return;
    }
    setError(null);
    declare.mutate(
      {
        subscription_id: subscriptionId,
        ...(needsAmount ? { amount: normalizeDecimal(values.amount, 2) ?? values.amount } : {}),
        ...(changePostes ? { requested_activations: postes } : {}),
        payment_method: values.payment_method,
        declared_reference: values.declared_reference.trim(),
        idempotency_key: idempotencyKey,
      },
      {
        onSuccess: () => {
          toast.success(t('subscriptionPayments.declared'));
          onClose();
        },
        onError: setError,
      },
    );
  });

  return (
    <Dialog
      header={t('subscriptionPayments.dialogTitle')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form
        className="sm-form"
        noValidate
        aria-label={t('subscriptionPayments.dialogTitle')}
        onSubmit={(e) => void onSubmit(e)}
      >
        <Message severity="info" text={t('subscriptionPayments.dialogInfo')} />
        {error !== null && <Message severity="error" text={translateError(t, error)} />}
        {subscriptions.length > 1 && (
          <FormField
            id="subscription-payment-subscription"
            label={t('subscriptionPayments.subscription')}
            required
          >
            <Dropdown
              inputId="subscription-payment-subscription"
              value={subscriptionId}
              onChange={(e) => {
                const next = subscriptions.find((s) => s.id === e.value);
                setSubscriptionId(e.value as string);
                setChangePostes(false);
                setPostes(next?.renewal.activations ?? 1);
              }}
              options={subscriptions.map((s) => ({
                value: s.id,
                label: subscriptionLabel(t, s),
              }))}
            />
          </FormField>
        )}
        {quote.isError && <Message severity="error" text={translateError(t, quote.error)} />}
        {quote.data && (
          <>
            <QuoteSummary quote={quote.data} locale={locale} />
            {quote.data.grace_continuity && (
              <Message severity="warn" text={t('renewal.graceContinuity')} />
            )}
          </>
        )}
        <div className="sm-checkbox">
          <Checkbox
            inputId="subscription-payment-change-postes"
            checked={changePostes}
            onChange={(e) => setChangePostes(Boolean(e.checked))}
          />
          <label htmlFor="subscription-payment-change-postes">{t('renewal.changePostes')}</label>
        </div>
        {changePostes && (
          <FormField
            id="subscription-payment-postes"
            label={t('renewal.requestedPostes')}
            required
            help={t('renewal.requestedPostesHelp')}
          >
            <InputNumber
              inputId="subscription-payment-postes"
              value={postes}
              min={1}
              max={10000}
              showButtons
              onValueChange={(e) => setPostes(Math.max(1, Math.min(10000, e.value ?? 1)))}
            />
          </FormField>
        )}
        {needsAmount && (
          <FormField
            id="subscription-payment-amount"
            label={t('subscriptionPayments.amount')}
            required
            error={amountError ? t('subscriptionPayments.invalidAmount') : undefined}
            help={t('subscriptionPayments.amountHelp', {
              currency: capabilities.tenant.currency,
            })}
          >
            <InputText
              id="subscription-payment-amount"
              inputMode="decimal"
              invalid={amountError}
              {...form.register('amount', { onChange: () => setAmountError(false) })}
            />
          </FormField>
        )}
        <FormField
          id="subscription-payment-method"
          label={t('subscriptionPayments.method')}
          required
        >
          <Controller
            control={form.control}
            name="payment_method"
            render={({ field }) => (
              <Dropdown
                inputId="subscription-payment-method"
                value={field.value}
                onChange={(e) => field.onChange(e.value as SubscriptionPaymentMethod)}
                options={SUBSCRIPTION_PAYMENT_METHODS.map((m) => ({
                  value: m,
                  label: t(`subscriptionPaymentMethod.${m}`),
                }))}
              />
            )}
          />
        </FormField>
        <FormField
          id="subscription-payment-reference"
          label={t('subscriptionPayments.reference')}
          required
          error={referenceError}
          help={t('subscriptionPayments.referenceHelp')}
        >
          <InputText
            id="subscription-payment-reference"
            maxLength={100}
            invalid={Boolean(errors.declared_reference)}
            {...form.register('declared_reference')}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            icon="pi pi-send"
            label={t('subscriptionPayments.submit')}
            loading={declare.isPending}
            disabled={!quote.data}
          />
        </div>
      </form>
    </Dialog>
  );
}

/**
 * Paiements de l'abonnement à TechNova : historique (statut, motif de rejet) et déclaration.
 * La confirmation appartient à TechNova seule ; aucun statut n'est calculé ici.
 */
export function SubscriptionPaymentsSection({
  subscriptions,
}: {
  subscriptions: SubscriptionDetails[];
}) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [status, setStatus] = useState<SubscriptionPaymentStatus | null>(null);
  const [declaring, setDeclaring] = useState(false);
  const payments = useSubscriptionPayments(toQueryString(table, { status }));
  const canDeclare = can('subscription.payment.declare');
  const bySubscription = new Map(subscriptions.map((s) => [s.id, s]));

  return (
    <section className="sm-block" aria-labelledby="subscription-payments-title">
      <div className="sm-section-header">
        <h2 id="subscription-payments-title">{t('subscriptionPayments.title')}</h2>
        {canDeclare && (
          <Button
            icon="pi pi-plus"
            label={t('subscriptionPayments.declare')}
            onClick={() => setDeclaring(true)}
          />
        )}
      </div>
      <p className="sm-help">{t('subscriptionPayments.help')}</p>
      <Card>
        <FilterBar
          active={status !== null}
          onReset={() => {
            setStatus(null);
            setTable((s) => ({ ...s, first: 0 }));
          }}
        >
          <Dropdown
            aria-label={t('subscriptionPayments.statusFilter')}
            data-testid="subscription-payment-status-filter"
            value={status}
            options={[
              { label: t('subscriptionPayments.allStatuses'), value: null },
              ...STATUSES.map((s) => ({ label: t(`subscriptionPaymentStatus.${s}`), value: s })),
            ]}
            onChange={(e) => {
              setStatus(e.value as SubscriptionPaymentStatus | null);
              setTable((s) => ({ ...s, first: 0 }));
            }}
          />
        </FilterBar>
        <ServerTable
          query={payments}
          table={table}
          onTableChange={setTable}
          minWidth="56rem"
          empty={
            <ListEmpty
              filtered={status !== null}
              icon="pi pi-wallet"
              title={t('subscriptionPayments.empty')}
            />
          }
        >
          <Column
            field="created_at"
            sortable
            header={t('subscriptionPayments.date')}
            bodyClassName="sm-nowrap"
            body={(p: SubscriptionPayment) => formatDateTime(p.created_at, locale, timezone)}
          />
          <Column
            header={t('subscriptionPayments.site')}
            body={(p: SubscriptionPayment) => {
              const subscription = bySubscription.get(p.subscription_id);
              return subscription ? subscriptionLabel(t, subscription) : '—';
            }}
          />
          <Column
            field="amount"
            sortable
            header={t('subscriptionPayments.amount')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(p: SubscriptionPayment) => formatMoney(p.amount, p.currency, locale)}
          />
          <Column
            field="period_start"
            sortable
            header={t('subscriptionPayments.period')}
            body={(p: SubscriptionPayment) => (
              <div>
                {t('subscriptionPayments.periodValue', {
                  start: day(p.period_start, locale),
                  end: day(p.period_end, locale),
                })}
                {p.requested_activations !== null && (
                  <div className="sm-help">
                    {t('renewal.postesRequested', { count: p.requested_activations })}
                  </div>
                )}
              </div>
            )}
          />
          <Column
            header={t('subscriptionPayments.method')}
            body={(p: SubscriptionPayment) => t(`subscriptionPaymentMethod.${p.payment_method}`)}
          />
          <Column field="declared_reference" header={t('subscriptionPayments.reference')} />
          <Column
            field="status"
            sortable
            header={t('subscriptionPayments.status')}
            body={(p: SubscriptionPayment) => (
              <div>
                <SubscriptionPaymentStatusBadge status={p.status} />
                {p.decided_at && (
                  <div className="sm-help">
                    {t('subscriptionPayments.decidedAt', {
                      date: formatDateTime(p.decided_at, locale, timezone),
                    })}
                  </div>
                )}
                {p.rejection_reason && (
                  <div className="sm-help" data-testid="rejection-reason">
                    {t('subscriptionPayments.rejectionReason', { reason: p.rejection_reason })}
                  </div>
                )}
              </div>
            )}
          />
        </ServerTable>
      </Card>
      {declaring && (
        <DeclarePaymentDialog subscriptions={subscriptions} onClose={() => setDeclaring(false)} />
      )}
    </section>
  );
}
