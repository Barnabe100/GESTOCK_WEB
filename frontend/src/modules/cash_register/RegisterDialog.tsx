import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Dialog } from 'primereact/dialog';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';
import { useToast } from '@/shared/ui/toast';

import { useCashMutations, type CashRegister } from './api';

const schema = z.object({
  site_id: z.string().nullable(),
  name: z.string().trim().min(1, 'required').max(100),
  description: z.string().trim().max(500),
});
type FormValues = z.infer<typeof schema>;

/** Création ou modification d'une caisse. Le site est choisi à la création, puis figé. */
export function RegisterDialog({
  register,
  onClose,
}: {
  register: CashRegister | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { capabilities, siteId } = useCapabilities();
  const { saveRegister } = useCashMutations();
  const sites = capabilities.sites;
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      site_id: register?.site_id ?? siteId ?? (sites.length === 1 ? (sites[0]?.id ?? null) : null),
      name: register?.name ?? '',
      description: register?.description ?? '',
    },
  });
  const errors = form.formState.errors;
  const submit = form.handleSubmit((values) => {
    if (!register && !values.site_id) {
      form.setError('site_id', { message: 'required' });
      return;
    }
    saveRegister.mutate(
      {
        id: register?.id,
        input: {
          ...(register ? {} : { site_id: values.site_id }),
          name: values.name,
          description: values.description || null,
        },
      },
      {
        onSuccess: (saved) => {
          toast.success(
            t(register ? 'cash.registerUpdated' : 'cash.registerCreated', { code: saved.code }),
          );
          onClose();
        },
        onError: (error) => toast.error(translateError(t, error)),
      },
    );
  });

  return (
    <Dialog
      header={t(register ? 'cash.editRegister' : 'cash.newRegister')}
      visible
      onHide={onClose}
      className="sm-dialog"
    >
      <form className="sm-form" noValidate onSubmit={(e) => void submit(e)}>
        {register ? (
          <p className="sm-help">
            {t('cash.registerSite', { site: register.site_name, code: register.code })}
          </p>
        ) : (
          <FormField
            id="register-site"
            label={t('layout.site')}
            required
            error={errors.site_id ? t('validation.required') : undefined}
          >
            <Controller
              control={form.control}
              name="site_id"
              render={({ field }) => (
                <Dropdown
                  inputId="register-site"
                  value={field.value}
                  onChange={(e) => field.onChange(e.value as string)}
                  options={sites.map((s) => ({ value: s.id, label: s.name }))}
                  placeholder={t('cash.chooseSite')}
                  disabled={siteId !== null}
                  invalid={errors.site_id !== undefined}
                />
              )}
            />
          </FormField>
        )}
        <FormField
          id="register-name"
          label={t('cash.registerName')}
          required
          error={errors.name ? t('validation.required') : undefined}
        >
          <InputText
            id="register-name"
            maxLength={100}
            invalid={errors.name !== undefined}
            {...form.register('name')}
          />
        </FormField>
        <FormField id="register-description" label={t('cash.description')}>
          <InputTextarea
            id="register-description"
            rows={2}
            maxLength={500}
            {...form.register('description')}
          />
        </FormField>
        <div className="sm-dialog-actions">
          <Button type="button" label={t('actions.cancel')} text onClick={onClose} />
          <Button
            type="submit"
            icon="pi pi-check"
            label={t('actions.save')}
            loading={saveRegister.isPending}
          />
        </div>
      </form>
    </Dialog>
  );
}
