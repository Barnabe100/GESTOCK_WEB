import { Button } from 'primereact/button';
import { Checkbox } from 'primereact/checkbox';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { CashRegisterChoice } from '@/modules/cash_register/CashRegisterChoice';
import { formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
import { FormField } from '@/shared/ui/FormField';

import { PAYMENT_METHODS, type ImmediatePayment, type PaymentMethod } from './api';

/**
 * Confirmation de la validation d'une vente, avec encaissement immédiat facultatif (paiement
 * comptant, même transaction). Le reste dû devient une créance, soumise à la limite de crédit
 * du client : le serveur recalcule et contrôle tout (montants, surpaiement, limite).
 */
export function ValidateSaleDialog({
  total,
  siteId,
  pending,
  onConfirm,
  onClose,
}: {
  /** Total indicatif (chaîne décimale). */
  total: string;
  /** Site de la vente : caisse des encaissements en espèces. */
  siteId: string | null;
  pending: boolean;
  onConfirm: (payments: ImmediatePayment[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { can, capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const canPay = can('sales.payment.create');
  const [payNow, setPayNow] = useState(false);
  const [amount, setAmount] = useState(total.replace(/\.00$/, ''));
  const [method, setMethod] = useState<PaymentMethod>('CASH');
  const [cashRegisterId, setCashRegisterId] = useState<string | null>(null);
  const [amountError, setAmountError] = useState<string | null>(null);

  const confirm = () => {
    if (!payNow) {
      onConfirm([]);
      return;
    }
    const normalized = normalizeDecimal(amount, 2);
    if (normalized === null || !/[1-9]/.test(normalized)) {
      setAmountError(t('payment.invalidAmount'));
      return;
    }
    setAmountError(null);
    onConfirm([
      { amount: normalized, method, cash_register_id: method === 'CASH' ? cashRegisterId : null },
    ]);
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
            {payNow && (
              <>
                <FormField
                  id="validate-amount"
                  label={t('payment.amount')}
                  required
                  error={amountError ?? undefined}
                  help={t('sales.payNowHelp', { currency })}
                >
                  <InputText
                    id="validate-amount"
                    inputMode="decimal"
                    value={amount}
                    invalid={amountError !== null}
                    onChange={(e) => setAmount(e.target.value)}
                  />
                </FormField>
                <FormField id="validate-method" label={t('payment.methodLabel')} required>
                  <Dropdown
                    inputId="validate-method"
                    value={method}
                    onChange={(e) => setMethod(e.value as PaymentMethod)}
                    options={PAYMENT_METHODS.map((m) => ({
                      value: m,
                      label: t(`payment.method.${m}`),
                    }))}
                  />
                </FormField>
                {method === 'CASH' && siteId && (
                  <CashRegisterChoice
                    siteId={siteId}
                    value={cashRegisterId}
                    onChange={setCashRegisterId}
                  />
                )}
              </>
            )}
          </>
        )}
        <Message severity="info" text={t('sales.creditNotice')} />
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="button"
            icon="pi pi-check"
            label={t('sales.validate')}
            loading={pending}
            onClick={confirm}
          />
        </div>
      </div>
    </Dialog>
  );
}
