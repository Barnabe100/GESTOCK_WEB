import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Controller, useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { normalizeDecimal } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { CUSTOMER_TYPES, useSaveCustomer, type Customer, type CustomerType } from './api';

/** Même règle que le serveur (ergonomie) : séparateurs retirés, « + » initial, 4 à 20 chiffres. */
const PHONE = /^\+?\d{4,20}$/;
const phone = z
  .string()
  .refine((v) => v.trim() === '' || PHONE.test(v.replace(/[\s.\-()/]/g, '')), 'phone');
const optional = (max: number) => z.string().trim().max(max);

const schema = z.object({
  customer_type: z.enum(['INDIVIDUAL', 'BUSINESS']),
  name: z.string().trim().min(1, 'required').max(150),
  legal_name: optional(200),
  tax_id: optional(50),
  phone,
  phone2: phone,
  email: z.union([z.literal(''), z.string().trim().email().max(150)]),
  address: optional(255),
  city: optional(100),
  country: optional(100),
  notes: optional(1000),
  credit_limit: z.string().refine((v) => v.trim() === '' || normalizeDecimal(v, 2) !== null),
});
type FormValues = z.infer<typeof schema>;

type TextField = 'tax_id' | 'phone' | 'phone2' | 'email' | 'address' | 'city' | 'country';
const TEXT_FIELDS: TextField[] = [
  'phone',
  'phone2',
  'email',
  'tax_id',
  'address',
  'city',
  'country',
];
const ERROR_KEYS: Partial<Record<keyof FormValues, string>> = {
  phone: 'customers.invalidPhone',
  phone2: 'customers.invalidPhone',
  email: 'validation.email',
  credit_limit: 'articles.invalidMoney',
};

/** Création ou modification d'un client (le serveur normalise et valide tout). */
export function CustomerDialog({
  customer,
  onClose,
}: {
  customer: Customer | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveCustomer();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      customer_type: customer?.customer_type ?? 'INDIVIDUAL',
      name: customer?.name ?? '',
      legal_name: customer?.legal_name ?? '',
      tax_id: customer?.tax_id ?? '',
      phone: customer?.phone ?? '',
      phone2: customer?.phone2 ?? '',
      email: customer?.email ?? '',
      address: customer?.address ?? '',
      city: customer?.city ?? '',
      country: customer?.country ?? '',
      notes: customer?.notes ?? '',
      credit_limit: customer?.credit_limit ?? '',
    },
  });
  const errors = form.formState.errors;
  const type = useWatch({ control: form.control, name: 'customer_type' });
  const errorText = (field: keyof FormValues) => {
    const error = errors[field];
    if (!error) return undefined;
    if (error.message === 'required') return t('validation.required');
    if (error.type === 'too_big') return t('validation.tooLong');
    return t(ERROR_KEYS[field] ?? 'validation.invalid');
  };

  const onSubmit = form.handleSubmit((values) =>
    save.mutate(
      {
        id: customer?.id,
        input: {
          ...values,
          // Raison sociale : entreprises seulement.
          legal_name: values.customer_type === 'BUSINESS' ? values.legal_name : '',
          credit_limit: normalizeDecimal(values.credit_limit, 2),
        },
      },
      {
        onSuccess: (saved) => {
          toast.success(
            t(customer ? 'customers.updated' : 'customers.created', { code: saved.code }),
          );
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={customer ? `${t('customers.edit')} — ${customer.code}` : t('customers.new')}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <div className="sm-form-grid">
          <FormField id="customer-type" label={t('customers.type')}>
            <Controller
              control={form.control}
              name="customer_type"
              render={({ field }) => (
                <Dropdown
                  inputId="customer-type"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value as CustomerType)}
                  options={CUSTOMER_TYPES.map((v) => ({
                    value: v,
                    label: t(`customers.types.${v}`),
                  }))}
                />
              )}
            />
          </FormField>
          <FormField
            id="customer-name"
            label={t(type === 'BUSINESS' ? 'customers.businessName' : 'customers.name')}
            error={errorText('name')}
          >
            <InputText id="customer-name" {...form.register('name')} autoFocus />
          </FormField>
          {type === 'BUSINESS' && (
            <FormField
              id="customer-legal_name"
              label={t('customers.legalName')}
              error={errorText('legal_name')}
            >
              <InputText id="customer-legal_name" {...form.register('legal_name')} />
            </FormField>
          )}
          {TEXT_FIELDS.map((field) => (
            <FormField
              key={field}
              id={`customer-${field}`}
              label={t(`customers.${field}`)}
              error={errorText(field)}
            >
              <InputText
                id={`customer-${field}`}
                inputMode={field.startsWith('phone') ? 'tel' : undefined}
                type={field === 'email' ? 'email' : 'text'}
                {...form.register(field)}
              />
            </FormField>
          ))}
          <FormField
            id="customer-credit_limit"
            label={t('customers.creditLimit')}
            help={t('customers.creditLimitHelp')}
            error={errorText('credit_limit')}
          >
            <InputText
              id="customer-credit_limit"
              inputMode="decimal"
              {...form.register('credit_limit')}
            />
          </FormField>
        </div>
        <FormField id="customer-notes" label={t('customers.notes')} error={errorText('notes')}>
          <InputTextarea id="customer-notes" rows={3} {...form.register('notes')} />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}
