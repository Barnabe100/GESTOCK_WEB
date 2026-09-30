import type { TFunction } from 'i18next';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { useTranslation } from 'react-i18next';

import { normalizeDecimal } from '@/shared/lib/decimal';
import { FormField } from '@/shared/ui/FormField';

import type { ConfiguredPaymentMethod, PaymentInput } from './api';

/** Saisie d'un paiement : moyen configuré, montant (espèces : montant reçu), référence. */
export interface PaymentDraft {
  methodId: string | null;
  amount: string;
  reference: string;
}

export interface PaymentDraftErrors {
  method?: string;
  amount?: string;
  reference?: string;
}

/**
 * Contrôles d'ergonomie (le serveur fait foi) : moyen choisi, montant positif, référence si le
 * moyen l'exige. Espèces : le montant saisi est le **montant reçu** ; le serveur impute
 * min(reçu, reste dû) et calcule la monnaie. Autres moyens : montant imputé, jamais de monnaie.
 */
export function toPaymentInput(
  t: TFunction,
  draft: PaymentDraft,
  methods: readonly ConfiguredPaymentMethod[],
): { input: Omit<PaymentInput, 'idempotency_key'> } | { errors: PaymentDraftErrors } {
  const method = methods.find((m) => m.id === draft.methodId);
  const errors: PaymentDraftErrors = {};
  if (!method) errors.method = t('validation.required');
  const amount = normalizeDecimal(draft.amount, 2);
  if (amount === null || !/[1-9]/.test(amount)) errors.amount = t('payment.invalidAmount');
  const reference = draft.reference.trim();
  if (method?.reference_required && reference === '') {
    errors.reference = t('payment.referenceRequired');
  }
  if (!method || amount === null || Object.keys(errors).length > 0) return { errors };
  const cash = method.kind === 'CASH';
  return {
    input: {
      payment_method_id: method.id,
      amount: cash ? null : amount,
      amount_received: cash ? amount : null,
      reference: reference || null,
    },
  };
}

/** Champs d'un paiement ; `methods` : moyens disponibles sur le site (calculés par le serveur). */
export function PaymentFields({
  idPrefix,
  methods,
  draft,
  errors,
  onChange,
  currency,
  autoFocus = false,
}: {
  idPrefix: string;
  methods: readonly ConfiguredPaymentMethod[];
  draft: PaymentDraft;
  errors: PaymentDraftErrors;
  onChange: (patch: Partial<PaymentDraft>) => void;
  currency: string;
  autoFocus?: boolean;
}) {
  const { t } = useTranslation();
  const method = methods.find((m) => m.id === draft.methodId);
  const cash = method?.kind === 'CASH';
  if (methods.length === 0) {
    return <Message severity="warn" text={t('payment.noMethodForSite')} />;
  }
  return (
    <>
      <FormField
        id={`${idPrefix}-method`}
        label={t('payment.methodLabel')}
        required
        error={errors.method}
      >
        <Dropdown
          inputId={`${idPrefix}-method`}
          value={draft.methodId}
          onChange={(e) => onChange({ methodId: e.value as string })}
          options={methods.map((m) => ({ value: m.id, label: m.label }))}
          placeholder={t('payment.chooseMethod')}
        />
      </FormField>
      <FormField
        id={`${idPrefix}-amount`}
        label={cash ? t('payment.amountReceived') : t('payment.amount')}
        required
        error={errors.amount}
        help={
          cash
            ? t('payment.amountReceivedHelp', { currency })
            : t('payment.amountHelp', { currency })
        }
      >
        <InputText
          id={`${idPrefix}-amount`}
          inputMode="decimal"
          value={draft.amount}
          invalid={errors.amount !== undefined}
          onChange={(e) => onChange({ amount: e.target.value })}
          autoFocus={autoFocus}
        />
      </FormField>
      <FormField
        id={`${idPrefix}-reference`}
        label={t('payment.reference')}
        required={method?.reference_required === true}
        error={errors.reference}
        help={method?.reference_required ? undefined : t('payment.referenceHelp')}
      >
        <InputText
          id={`${idPrefix}-reference`}
          value={draft.reference}
          maxLength={100}
          invalid={errors.reference !== undefined}
          onChange={(e) => onChange({ reference: e.target.value })}
        />
      </FormField>
    </>
  );
}

/** Moyen proposé par défaut : le premier disponible (ordre défini par l'entreprise). */
export function defaultMethodId(methods: readonly ConfiguredPaymentMethod[]): string | null {
  return methods[0]?.id ?? null;
}
