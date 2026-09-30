import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import type { TFunction } from 'i18next';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { CashRegisterChoice } from '@/modules/cash_register/CashRegisterChoice';
import { formatMoney } from '@/shared/lib/decimal';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { MetricCard } from '@/shared/ui/MetricCard';
import { RowActions } from '@/shared/ui/RowActions';
import { useToast } from '@/shared/ui/toast';

import {
  useAvailablePaymentMethods,
  usePaymentMutations,
  useSalePayments,
  type Payment,
  type PaymentSummary,
  type Sale,
} from './api';
import {
  defaultMethodId,
  PaymentFields,
  toPaymentInput,
  type PaymentDraft,
  type PaymentDraftErrors,
} from './PaymentFields';
import { PaymentStatusBadge, SalePaymentBadge, paymentError } from './ui';

/** « 70000.00 » → « 70000 » : valeur proposée dans le champ montant (aucun calcul). */
function toInput(value: string): string {
  return value.replace(/\.00$/, '');
}

/** Confirmation d'un paiement : numéro, montant et, en espèces, monnaie calculée par le serveur. */
export function paymentRecordedMessage(
  t: TFunction,
  payment: Payment,
  currency: string,
  locale: string,
): string {
  const recorded = t('payment.recorded', {
    number: payment.number,
    amount: formatMoney(payment.amount, currency, locale),
  });
  return payment.change_given !== null && /[1-9]/.test(payment.change_given)
    ? `${recorded} — ${t('payment.changeGiven', { amount: formatMoney(payment.change_given, currency, locale) })}`
    : recorded;
}

function PaymentDialog({
  sale,
  summary,
  onClose,
}: {
  sale: Sale;
  summary: PaymentSummary;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const { create } = usePaymentMutations(sale.id);
  const { methods, isPending } = useAvailablePaymentMethods(sale.site_id);
  const [draft, setDraft] = useState<PaymentDraft>({
    methodId: null,
    amount: toInput(summary.remaining_amount),
    reference: '',
  });
  const [cashRegisterId, setCashRegisterId] = useState<string | null>(null);
  const [errors, setErrors] = useState<PaymentDraftErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  // Une clé par saisie : double clic ou nouvel envoi après une coupure → aucun doublon.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const remaining = formatMoney(summary.remaining_amount, currency, locale);
  const methodId = draft.methodId ?? defaultMethodId(methods);
  const current = { ...draft, methodId };
  const method = methods.find((m) => m.id === methodId);

  const submit = () => {
    const result = toPaymentInput(t, current, methods);
    if ('errors' in result) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    setServerError(null);
    create.mutate(
      {
        ...result.input,
        idempotency_key: idempotencyKey,
        cash_register_id: method?.kind === 'CASH' ? cashRegisterId : null,
      },
      {
        onSuccess: (payment) => {
          toast.success(paymentRecordedMessage(t, payment, currency, locale));
          onClose();
        },
        // Refus du serveur (ex. montant supérieur au reste, référence manquante) : affiché.
        onError: (error) => setServerError(paymentError(t, error, currency, locale)),
      },
    );
  };

  return (
    <Dialog header={t('payment.record')} visible onHide={onClose} className="sm-dialog">
      <form
        className="sm-form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Message severity="info" text={t('payment.remainingToPay', { amount: remaining })} />
        {isPending ? (
          <LoadingState />
        ) : (
          <PaymentFields
            idPrefix="payment"
            methods={methods}
            draft={current}
            errors={errors}
            onChange={(patch) => setDraft((d) => ({ ...d, methodId, ...patch }))}
            currency={currency}
            autoFocus
          />
        )}
        {method?.kind === 'CASH' && (
          <CashRegisterChoice
            siteId={sale.site_id}
            value={cashRegisterId}
            onChange={setCashRegisterId}
          />
        )}
        {serverError && <Message severity="error" text={serverError} />}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            icon="pi pi-check"
            label={t('payment.save')}
            loading={create.isPending}
            disabled={methods.length === 0}
          />
        </div>
      </form>
    </Dialog>
  );
}

/** Annulation d'un paiement : confirmation explicite et motif obligatoire (audit). */
function CancelPaymentDialog({
  sale,
  payment,
  onClose,
}: {
  sale: Sale;
  payment: Payment;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const { cancel } = usePaymentMutations(sale.id);
  const [reason, setReason] = useState('');
  const amount = formatMoney(payment.amount, currency, locale);
  const submit = () =>
    cancel.mutate(
      { id: payment.id, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success(t('payment.cancelled', { number: payment.number }));
          onClose();
        },
        onError: (error) => toast.error(paymentError(t, error, currency, locale)),
      },
    );
  return (
    <Dialog
      header={t('payment.cancelTitle', { amount })}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <div className="sm-form">
        <Message severity="warn" text={t('payment.cancelWarning')} />
        <FormField
          id="payment-cancel-reason"
          label={t('stock.cancellationReason')}
          help={t('stock.cancellationReasonHelp')}
          required
        >
          <InputTextarea
            id="payment-cancel-reason"
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
            label={t('payment.cancel')}
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

/**
 * Encaissement d'une vente validée, sur sa fiche : résumé (total, payé, reste, état),
 * enregistrement de paiements successifs (paiement mixte = plusieurs paiements), historique
 * complet (paiements annulés compris), annulation. Le serveur calcule et contrôle tout.
 */
export function PaymentsPanel({ sale }: { sale: Sale }) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale, timezone } = capabilities.tenant;
  const payments = useSalePayments(sale.id, true);
  const [recording, setRecording] = useState(false);
  const [cancelling, setCancelling] = useState<Payment | null>(null);

  if (payments.isPending) return <LoadingState />;
  if (payments.isError) {
    return <ErrorMessage error={payments.error} onRetry={() => void payments.refetch()} />;
  }
  const { summary, items } = payments.data;
  const money = (value: string) => formatMoney(value, currency, locale);
  const canRecord =
    summary !== null && summary.payment_status !== 'PAID' && can('sales.payment.create');
  const canCancel = can('sales.payment.cancel');

  return (
    <section className="sm-block" aria-labelledby="payments-title">
      <div className="sm-section-header">
        <h2 id="payments-title">{t('payment.title')}</h2>
        {summary && <SalePaymentBadge status={summary.payment_status} />}
        {canRecord && (
          <Button
            icon="pi pi-wallet"
            label={t('payment.record')}
            onClick={() => setRecording(true)}
          />
        )}
      </div>
      {summary && (
        <div className="sm-metrics" role="group" aria-label={t('payment.summary')}>
          <MetricCard
            icon="pi pi-receipt"
            value={money(summary.total)}
            label={t('payment.total')}
          />
          <MetricCard
            icon="pi pi-check-circle"
            tone="success"
            value={money(summary.paid_amount)}
            label={t('payment.paid')}
          />
          <MetricCard
            icon="pi pi-clock"
            tone={summary.payment_status === 'PAID' ? 'success' : 'warning'}
            value={money(summary.remaining_amount)}
            label={t('payment.remaining')}
            hint={
              sale.customer_id && summary.payment_status !== 'PAID'
                ? t('payment.receivableHint')
                : undefined
            }
          />
        </div>
      )}
      <Card title={t('payment.history')}>
        <DataTable
          className="sm-table"
          value={items}
          dataKey="id"
          rowHover
          tableStyle={{ minWidth: '44rem' }}
          emptyMessage={<EmptyState icon="pi pi-wallet" title={t('payment.empty')} />}
        >
          <Column
            header={t('payment.date')}
            body={(p: Payment) => formatDateTime(p.paid_at, locale, timezone)}
          />
          <Column field="number" header={t('payment.number')} bodyClassName="sm-nowrap" />
          <Column
            header={t('payment.methodLabel')}
            body={(p: Payment) => (
              <div>
                {p.provider ? `${p.method_label} — ${p.provider}` : p.method_label}
                {p.amount_received !== null && (
                  <div className="sm-help">
                    {t('payment.receivedAndChange', {
                      received: money(p.amount_received),
                      change: money(p.change_given ?? '0'),
                    })}
                  </div>
                )}
              </div>
            )}
          />
          <Column header={t('payment.reference')} body={(p: Payment) => p.reference ?? '—'} />
          <Column
            header={t('payment.amount')}
            headerClassName="sm-num"
            bodyClassName="sm-num"
            body={(p: Payment) => (
              <span className={p.status === 'CANCELLED' ? 'sm-struck' : undefined}>
                {money(p.amount)}
              </span>
            )}
          />
          <Column
            header={t('payment.statusLabel')}
            body={(p: Payment) => (
              <div>
                <PaymentStatusBadge status={p.status} />
                {p.status === 'CANCELLED' && (
                  <div className="sm-help">
                    {t('payment.cancelledBy', {
                      name: p.cancelled_by_name ?? '',
                      reason: p.cancellation_reason ?? '',
                    })}
                  </div>
                )}
              </div>
            )}
          />
          <Column field="created_by_name" header={t('payment.recordedBy')} />
          {canCancel && (
            <Column
              header={t('common.actions')}
              body={(p: Payment) => (
                <RowActions
                  actions={[
                    {
                      key: 'cancel',
                      label: t('payment.cancel'),
                      icon: 'pi pi-times',
                      danger: true,
                      hidden: p.status === 'CANCELLED',
                      onClick: () => setCancelling(p),
                    },
                  ]}
                />
              )}
            />
          )}
        </DataTable>
      </Card>
      {recording && summary && (
        <PaymentDialog sale={sale} summary={summary} onClose={() => setRecording(false)} />
      )}
      {cancelling && (
        <CancelPaymentDialog sale={sale} payment={cancelling} onClose={() => setCancelling(null)} />
      )}
    </section>
  );
}
