import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { formatMoney, normalizeDecimal } from '@/shared/lib/decimal';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import {
  IN_CATEGORIES,
  OUT_CATEGORIES,
  useCashMutations,
  type CashMovementCategory,
  type CashSession,
} from './api';
import { cashError } from './ui';

const schema = z.object({
  amount: z.string().refine((v) => {
    const n = normalizeDecimal(v, 2);
    return n !== null && /[1-9]/.test(n);
  }, 'amount'),
  category: z.string().min(1, 'required'),
  reason: z.string().trim().min(3, 'reason').max(255),
  reference: z.string().trim().max(100),
});
type FormValues = z.infer<typeof schema>;

/**
 * Entrée ou sortie manuelle : montant > 0 (le sens est fixé par l'action), nature, motif
 * obligatoire. Le serveur refuse une sortie supérieure au solde de la caisse.
 */
export function MovementDialog({
  session,
  direction,
  onClose,
}: {
  session: CashSession;
  direction: 'in' | 'out';
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities } = useCapabilities();
  const { currency, locale } = capabilities.tenant;
  const { move } = useCashMutations();
  // Une clé par saisie : double clic ou nouvel envoi → aucun doublon.
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [serverError, setServerError] = useState<string | null>(null);
  const categories = direction === 'in' ? IN_CATEGORIES : OUT_CATEGORIES;
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { amount: '', category: categories[0], reason: '', reference: '' },
  });
  const errors = form.formState.errors;

  const submit = form.handleSubmit((values) => {
    setServerError(null);
    move.mutate(
      {
        sessionId: session.id,
        input: {
          movement_type: direction === 'in' ? 'MANUAL_CASH_IN' : 'MANUAL_CASH_OUT',
          amount: normalizeDecimal(values.amount, 2) ?? '0',
          category: values.category as CashMovementCategory,
          reason: values.reason,
          reference: values.reference || null,
          idempotency_key: idempotencyKey,
        },
      },
      {
        onSuccess: (movement) => {
          toast.success(
            t(direction === 'in' ? 'cash.cashInRecorded' : 'cash.cashOutRecorded', {
              amount: formatMoney(movement.amount, currency, locale),
            }),
          );
          onClose();
        },
        onError: (e) => setServerError(cashError(t, e, currency, locale)),
      },
    );
  });

  return (
    <Dialog
      header={t(direction === 'in' ? 'cash.cashInTitle' : 'cash.cashOutTitle')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form className="sm-form" noValidate onSubmit={(e) => void submit(e)}>
        <Message
          severity="info"
          text={t('cash.currentBalance', {
            amount: formatMoney(session.theoretical_balance, currency, locale),
          })}
        />
        {serverError && <Message severity="error" text={serverError} />}
        <FormField
          id="movement-amount"
          label={t('cash.amount')}
          required
          error={errors.amount ? t('cash.invalidPositiveAmount') : undefined}
          help={t('cash.amountHelp', { currency })}
        >
          <InputText
            id="movement-amount"
            inputMode="decimal"
            invalid={errors.amount !== undefined}
            autoFocus
            {...form.register('amount')}
          />
        </FormField>
        <FormField id="movement-category" label={t('cash.category')} required>
          <Controller
            control={form.control}
            name="category"
            render={({ field }) => (
              <Dropdown
                inputId="movement-category"
                value={field.value}
                onChange={(e) => field.onChange(e.value as string)}
                options={categories.map((c) => ({ value: c, label: t(`cash.categories.${c}`) }))}
              />
            )}
          />
        </FormField>
        <FormField
          id="movement-reason"
          label={t('cash.reason')}
          required
          error={errors.reason ? t('cash.reasonRequired') : undefined}
        >
          <InputTextarea
            id="movement-reason"
            rows={2}
            maxLength={255}
            invalid={errors.reason !== undefined}
            {...form.register('reason')}
          />
        </FormField>
        <FormField
          id="movement-reference"
          label={t('cash.reference')}
          help={t('cash.referenceHelp')}
        >
          <InputText id="movement-reference" maxLength={100} {...form.register('reference')} />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            icon={direction === 'in' ? 'pi pi-plus' : 'pi pi-minus'}
            label={t(direction === 'in' ? 'cash.recordCashIn' : 'cash.recordCashOut')}
            severity={direction === 'out' ? 'warning' : undefined}
            loading={move.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}
