import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { CashRegisterChoice } from '@/modules/cash_register/CashRegisterChoice';
import {
  useAvailablePaymentMethods,
  type ConfiguredPaymentMethod,
  type PaymentMethod,
} from '@/modules/sales/api';
import { formatMoney, normalizeDecimal, subtractMoney, sumMoney } from '@/shared/lib/decimal';
import { LoadingState } from '@/shared/ui/LoadingState';

/**
 * Paiement saisi au point de vente : moyen configuré (instantané du libellé et du type pour
 * l'affichage). Espèces : `amount` est le **montant reçu** (le serveur impute min(reçu, reste
 * dû) et calcule la monnaie) ; autres moyens : montant payé, jamais de monnaie.
 */
export interface PosPayment {
  key: string;
  methodId: string;
  label: string;
  kind: PaymentMethod;
  referenceRequired: boolean;
  amount: string;
  reference: string;
}

/** Corps de l'API : le montant reçu en espèces, le montant payé pour les autres moyens. */
export function toCheckoutPayment(p: PosPayment, cashRegisterId: string | null) {
  const cash = p.kind === 'CASH';
  return {
    payment_method_id: p.methodId,
    amount: cash ? null : p.amount,
    amount_received: cash ? p.amount : null,
    reference: p.reference.trim() || null,
    cash_register_id: cash ? cashRegisterId : null,
  };
}

/**
 * Paiements de la vente (F8) : aucun, complet, partiel ou plusieurs moyens (moyens disponibles
 * sur le site, calculés par le serveur). Montants et reste indicatifs ; le serveur refuse tout
 * surpaiement et calcule la monnaie sur la seule partie espèces. Espèces sur un site avec
 * caisse : session de l'utilisateur sur ce site.
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
  const { methods, isPending } = useAvailablePaymentMethods(siteId);
  const [lines, setLines] = useState<PosPayment[]>(initial);
  const [register, setRegister] = useState<string | null>(cashRegisterId);
  const [error, setError] = useState<string | null>(null);
  const money = (v: string) => formatMoney(v, currency, locale);
  const amounts = lines.map((l) => normalizeDecimal(l.amount, 2) ?? '0');
  const paid = sumMoney(amounts);
  const remaining = subtractMoney(total, paid);
  const positiveRemaining = remaining.startsWith('-') ? '0.00' : remaining;
  const nonCash = sumMoney(
    lines.filter((l) => l.kind !== 'CASH').map((l) => normalizeDecimal(l.amount, 2) ?? '0'),
  );
  const update = (key: string, patch: Partial<PosPayment>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const add = (method: ConfiguredPaymentMethod) =>
    setLines((ls) => [
      ...ls,
      {
        key: crypto.randomUUID(),
        methodId: method.id,
        label: method.label,
        kind: method.kind,
        referenceRequired: method.reference_required,
        // Reste dû proposé ; rien si la vente est déjà entièrement réglée.
        amount: /[1-9]/.test(positiveRemaining) ? positiveRemaining.replace(/\.00$/, '') : '',
        reference: '',
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
    if (lines.some((l) => l.referenceRequired && l.reference.trim() === '')) {
      setError(t('payment.referenceRequired'));
      return;
    }
    // Seules les espèces peuvent dépasser le reste (monnaie rendue) : jamais les autres moyens.
    if (subtractMoney(total, nonCash).startsWith('-')) {
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
            <span className="sm-pos-payment-label">{line.label}</span>
            <InputText
              value={line.amount}
              inputMode="decimal"
              aria-label={
                line.kind === 'CASH'
                  ? t('pos.receivedAmountN', { n: index + 1 })
                  : t('pos.paymentAmountN', { n: index + 1 })
              }
              placeholder={line.kind === 'CASH' ? t('payment.amountReceived') : undefined}
              onChange={(e) => update(line.key, { amount: e.target.value })}
            />
            {line.kind !== 'CASH' && (
              <InputText
                value={line.reference}
                maxLength={100}
                aria-label={t('pos.paymentReferenceN', { n: index + 1 })}
                placeholder={
                  line.referenceRequired ? `${t('payment.reference')} *` : t('payment.reference')
                }
                onChange={(e) => update(line.key, { reference: e.target.value })}
              />
            )}
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
        {lines.some((l) => l.kind === 'CASH') && (
          <p className="sm-help">{t('pos.changeByServer')}</p>
        )}
        {isPending ? (
          <LoadingState />
        ) : methods.length === 0 ? (
          <Message severity="warn" text={t('payment.noMethodForSite')} />
        ) : (
          <div className="sm-pos-methods" role="group" aria-label={t('pos.addPayment')}>
            {methods.map((m) => (
              <Button
                key={m.id}
                type="button"
                icon="pi pi-plus"
                label={m.label}
                outlined
                onClick={() => add(m)}
              />
            ))}
          </div>
        )}
        {lines.some((l) => l.kind === 'CASH') && (
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
