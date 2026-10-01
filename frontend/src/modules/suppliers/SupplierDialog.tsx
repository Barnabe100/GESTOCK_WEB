import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { useSaveSupplier, type Supplier } from './api';

const optional = (max: number) => z.string().max(max);
const schema = z.object({
  name: z.string().trim().min(1).max(150),
  contact_name: optional(150),
  phone: optional(30),
  email: z.union([z.literal(''), z.string().trim().email().max(150)]),
  address: optional(255),
  city: optional(100),
  country: optional(100),
  notes: optional(500),
});
type FormValues = z.infer<typeof schema>;
const FIELDS = ['contact_name', 'phone', 'email', 'address', 'city', 'country'] as const;
const LABELS: Record<(typeof FIELDS)[number], string> = {
  contact_name: 'suppliers.contact',
  phone: 'suppliers.phone',
  email: 'suppliers.email',
  address: 'suppliers.address',
  city: 'suppliers.city',
  country: 'suppliers.country',
};

/** Création / modification d'un fournisseur (liste et fiche). */
export function SupplierDialog({
  supplier,
  onClose,
}: {
  supplier: Supplier | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const save = useSaveSupplier();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: supplier?.name ?? '',
      contact_name: supplier?.contact_name ?? '',
      phone: supplier?.phone ?? '',
      email: supplier?.email ?? '',
      address: supplier?.address ?? '',
      city: supplier?.city ?? '',
      country: supplier?.country ?? '',
      notes: supplier?.notes ?? '',
    },
  });
  const errors = form.formState.errors;

  // Chaîne vide = champ effacé côté serveur.
  const onSubmit = form.handleSubmit((values) =>
    save.mutate(
      { id: supplier?.id, input: values },
      {
        onSuccess: () => {
          toast.success(t(supplier ? 'suppliers.updated' : 'suppliers.created'));
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    ),
  );

  return (
    <Dialog
      header={t(supplier ? 'suppliers.edit' : 'suppliers.new')}
      visible
      onHide={onClose}
      className="sm-dialog sm-dialog-wide"
    >
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        <FormField
          id="supplier-name"
          label={t('suppliers.name')}
          required
          error={errors.name && t('validation.required')}
        >
          <InputText id="supplier-name" {...form.register('name')} autoFocus />
        </FormField>
        <div className="sm-form-grid">
          {FIELDS.map((field) => (
            <FormField
              key={field}
              id={`supplier-${field}`}
              label={t(LABELS[field])}
              error={
                errors[field] && t(field === 'email' ? 'validation.email' : 'validation.invalid')
              }
            >
              <InputText id={`supplier-${field}`} {...form.register(field)} />
            </FormField>
          ))}
        </div>
        <FormField
          id="supplier-notes"
          label={t('suppliers.notes')}
          error={errors.notes && t('validation.invalid')}
        >
          <InputTextarea id="supplier-notes" rows={3} {...form.register('notes')} />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button type="submit" label={t('actions.save')} loading={save.isPending} />
        </div>
      </form>
    </Dialog>
  );
}
