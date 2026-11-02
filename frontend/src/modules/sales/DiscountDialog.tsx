import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { SelectButton } from 'primereact/selectbutton';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { compareMoney, formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
import { FormField } from '@/shared/ui/FormField';

import { discountAmount, type DiscountMode, type SaleDiscount } from './discount';

interface Props {
  /** Sous-total indicatif de la vente (avant remise). */
  subtotal: string;
  currency: string;
  locale: string;
  initial: SaleDiscount | null;
  idPrefix: string;
  onApply: (discount: SaleDiscount | null) => void;
  onClose: () => void;
}

/**
 * Remise globale (palier R9, ADR-0054) : montant ou pourcentage, motif obligatoire. Aide à la
 * saisie seulement : le serveur vérifie la permission sur le site, calcule le montant et le
 * total, et refuse une remise supérieure au montant de la vente.
 */
export function DiscountDialog({
  subtotal,
  currency,
  locale,
  initial,
  idPrefix,
  onApply,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<DiscountMode>(initial?.mode ?? 'AMOUNT');
  const [value, setValue] = useState(initial?.value ?? '');
  const [reason, setReason] = useState(initial?.reason ?? '');
  const [submitted, setSubmitted] = useState(false);

  const normalized = normalizeDecimal(value, 2);
  const positive = normalized !== null && /[1-9]/.test(normalized);
  const tooLarge =
    positive &&
    (mode === 'PERCENT'
      ? compareMoney(normalized, '100') > 0
      : compareMoney(normalized, subtotal) > 0);
  const valueError = !positive
    ? t('sales.discount.valueRequired')
    : tooLarge
      ? t('sales.discount.tooLarge')
      : null;
  const reasonError = reason.trim().length < 3 ? t('sales.discount.reasonRequired') : null;
  const draft: SaleDiscount | null = positive
    ? { mode, value: normalized, reason: reason.trim() }
    : null;
  const amount = draft && !tooLarge ? discountAmount(subtotal, draft) : '0.00';

  const apply = () => {
    setSubmitted(true);
    if (draft && !valueError && !reasonError) onApply(draft);
  };

  return (
    <Dialog
      header={t('sales.discount.title')}
      visible
      onHide={onClose}
      className="sm-dialog"
      footer={
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          {initial && (
            <Button
              type="button"
              icon="pi pi-times"
              label={t('sales.discount.remove')}
              severity="danger"
              outlined
              onClick={() => onApply(null)}
            />
          )}
          <Button
            type="button"
            icon="pi pi-check"
            label={t('sales.discount.apply')}
            onClick={apply}
          />
        </div>
      }
    >
      <div className="sm-form">
        <FormField id={`${idPrefix}-discount-mode`} label={t('sales.discount.mode')} required>
          <SelectButton
            id={`${idPrefix}-discount-mode`}
            value={mode}
            allowEmpty={false}
            onChange={(e) => setMode(e.value as DiscountMode)}
            options={[
              { value: 'AMOUNT', label: t('sales.discount.modes.AMOUNT') },
              { value: 'PERCENT', label: t('sales.discount.modes.PERCENT') },
            ]}
          />
        </FormField>
        <FormField
          id={`${idPrefix}-discount-value`}
          label={mode === 'PERCENT' ? t('sales.discount.percent') : t('sales.discount.amount')}
          required
          error={submitted && valueError ? valueError : undefined}
        >
          <InputText
            id={`${idPrefix}-discount-value`}
            inputMode="decimal"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </FormField>
        <FormField
          id={`${idPrefix}-discount-reason`}
          label={t('sales.discount.reason')}
          required
          error={submitted && reasonError ? reasonError : undefined}
        >
          <InputTextarea
            id={`${idPrefix}-discount-reason`}
            rows={2}
            maxLength={200}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </FormField>
        <p className="sm-help" data-testid={`${idPrefix}-discount-preview`}>
          {t('sales.discount.preview', {
            amount: formatMoney(amount, currency, locale),
            subtotal: formatMoney(subtotal, currency, locale),
          })}
        </p>
      </div>
    </Dialog>
  );
}
