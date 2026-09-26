import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Checkbox } from 'primereact/checkbox';
import { Column } from 'primereact/column';
import { DataTable } from 'primereact/datatable';
import { Dialog } from 'primereact/dialog';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';

import { ApiError } from '@/core/api/client';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { formatDateTime } from '@/shared/lib/format';
import { EmptyState } from '@/shared/ui/EmptyState';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { SubscriptionPaymentStatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import { AuditChanges } from '../auditDisplay';
import { CONSOLE_BASE } from '../ConsoleLayout';
import { useAudit, usePayment, usePaymentDecision } from '../queries';
import { Details, paymentDay } from '../tenantDisplay';
import type { ConsolePayment, PaymentDecision, PlatformAuditEntry } from '../types';

type Kind = PaymentDecision['kind'];

/**
 * Décision TechNova : récapitulatif, raison obligatoire (motif de rejet visible par
 * l'entreprise), case de confirmation explicite, un seul envoi à la fois. Le serveur verrouille
 * le paiement et fait foi : une décision déjà prise (`409`) est affichée et la fiche relue.
 */
function DecisionDialog({
  payment,
  kind,
  onClose,
}: {
  payment: ConsolePayment;
  kind: Kind;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const decision = usePaymentDecision(payment.id);
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [reasonError, setReasonError] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reject = kind === 'reject';
  const alreadyDecided = error instanceof ApiError && error.code === 'payment_already_decided';

  const submit = () => {
    if (decision.isPending) return;
    if (!reason.trim()) {
      setReasonError(true);
      return;
    }
    setError(null);
    decision.mutate(
      { kind, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success(t(reject ? 'console:payment.rejected' : 'console:payment.confirmed'));
          onClose();
        },
        onError: setError,
      },
    );
  };

  const title = t(reject ? 'console:payment.rejectTitle' : 'console:payment.confirmTitle');
  return (
    <Dialog header={title} visible onHide={onClose} className="sm-dialog">
      <form
        className="sm-form"
        noValidate
        aria-label={title}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Details
          items={[
            [t('console:payment.company'), payment.tenant_name],
            [t('console:payment.amount'), formatMoney(payment.amount, payment.currency)],
            [t('console:payment.reference'), payment.declared_reference],
          ]}
        />
        {!reject && <Message severity="warn" text={t('console:payment.noActivation')} />}
        {error !== null && <Message severity="error" text={translateError(t, error)} />}
        <FormField
          id="decision-reason"
          label={t(reject ? 'console:payment.rejectReason' : 'console:payment.reason')}
          required
          help={t(reject ? 'console:payment.rejectReasonHelp' : 'console:payment.reasonHelp')}
          error={reasonError ? t('console:payment.reasonRequired') : undefined}
        >
          <InputTextarea
            id="decision-reason"
            rows={2}
            maxLength={500}
            value={reason}
            invalid={reasonError}
            disabled={alreadyDecided}
            onChange={(e) => {
              setReason(e.target.value);
              setReasonError(false);
            }}
            autoFocus
          />
        </FormField>
        <div className="sm-checkbox">
          <Checkbox
            inputId="decision-confirm"
            checked={confirmed}
            disabled={alreadyDecided}
            onChange={(e) => setConfirmed(e.checked === true)}
          />
          <label htmlFor="decision-confirm">{t('console:payment.confirmCheck')}</label>
        </div>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            label={t(reject ? 'console:payment.rejectAction' : 'console:payment.confirmAction')}
            severity={reject ? 'danger' : undefined}
            disabled={!confirmed || alreadyDecided}
            loading={decision.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}

function PaymentHistory({ paymentId }: { paymentId: string }) {
  const { t } = useTranslation();
  const history = useAudit(20, 0, { target_type: 'subscription_payment', target_id: paymentId });
  if (history.isError) {
    return <ErrorMessage error={history.error} onRetry={() => void history.refetch()} />;
  }
  return (
    <Card title={t('console:payment.history')}>
      <DataTable
        className="sm-table"
        tableStyle={{ minWidth: '40rem' }}
        value={history.data?.items ?? []}
        loading={history.isFetching}
        dataKey="id"
        emptyMessage={<EmptyState icon="pi pi-history" title={t('console:payment.historyEmpty')} />}
      >
        <Column
          header={t('console:audit.date')}
          body={(e: PlatformAuditEntry) => formatDateTime(e.occurred_at, 'fr')}
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

export function PaymentDetailPage() {
  const { t } = useTranslation();
  const { id = '' } = useParams();
  const payment = usePayment(id);
  const [kind, setKind] = useState<Kind | null>(null);

  if (payment.isLoading) return <LoadingState />;
  if (payment.isError || !payment.data) {
    return <ErrorMessage error={payment.error} onRetry={() => void payment.refetch()} />;
  }
  const p = payment.data;
  const pending = p.status === 'PENDING';
  return (
    <>
      <PageHeader
        title={t('console:payment.title', { reference: p.declared_reference })}
        breadcrumbs={[
          { label: t('console:payment.breadcrumb'), to: `${CONSOLE_BASE}/payments` },
          { label: p.declared_reference },
        ]}
        actions={
          <>
            <SubscriptionPaymentStatusBadge status={p.status} />
            <Button
              icon="pi pi-refresh"
              label={t('console:payments.refresh')}
              outlined
              loading={payment.isFetching}
              onClick={() => void payment.refetch()}
            />
          </>
        }
      />
      <div className="sm-dashboard-grid">
        <Card title={t('console:payment.details')}>
          <Details
            items={[
              [
                t('console:payment.company'),
                <Link to={`${CONSOLE_BASE}/tenants/${p.tenant_id}`}>{p.tenant_name}</Link>,
              ],
              [t('console:payment.plan'), p.plan_code],
              [t('console:payment.amount'), formatMoney(p.amount, p.currency), 'payment-amount'],
              [
                t('console:payment.period'),
                t('console:payments.periodValue', {
                  start: paymentDay(p.period_start),
                  end: paymentDay(p.period_end),
                }),
              ],
              [t('console:payment.method'), t(`subscriptionPaymentMethod.${p.payment_method}`)],
              [t('console:payment.reference'), p.declared_reference],
              [t('console:payment.declaredAt'), formatDateTime(p.created_at, 'fr')],
            ]}
          />
        </Card>
        <Card title={t('console:payment.decision')}>
          <Details
            items={[
              [
                t('console:payment.status'),
                <SubscriptionPaymentStatusBadge status={p.status} />,
                'payment-status',
              ],
              [
                t('console:payment.decidedAt'),
                p.decided_at ? formatDateTime(p.decided_at, 'fr') : null,
              ],
              [t('console:payment.decidedBy'), p.decided_by_email],
              ...(p.rejection_reason
                ? [
                    [
                      t('console:payment.rejectionReason'),
                      p.rejection_reason,
                      'payment-rejection-reason',
                    ] as [string, string, string],
                  ]
                : []),
            ]}
          />
          <p className="sm-help">
            {t(pending ? 'console:payment.pendingHelp' : 'console:payment.decidedHelp')}
          </p>
          {pending && (
            <>
              <Message severity="info" text={t('console:payment.noActivation')} />
              <div className="sm-quick-actions" data-testid="payment-actions">
                <Button
                  icon="pi pi-check"
                  label={t('console:payment.confirmAction')}
                  onClick={() => setKind('confirm')}
                />
                <Button
                  icon="pi pi-times"
                  severity="danger"
                  outlined
                  label={t('console:payment.rejectAction')}
                  onClick={() => setKind('reject')}
                />
              </div>
            </>
          )}
        </Card>
      </div>
      <PaymentHistory paymentId={p.id} />
      {kind && <DecisionDialog payment={p} kind={kind} onClose={() => setKind(null)} />}
    </>
  );
}
