import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { CashRegisterChoice } from '@/modules/cash_register/CashRegisterChoice';
import { PAYMENT_METHODS, type PaymentMethod } from '@/modules/sales/api';
import { formatMoney, normalizeDecimal, subtractMoney, sumMoney } from '@/shared/lib/decimal';

export interface PosPayment {
  key: string;
  method: PaymentMethod;
  amount: string;
}

/**
 * Paiements de la vente (F8) : aucun, complet, partiel ou plusieurs moyens. Montants et reste
 * indicatifs ; le serveur refuse tout surpaiement. Espèces : caisse ouverte du site (choisie
 * par le serveur, ou à désigner s'il y en a plusieurs).
 */
export function PosPaymentDialog({
  total,
  siteId,
  initial,
  cashRegisterId,
  onSave,
  onClose,
}: {
  total: string;
  siteId: string;
  initial: PosPayment[];
  cashRegisterId: string | null;
  onSave: (payments: PosPayment[], cashRegisterId: string | null) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const [lines, setLines] = useState<PosPayment[]>(initial);
  const [register, setRegister] = useState<string | null>(cashRegisterId);
  const [error, setError] = useState<string | null>(null);
  const money = (v: string) => formatMoney(v, currency, locale);
  const amounts = lines.map((l) => normalizeDecimal(l.amount, 2) ?? '0');
  const paid = sumMoney(amounts);
  const remaining = subtractMoney(total, paid);
  const positiveRemaining = remaining.startsWith('-') ? '0.00' : remaining;
  const update = (key: string, patch: Partial<PosPayment>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const add = (method: PaymentMethod) =>
    setLines((ls) => [
      ...ls,
      {
        key: crypto.randomUUID(),
        method,
        // Reste dû proposé ; rien si la vente est déjà entièrement réglée.
        amount: /[1-9]/.test(positiveRemaining) ? positiveRemaining.replace(/\.00$/, '') : '',
      },
    ]);

  const save = () => {
    const invalid = lines.some((l) => {
      const n = normalizeDecimal(l.amount, 2);
      return n === null || !/[1-9]/.test(n);
    });
    if (invalid) {
      setError(t('payment.invalidAmount'));
      return;
    }
    if (remaining.startsWith('-')) {
      setError(t('pos.overpayment'));
      return;
    }
    onSave(
      lines.map((l) => ({ ...l, amount: normalizeDecimal(l.amount, 2) ?? l.amount })),
      register,
    );
  };

  return (
    <Dialog header={t('pos.payments')} visible onHide={onClose} className="sm-dialog">
      <div className="sm-form">
        <dl className="sm-pos-summary" aria-label={t('pos.paymentSummary')}>
          <div>
            <dt>{t('pos.total')}</dt>
            <dd>{money(total)}</dd>
          </div>
          <div>
            <dt>{t('pos.paid')}</dt>
            <dd>{money(paid)}</dd>
          </div>
          <div>
            <dt>{t('pos.remaining')}</dt>
            <dd data-testid="pos-remaining">{money(positiveRemaining)}</dd>
          </div>
        </dl>
        {lines.length === 0 && <Message severity="info" text={t('pos.noPaymentHelp')} />}
        {lines.map((line, index) => (
          <div key={line.key} className="sm-pos-payment">
            <Dropdown
              value={line.method}
              onChange={(e) => update(line.key, { method: e.value as PaymentMethod })}
              options={PAYMENT_METHODS.map((m) => ({ value: m, label: t(`payment.method.${m}`) }))}
              aria-label={t('pos.paymentMethodN', { n: index + 1 })}
            />
            <InputText
              value={line.amount}
              inputMode="decimal"
              aria-label={t('pos.paymentAmountN', { n: index + 1 })}
              onChange={(e) => update(line.key, { amount: e.target.value })}
            />
            <Button
              type="button"
              icon="pi pi-trash"
              text
              severity="danger"
              aria-label={t('pos.removePayment')}
              onClick={() => setLines((ls) => ls.filter((l) => l.key !== line.key))}
            />
          </div>
        ))}
        <div className="sm-pos-methods" role="group" aria-label={t('pos.addPayment')}>
          {PAYMENT_METHODS.map((m) => (
            <Button
              key={m}
              type="button"
              icon="pi pi-plus"
              label={t(`payment.method.${m}`)}
              outlined
              onClick={() => add(m)}
            />
          ))}
        </div>
        {lines.some((l) => l.method === 'CASH') && (
          <CashRegisterChoice siteId={siteId} value={register} onChange={setRegister} />
        )}
        {error && <Message severity="error" text={error} />}
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="button" icon="pi pi-check" label={t('pos.applyPayments')} onClick={save} />
        </div>
      </div>
    </Dialog>
  );
}
