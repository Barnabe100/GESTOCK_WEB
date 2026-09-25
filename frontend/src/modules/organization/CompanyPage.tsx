import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { InputText } from 'primereact/inputtext';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { FormSection } from '@/shared/ui/FormSection';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { useToast } from '@/shared/ui/toast';

import { useTenant, useUpdateTenant } from './api';
import { BusinessProfileSection } from './BusinessProfileSection';

const schema = z.object({
  name: z.string().trim().min(1).max(150),
  timezone: z.string().trim().min(1).max(64),
});
type FormValues = z.infer<typeof schema>;

export default function CompanyPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const tenant = useTenant();
  const update = useUpdateTenant();
  const editable = can('organization.tenant.update');
  const form = useForm<FormValues>({ resolver: zodResolver(schema) });

  useEffect(() => {
    if (tenant.data) form.reset({ name: tenant.data.name, timezone: tenant.data.timezone });
  }, [tenant.data, form]);

  if (tenant.isError)
    return <ErrorMessage error={tenant.error} onRetry={() => void tenant.refetch()} />;
  if (!tenant.data) return <LoadingState />;

  const onSubmit = form.handleSubmit((values) =>
    update.mutate(values, {
      onSuccess: () => toast.success(t('company.saved')),
      onError: (error) => toast.error(translateError(t, error)),
    }),
  );

  return (
    <>
      <PageHeader title={t('company.title')} description={t('company.subtitle')} />
      <Card className="sm-form-card">
        <form onSubmit={onSubmit} className="sm-form" noValidate>
          <FormSection title={t('company.identity')}>
            <FormField
              id="name"
              label={t('company.name')}
              required={editable}
              error={form.formState.errors.name && t('validation.required')}
            >
              <InputText id="name" {...form.register('name')} disabled={!editable} />
            </FormField>
            <FormField
              id="timezone"
              label={t('company.timezone')}
              required={editable}
              error={form.formState.errors.timezone && t('validation.required')}
            >
              <InputText id="timezone" {...form.register('timezone')} disabled={!editable} />
            </FormField>
          </FormSection>
          <FormSection title={t('company.fixed')} description={t('company.fixedHelp')}>
            <div className="sm-form-grid">
              <FormField id="slug" label={t('company.slug')}>
                <InputText id="slug" value={tenant.data.slug} disabled />
              </FormField>
              <FormField id="currency" label={t('company.currency')}>
                <InputText id="currency" value={tenant.data.currency} disabled />
              </FormField>
            </div>
          </FormSection>
          {editable && (
            <div className="sm-form-actions">
              <Button
                type="submit"
                icon="pi pi-check"
                label={t('actions.save')}
                loading={update.isPending}
              />
            </div>
          )}
        </form>
      </Card>
      <Card className="sm-form-card">
        <BusinessProfileSection />
      </Card>
    </>
  );
}
