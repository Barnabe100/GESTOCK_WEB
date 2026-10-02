import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Dialog } from 'primereact/dialog';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { CashRegisterChoice } from '@/modules/cash_register/CashRegisterChoice';
import { formatMoney } from '@/shared/lib/decimal';
import { FormField } from '@/shared/ui/FormField';
import { LoadingState } from '@/shared/ui/LoadingState';

import type { ExpiredShortage } from '@/modules/stock/ui';

import {
  useAvailablePaymentMethods,
  type CreditOverride,
  type ExpiredLotOverride,
  type ImmediatePayment,
} from './api';
import { ExpiredLotOverridePanel } from './ExpiredLotOverridePanel';
import {
  defaultMethodId,
  PaymentFields,
  toPaymentInput,
  type PaymentDraft,
  type PaymentDraftErrors,
} from './PaymentFields';

/**
 * Confirmation de la validation d'une vente, avec encaissement immédiat facultatif (même
 * transaction). Le reste dû est une vente à crédit : client identifié obligatoire, permission
 * de vendre à crédit, limite de crédit du client. Dépassement de la limite : seulement si le
 * serveur l'autorise pour cet utilisateur (`overrideAllowed`), avec une justification. Lot
 * 3-H-A : stock non périmé insuffisant (`expiredShortages`) — dérogation explicite proposée à
 * qui détient la permission. Le serveur recalcule et contrôle tout (montants, monnaie, crédit,
 * lots).
 */
export function ValidateSaleDialog({
  total,
  siteId,
  hasCustomer,
  pending,
  error = null,
  overrideAllowed = false,
  expiredShortages = [],
  onConfirm,
  onClose,
}: {
  /** Total indicatif (chaîne décimale). */
  total: string;
  /** Site de la vente : moyens disponibles et caisse des encaissements en espèces. */
  siteId: string | null;
  hasCustomer: boolean;
  pending: boolean;
  /** Dernier refus du serveur (message traduit). */
  error?: string | null;
  /** Limite de crédit dépassée et dépassement permis à cet utilisateur (réponse du serveur). */
  overrideAllowed?: boolean;
  /** Lot 3-H-A : refus `insufficient_unexpired_stock` (lots périmés disponibles). */
  expiredShortages?: ExpiredShortage[];
  onConfirm: (
    payments: ImmediatePayment[],
    creditOverride: CreditOverride | null,
    expiredLotOverride?: ExpiredLotOverride | null,
  ) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const canPay = can('sales.payment.create');
  const canCredit = can('sales.sale.credit_create');
  const { methods, isPending } = useAvailablePaymentMethods(siteId, canPay);
  // Sans client ou sans droit au crédit : encaissement proposé d'emblée (le serveur refuse
  // toute vente à crédit non permise).
  const [payNow, setPayNow] = useState(canPay && (!hasCustomer || !canCredit));
  const [draft, setDraft] = useState<PaymentDraft>({
    methodId: null,
    amount: total.replace(/\.00$/, ''),
    reference: '',
  });
  const [errors, setErrors] = useState<PaymentDraftErrors>({});
  const [cashRegisterId, setCashRegisterId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const methodId = draft.methodId ?? defaultMethodId(methods);
  const current = { ...draft, methodId };
  const method = methods.find((m) => m.id === methodId);

  const confirm = (withOverride: boolean, expiredLotOverride: ExpiredLotOverride | null = null) => {
    const creditOverride = withOverride ? { reason: reason.trim() } : null;
    if (!payNow || !canPay) {
      onConfirm([], creditOverride, expiredLotOverride);
      return;
    }
    const result = toPaymentInput(t, current, methods);
    if ('errors' in result) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    onConfirm(
      [
        {
          ...result.input,
          cash_register_id: method?.kind === 'CASH' ? cashRegisterId : null,
        },
      ],
      creditOverride,
      expiredLotOverride,
    );
  };

  return (
    <Dialog header={t('sales.validate')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <p>{t('sales.confirmValidate', { total: formatMoney(total, currency, locale) })}</p>
        {canPay && (
          <>
            <div className="sm-checkbox">
              <Checkbox
                inputId="validate-pay-now"
                checked={payNow}
                onChange={(e) => setPayNow(e.checked === true)}
              />
              <label htmlFor="validate-pay-now">{t('sales.payNow')}</label>
            </div>
            {payNow &&
              (isPending ? (
                <LoadingState />
              ) : (
                <PaymentFields
                  idPrefix="validate"
                  methods={methods}
                  draft={current}
                  errors={errors}
                  onChange={(patch) => setDraft((d) => ({ ...d, methodId, ...patch }))}
                  currency={currency}
                />
              ))}
            {payNow && method?.kind === 'CASH' && siteId && (
              <CashRegisterChoice
                siteId={siteId}
                value={cashRegisterId}
                onChange={setCashRegisterId}
              />
            )}
          </>
        )}
        <Message
          severity="info"
          text={
            !hasCustomer
              ? t('sales.creditNeedsCustomer')
              : canCredit
                ? t('sales.creditNotice')
                : t('sales.creditNotAllowedNotice')
          }
        />
        {error && <Message severity="error" text={error} />}
        {expiredShortages.length > 0 && (
          <ExpiredLotOverridePanel
            shortages={expiredShortages}
            idPrefix="validate"
            pending={pending}
            onConfirm={(override) => confirm(false, override)}
          />
        )}
        {overrideAllowed && (
          <FormField
            id="validate-override-reason"
            label={t('sales.overrideReason')}
            help={t('sales.overrideReasonHelp')}
            required
          >
            <InputTextarea
              id="validate-override-reason"
              rows={3}
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </FormField>
        )}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          {overrideAllowed && (
            <Button
              type="button"
              icon="pi pi-shield"
              label={t('sales.overrideAndValidate')}
              severity="warning"
              disabled={reason.trim().length < 5}
              loading={pending}
              onClick={() => confirm(true)}
            />
          )}
          <Button
            type="button"
            icon="pi pi-check"
            label={t('sales.validate')}
            loading={pending}
            onClick={() => confirm(false)}
          />
        </div>
      </div>
    </Dialog>
  );
}
