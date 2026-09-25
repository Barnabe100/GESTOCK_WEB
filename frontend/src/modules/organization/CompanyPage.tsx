import { zodResolver } from '@hookform/resolvers/zod';
import type { TFunction } from 'i18next';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { useEffect } from 'react';
import { Controller, useForm, useWatch, type FieldPath } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

import { usePublicCountries, type PublicCountry } from '@/core/api/geo';
import { useCapabilities } from '@/core/capabilities/CapabilitiesContext';
import { invalidFields, translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { FormSection } from '@/shared/ui/FormSection';
import { LoadingState } from '@/shared/ui/LoadingState';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBadge } from '@/shared/ui/StatusBadge';
import { useToast } from '@/shared/ui/toast';

import {
  OPTIONAL_COMPANY_FIELDS,
  RECOMMENDED_COMPANY_FIELDS,
  useTenant,
  useUpdateTenant,
  type CompanyField,
  type Tenant,
  type TenantInput,
} from './api';
import { BusinessProfileSection } from './BusinessProfileSection';
import { DocumentIdentityPreview } from './DocumentIdentityPreview';
import { useOnboarding } from './onboardingApi';

const optional = (max: number) => z.string().trim().max(max);
const https = z
  .string()
  .trim()
  .max(500)
  .regex(/^(https:\/\/[^\s/@]+\.[^\s/@]+(\/\S*)?)?$/);

// Contrôles d'ergonomie seulement : le serveur revalide tout et reste seul juge. Obligatoires
// (« * ») : exactement ceux que l'API refuse vides — nom, pays, fuseau (la devise, obligatoire
// elle aussi, est fixée à la création et seulement affichée).
const schema = z.object({
  name: z.string().trim().min(1).max(150),
  country_code: z.string().length(2),
  timezone: z.string().trim().min(1).max(64),
  trade_name: optional(150),
  logo_url: https,
  email: z.union([z.literal(''), z.string().trim().email()]),
  phone: optional(30).regex(/^[+0-9 ().\-/]*$/),
  address: optional(255),
  city: optional(100),
  region: optional(100),
  tax_id: optional(50),
  trade_register: optional(50),
  website: https,
  description: optional(1000),
});
type FormValues = z.infer<typeof schema>;

const GROUPS: { key: 'identity' | 'contact' | 'legal'; fields: CompanyField[] }[] = [
  { key: 'identity', fields: ['trade_name', 'logo_url'] },
  { key: 'contact', fields: ['email', 'phone', 'address', 'city', 'region'] },
  { key: 'legal', fields: ['tax_id', 'trade_register'] },
];
const INPUT_TYPES: Partial<Record<CompanyField, string>> = {
  email: 'email',
  phone: 'tel',
  logo_url: 'url',
  website: 'url',
};
const COMPANY_FIELDS = [...RECOMMENDED_COMPANY_FIELDS, ...OPTIONAL_COMPANY_FIELDS];

function toValues(tenant: Tenant): FormValues {
  const values = {
    name: tenant.name,
    country_code: tenant.country_code ?? '',
    timezone: tenant.timezone,
  } as FormValues;
  for (const field of COMPANY_FIELDS) values[field] = tenant[field] ?? '';
  return values;
}

/** Tout est envoyé : un champ recommandé ou facultatif vidé est effacé (`null`). */
function toInput(values: FormValues): TenantInput {
  const input: TenantInput = {
    name: values.name.trim(),
    timezone: values.timezone.trim(),
    country_code: values.country_code,
  };
  for (const field of COMPANY_FIELDS) input[field] = values[field].trim() || null;
  return input;
}

function countryLabel(t: TFunction, c: PublicCountry) {
  return c.calling_code
    ? t('company.countryOption', { name: c.name, calling: c.calling_code, currency: c.currency })
    : `${c.name} · ${c.currency}`;
}

export default function CompanyPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { can } = useCapabilities();
  const tenant = useTenant();
  const update = useUpdateTenant();
  const editable = can('organization.tenant.update');
  const countries = usePublicCountries();
  const onboarding = useOnboarding(can('organization.onboarding.view'));
  const form = useForm<FormValues>({ resolver: zodResolver(schema) });
  const countryCode = useWatch({ control: form.control, name: 'country_code' });

  useEffect(() => {
    if (tenant.data) form.reset(toValues(tenant.data));
  }, [tenant.data, form]);

  if (tenant.isError)
    return <ErrorMessage error={tenant.error} onRetry={() => void tenant.refetch()} />;
  if (!tenant.data) return <LoadingState />;
  const current = tenant.data;

  // Référentiel (pays actifs) ; le pays actuel reste affiché même s'il a été désactivé.
  const options = [...(countries.data ?? [])];
  if (current.country_code && !options.some((c) => c.code === current.country_code)) {
    options.push({
      code: current.country_code,
      name: current.country_code,
      currency: current.currency,
      calling_code: null,
      timezone: current.timezone,
    });
  }
  const selected = options.find((c) => c.code === countryCode);
  // Étapes d'installation que cette page permet de terminer (déclarées par le serveur).
  const relatedSteps = (onboarding.data?.steps ?? []).filter(
    (s) => s.action?.route === '/organization/company',
  );

  const errors = form.formState.errors;
  // Message traduit ; celui du serveur seulement pour ses propres refus (jamais le texte brut
  // du validateur client).
  const errorOf = (field: FieldPath<FormValues>, key = 'validation.invalid') =>
    errors[field] && (errors[field]?.type === 'server' ? errors[field]?.message : t(key));

  const onSubmit = form.handleSubmit((values) =>
    update.mutate(toInput(values), {
      onSuccess: () => toast.success(t('company.saved')),
      onError: (error) => {
        // Refus du serveur rattachés à leur champ (le serveur reste juge du format).
        for (const field of invalidFields(error)) {
          if (field in values)
            form.setError(field as FieldPath<FormValues>, {
              type: 'server',
              message: t('company.invalidField'),
            });
        }
        toast.error(translateError(t, error));
      },
    }),
  );

  const textField = (field: CompanyField, help?: string) => (
    <FormField
      key={field}
      id={field}
      label={t(`company.fields.${field}`)}
      help={help}
      error={errorOf(field)}
    >
      {field === 'description' ? (
        <InputTextarea id={field} rows={3} {...form.register(field)} disabled={!editable} />
      ) : (
        <InputText
          id={field}
          type={INPUT_TYPES[field] ?? 'text'}
          {...form.register(field)}
          disabled={!editable}
        />
      )}
    </FormField>
  );

  return (
    <>
      <PageHeader
        title={t('company.title')}
        description={`${t('company.subtitle')} ${t('company.slugInfo', { slug: current.slug })}`}
      />
      {!current.country_code && (
        <Message
          severity="warn"
          className="sm-block"
          data-testid="country-missing"
          text={t('company.countryMissing')}
        />
      )}
      <div className="sm-company-layout">
        <Card className="sm-form-card">
          <form onSubmit={onSubmit} className="sm-form" noValidate aria-label={t('company.title')}>
            {editable && <p className="sm-help">{t('company.requiredHint')}</p>}
            <FormSection title={t('company.required')}>
              <FormField
                id="name"
                label={t('company.fields.name')}
                required={editable}
                error={errorOf('name', 'validation.required')}
              >
                <InputText id="name" {...form.register('name')} disabled={!editable} />
              </FormField>
              <div className="sm-form-grid">
                <FormField
                  id="country_code"
                  label={t('company.fields.country_code')}
                  required={editable}
                  error={errorOf('country_code', 'validation.required')}
                >
                  <Controller
                    control={form.control}
                    name="country_code"
                    render={({ field }) => (
                      <Dropdown
                        inputId="country_code"
                        value={field.value || null}
                        options={options}
                        optionValue="code"
                        optionLabel="name"
                        itemTemplate={(c: PublicCountry) => countryLabel(t, c)}
                        filter
                        filterBy="name,code"
                        placeholder={t('company.chooseCountry')}
                        disabled={!editable}
                        invalid={Boolean(errors.country_code)}
                        onChange={(e) => field.onChange(e.value)}
                      />
                    )}
                  />
                </FormField>
                <FormField
                  id="currency"
                  label={t('company.fields.currency')}
                  required={editable}
                  help={
                    selected && selected.currency !== current.currency
                      ? t('company.currencyOther', {
                          currency: selected.currency,
                          current: current.currency,
                        })
                      : t('company.currencyFixed')
                  }
                >
                  <InputText id="currency" value={current.currency} readOnly disabled />
                </FormField>
              </div>
              <FormField
                id="timezone"
                label={t('company.fields.timezone')}
                required={editable}
                error={errorOf('timezone', 'validation.required')}
              >
                <InputText id="timezone" {...form.register('timezone')} disabled={!editable} />
              </FormField>
            </FormSection>
            <FormSection
              title={t('company.recommended')}
              description={t('company.recommendedHelp')}
            >
              {GROUPS.map((group) => (
                <div key={group.key} className="sm-form-subsection">
                  <h3>{t(`company.groups.${group.key}`)}</h3>
                  <div className="sm-form-grid">
                    {group.fields.map((f) =>
                      textField(f, f === 'logo_url' ? t('company.logoHelp') : undefined),
                    )}
                  </div>
                </div>
              ))}
            </FormSection>
            <FormSection title={t('company.optional')}>
              {OPTIONAL_COMPANY_FIELDS.map((f) => textField(f))}
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
        <div className="sm-stack">
          <Card title={t('company.preview.title')}>
            <p className="sm-help">{t('company.preview.help')}</p>
            <DocumentIdentityPreview totalRecommended={RECOMMENDED_COMPANY_FIELDS.length} />
          </Card>
          {relatedSteps.length > 0 && (
            <Card title={t('company.preview.steps')}>
              <ul className="sm-plain-list" data-testid="company-steps">
                {relatedSteps.map((s) => (
                  <li key={s.code}>
                    <span>{t(s.title)}</span>
                    <StatusBadge
                      tone={
                        s.status === 'COMPLETED'
                          ? 'success'
                          : s.status === 'IN_PROGRESS'
                            ? 'info'
                            : 'neutral'
                      }
                      label={t(`onboarding.status.${s.status}`)}
                    />
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
      <Card className="sm-form-card">
        <BusinessProfileSection />
      </Card>
    </>
  );
}
