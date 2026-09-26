import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
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
  useSubscriptionPayments,
  type SubscriptionPayment,
  type SubscriptionPaymentMethod,
} from './api';

const STATUSES: SubscriptionPaymentStatus[] = ['PENDING', 'CONFIRMED', 'REJECTED'];
const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Jour déclaré (sans heure) : affiché tel quel, sans conversion de fuseau. */
const day = (value: string, locale: string) => formatDate(value, locale, 'UTC');

/**
 * Contrôles d'ergonomie seulement : le serveur revalide tout (montant, période, référence) et
 * fixe lui-même la devise et le statut.
 */
const schema = z
  .object({
    amount: z.string().refine((v) => {
      const normalized = normalizeDecimal(v, 2);
      return normalized !== null && /[1-9]/.test(normalized);
    }, 'amount'),
    period_start: z.string().regex(DAY, 'required'),
    period_end: z.string().regex(DAY, 'required'),
    payment_method: z.enum(SUBSCRIPTION_PAYMENT_METHODS),
    declared_reference: z.string().trim().min(1, 'required').max(100),
  })
  .refine((v) => v.period_end > v.period_start, { path: ['period_end'], message: 'order' });
type FormValues = z.infer<typeof schema>;

function DeclarePaymentDialog({
  subscriptionId,
  onClose,
}: {
  subscriptionId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const declare = useDeclareSubscriptionPayment();
  // Une clé par saisie : double clic ou nouvel envoi après une coupure → aucun doublon.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<unknown>(null);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      amount: '',
      period_start: '',
      period_end: '',
      payment_method: 'BANK_TRANSFER',
      declared_reference: '',
    },
  });
  const errors = form.formState.errors;
  const errorText = (field: keyof FormValues) => {
    const message = errors[field]?.message;
    if (!message) return undefined;
    if (message === 'amount') return t('subscriptionPayments.invalidAmount');
    if (message === 'order') return t('subscriptionPayments.periodOrder');
    if (message === 'required') return t('validation.required');
    if (errors[field]?.type === 'too_big') return t('validation.tooLong');
    return t('validation.invalid');
  };

  const onSubmit = form.handleSubmit((values) => {
    if (declare.isPending) return;
    setError(null);
    declare.mutate(
      {
        subscription_id: subscriptionId,
        amount: normalizeDecimal(values.amount, 2) ?? values.amount,
        period_start: values.period_start,
        period_end: values.period_end,
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
        <FormField
          id="subscription-payment-amount"
          label={t('subscriptionPayments.amount')}
          required
          error={errorText('amount')}
          help={t('subscriptionPayments.amountHelp', {
            currency: capabilities.tenant.currency,
          })}
        >
          <InputText
            id="subscription-payment-amount"
            inputMode="decimal"
            invalid={Boolean(errors.amount)}
            autoFocus
            {...form.register('amount')}
          />
        </FormField>
        <div className="sm-form-grid">
          <FormField
            id="subscription-payment-start"
            label={t('subscriptionPayments.periodStart')}
            required
            error={errorText('period_start')}
          >
            <InputText
              id="subscription-payment-start"
              type="date"
              invalid={Boolean(errors.period_start)}
              {...form.register('period_start')}
            />
          </FormField>
          <FormField
            id="subscription-payment-end"
            label={t('subscriptionPayments.periodEnd')}
            required
            error={errorText('period_end')}
          >
            <InputText
              id="subscription-payment-end"
              type="date"
              invalid={Boolean(errors.period_end)}
              {...form.register('period_end')}
            />
          </FormField>
        </div>
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
          error={errorText('declared_reference')}
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
export function SubscriptionPaymentsSection({ subscriptionId }: { subscriptionId: string }) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { locale, timezone } = capabilities.tenant;
  const [table, setTable] = useState<TableState>(INITIAL_TABLE);
  const [status, setStatus] = useState<SubscriptionPaymentStatus | null>(null);
  const [declaring, setDeclaring] = useState(false);
  const payments = useSubscriptionPayments(toQueryString(table, { status }));
  const canDeclare = can('subscription.payment.declare');

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
            body={(p: SubscriptionPayment) =>
              t('subscriptionPayments.periodValue', {
                start: day(p.period_start, locale),
                end: day(p.period_end, locale),
              })
            }
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
        <DeclarePaymentDialog subscriptionId={subscriptionId} onClose={() => setDeclaring(false)} />
      )}
    </section>
  );
}
