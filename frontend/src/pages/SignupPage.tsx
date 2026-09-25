import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Dropdown } from 'primereact/dropdown';
import { InputText } from 'primereact/inputtext';
import { InputTextarea } from 'primereact/inputtextarea';
import { Message } from 'primereact/message';
import { Password } from 'primereact/password';
import { Steps } from 'primereact/steps';
import { useMemo, useState } from 'react';
import { Controller, useForm, useWatch, type FieldPath } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useNavigate } from 'react-router';
import { z } from 'zod';

import { ApiError } from '@/core/api/client';
import type { SignupInput } from '@/core/api/types';
import { useAuth } from '@/core/auth/AuthContext';
import { profileLabel, sectorLabel } from '@/core/capabilities/profile';
import { translateError } from '@/shared/lib/errors';
import { ErrorMessage } from '@/shared/ui/ErrorMessage';
import { FormField } from '@/shared/ui/FormField';
import { FormSection } from '@/shared/ui/FormSection';
import { LoadingState } from '@/shared/ui/LoadingState';

import { AuthCard } from './AuthCard';
import { usePublicCountries, usePublicPlans, usePublicProfiles } from './signup/api';
import { PeriodPrice, PlanChoice } from './signup/PlanChoice';

const optional = (max: number) => z.string().trim().max(max);
const https = z
  .string()
  .trim()
  .max(500)
  .regex(/^(https:\/\/[^\s/@]+\.[^\s/@]+(\/\S*)?)?$/);

// Contrôles d'ergonomie ; le serveur revalide tout (et reste seul juge).
const schema = z.object({
  full_name: z.string().trim().min(1).max(150),
  email: z.string().trim().email(),
  password: z.string().min(8).max(256),
  confirm: z.string(),
  name: z.string().trim().min(1).max(150),
  country_code: z.string().length(2),
  currency: z.string().length(3),
  trade_name: optional(150),
  company_email: z.union([z.literal(''), z.string().trim().email()]),
  phone: optional(30).regex(/^[+0-9 ().\-/]*$/),
  address: optional(255),
  city: optional(100),
  region: optional(100),
  website: https,
  tax_id: optional(50),
  trade_register: optional(50),
  description: optional(1000),
  logo_url: https,
  sector: z.string().min(1),
  business_profile: z.string().min(1),
  plan_code: z.string().min(1),
  billing_period: z.enum(['monthly', 'annual']),
});
type FormValues = z.infer<typeof schema>;

const STEPS = ['account', 'company', 'activity', 'plan', 'confirm'] as const;
const STEP_FIELDS: FieldPath<FormValues>[][] = [
  ['full_name', 'email', 'password', 'confirm'],
  [
    'name',
    'country_code',
    'currency',
    'trade_name',
    'company_email',
    'phone',
    'address',
    'city',
    'region',
    'website',
    'tax_id',
    'trade_register',
    'description',
    'logo_url',
  ],
  ['sector', 'business_profile'],
  ['plan_code', 'billing_period'],
  [],
];
// Étape à rouvrir selon le code d'erreur du serveur.
const ERROR_STEP: Record<string, number> = {
  password_too_short: 0,
  password_too_weak: 0,
  unknown_country: 1,
  invalid_currency: 1,
  country_required: 1,
  unknown_profile: 2,
  plan_not_available: 3,
};

const RECOMMENDED = [
  ['trade_name', 'text'],
  ['company_email', 'email'],
  ['phone', 'tel'],
  ['address', 'text'],
  ['city', 'text'],
  ['region', 'text'],
  ['tax_id', 'text'],
  ['trade_register', 'text'],
  ['logo_url', 'url'],
] as const;

const DEFAULTS: FormValues = {
  full_name: '',
  email: '',
  password: '',
  confirm: '',
  name: '',
  country_code: '',
  currency: '',
  trade_name: '',
  company_email: '',
  phone: '',
  address: '',
  city: '',
  region: '',
  website: '',
  tax_id: '',
  trade_register: '',
  description: '',
  logo_url: '',
  sector: '',
  business_profile: '',
  plan_code: '',
  billing_period: 'monthly',
};

const orUndefined = (value: string) => value.trim() || undefined;

export function SignupPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const countries = usePublicCountries();
  const catalog = usePublicProfiles();
  const offers = usePublicPlans();
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: DEFAULTS });
  const errors = form.formState.errors;
  // Valeurs courantes (champs non encore touchés : valeurs par défaut).
  const values = { ...DEFAULTS, ...useWatch({ control: form.control }) } as FormValues;

  const currencies = useMemo(
    () => [...new Set((countries.data ?? []).map((c) => c.currency))].sort(),
    [countries.data],
  );
  const profiles = (catalog.data?.profiles ?? []).filter((p) => p.sector === values.sector);
  const plans = offers.data?.plans ?? [];
  const selectedPlan = plans.find((p) => p.code === values.plan_code);
  const selectedProfile = catalog.data?.profiles.find((p) => p.code === values.business_profile);
  const selectedCountry = countries.data?.find((c) => c.code === values.country_code);

  if (auth.status === 'authenticated') return <Navigate to="/" replace />;

  const next = async () => {
    setError(null);
    const valid = await form.trigger(STEP_FIELDS[step]);
    if (step === 0 && values.confirm !== values.password) {
      form.setError('confirm', { message: 'mismatch' });
      return;
    }
    if (valid) setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  const onSubmit = form.handleSubmit(async (v) => {
    setError(null);
    const input: SignupInput = {
      account: { full_name: v.full_name, email: v.email, password: v.password },
      company: {
        name: v.name,
        country_code: v.country_code,
        currency: v.currency,
        trade_name: orUndefined(v.trade_name),
        email: orUndefined(v.company_email),
        phone: orUndefined(v.phone),
        address: orUndefined(v.address),
        city: orUndefined(v.city),
        region: orUndefined(v.region),
        website: orUndefined(v.website),
        tax_id: orUndefined(v.tax_id),
        trade_register: orUndefined(v.trade_register),
        description: orUndefined(v.description),
        logo_url: orUndefined(v.logo_url),
      },
      business_profile: v.business_profile,
      plan_code: v.plan_code,
      billing_period: v.billing_period,
    };
    try {
      await auth.signup(input);
      void navigate('/', { replace: true });
    } catch (e) {
      setError(e);
      const target = e instanceof ApiError ? ERROR_STEP[e.code] : undefined;
      if (target !== undefined) setStep(target);
    }
  });

  const loading = countries.isPending || catalog.isPending || offers.isPending;
  const loadError = countries.error ?? catalog.error ?? offers.error;
  const text = (name: FieldPath<FormValues>, type = 'text', autoComplete?: string) => (
    <InputText
      id={name}
      type={type}
      autoComplete={autoComplete}
      invalid={Boolean(errors[name])}
      {...form.register(name)}
    />
  );
  const fieldError = (name: FieldPath<FormValues>, key = 'validation.invalid') =>
    errors[name] ? t(key) : undefined;

  return (
    <AuthCard title={t('signup.title')} wide>
      <p className="sm-muted">{t('signup.intro')}</p>
      <Steps
        model={STEPS.map((s) => ({ label: t(`signup.steps.${s}`) }))}
        activeIndex={step}
        readOnly
        className="sm-signup-steps"
      />
      <p className="sm-sr-only" aria-live="polite">
        {t('signup.stepOf', { current: step + 1, total: STEPS.length })}
      </p>
      {loadError ? (
        <ErrorMessage error={loadError} />
      ) : loading ? (
        <LoadingState />
      ) : (
        <form onSubmit={onSubmit} className="sm-form" noValidate>
          {error !== null && (
            <Message
              severity="error"
              text={translateError(t, error)}
              className="sm-block"
              role="alert"
            />
          )}

          {step === 0 && (
            <FormSection title={t('signup.steps.account')}>
              <FormField
                id="full_name"
                label={t('signup.fullName')}
                required
                error={fieldError('full_name', 'validation.required')}
              >
                {text('full_name', 'text', 'name')}
              </FormField>
              <FormField
                id="email"
                label={t('auth.email')}
                required
                error={fieldError('email', 'auth.emailInvalid')}
              >
                {text('email', 'email', 'username')}
              </FormField>
              <FormField
                id="password"
                label={t('auth.password')}
                required
                help={t('auth.passwordMin', { count: 8 })}
                error={errors.password && t('auth.passwordMin', { count: 8 })}
              >
                <Controller
                  control={form.control}
                  name="password"
                  render={({ field }) => (
                    <Password
                      inputId="password"
                      value={field.value}
                      onChange={(e) => field.onChange(e.target.value)}
                      feedback={false}
                      toggleMask
                      autoComplete="new-password"
                    />
                  )}
                />
              </FormField>
              <FormField
                id="confirm"
                label={t('auth.confirmPassword')}
                required
                error={errors.confirm && t('auth.passwordMismatch')}
              >
                <Controller
                  control={form.control}
                  name="confirm"
                  render={({ field }) => (
                    <Password
                      inputId="confirm"
                      value={field.value}
                      onChange={(e) => field.onChange(e.target.value)}
                      feedback={false}
                      toggleMask
                      autoComplete="new-password"
                    />
                  )}
                />
              </FormField>
            </FormSection>
          )}

          {step === 1 && (
            <>
              <FormSection title={t('signup.companyRequired')}>
                <FormField
                  id="name"
                  label={t('signup.companyName')}
                  required
                  error={fieldError('name', 'validation.required')}
                >
                  {text('name', 'text', 'organization')}
                </FormField>
                <div className="sm-form-grid">
                  <FormField
                    id="country_code"
                    label={t('signup.country')}
                    required
                    error={fieldError('country_code', 'validation.required')}
                  >
                    <Controller
                      control={form.control}
                      name="country_code"
                      render={({ field }) => (
                        <Dropdown
                          inputId="country_code"
                          value={field.value}
                          options={countries.data}
                          optionLabel="name"
                          optionValue="code"
                          filter
                          placeholder={t('signup.chooseCountry')}
                          onChange={(e) => {
                            field.onChange(e.value);
                            const country = countries.data?.find((c) => c.code === e.value);
                            if (country) form.setValue('currency', country.currency);
                          }}
                        />
                      )}
                    />
                  </FormField>
                  <FormField
                    id="currency"
                    label={t('signup.currency')}
                    required
                    help={t('signup.currencyHelp')}
                    error={fieldError('currency', 'validation.required')}
                  >
                    <Controller
                      control={form.control}
                      name="currency"
                      render={({ field }) => (
                        <Dropdown
                          inputId="currency"
                          value={field.value}
                          options={currencies}
                          filter
                          onChange={(e) => field.onChange(e.value)}
                        />
                      )}
                    />
                  </FormField>
                </div>
              </FormSection>
              <FormSection
                title={t('signup.companyRecommended')}
                description={t('signup.companyRecommendedHelp')}
              >
                <div className="sm-form-grid">
                  {RECOMMENDED.map(([name, type]) => (
                    <FormField
                      key={name}
                      id={name}
                      label={t(`signup.fields.${name}`)}
                      help={name === 'logo_url' ? t('signup.logoHelp') : undefined}
                      error={fieldError(name)}
                    >
                      {text(name, type)}
                    </FormField>
                  ))}
                </div>
              </FormSection>
              <FormSection title={t('signup.companyOptional')}>
                <FormField
                  id="website"
                  label={t('signup.fields.website')}
                  error={fieldError('website')}
                >
                  {text('website', 'url')}
                </FormField>
                <FormField
                  id="description"
                  label={t('signup.fields.description')}
                  error={fieldError('description')}
                >
                  <InputTextarea id="description" rows={3} {...form.register('description')} />
                </FormField>
              </FormSection>
            </>
          )}

          {step === 2 && (
            <FormSection title={t('signup.steps.activity')} description={t('signup.activityHelp')}>
              <div className="sm-sector-grid" role="radiogroup" aria-label={t('signup.sector')}>
                {catalog.data?.sectors.map((sector) => (
                  <Button
                    key={sector.code}
                    type="button"
                    role="radio"
                    aria-checked={values.sector === sector.code}
                    icon={sector.icon ?? undefined}
                    label={sectorLabel(t, sector)}
                    outlined={values.sector !== sector.code}
                    onClick={() => {
                      form.setValue('sector', sector.code, { shouldValidate: true });
                      form.setValue('business_profile', '');
                    }}
                  />
                ))}
              </div>
              {errors.sector && (
                <small className="p-error" role="alert">
                  {t('signup.sectorRequired')}
                </small>
              )}
              {values.sector && (
                <FormField
                  id="business_profile"
                  label={t('signup.profile')}
                  required
                  error={fieldError('business_profile', 'validation.required')}
                >
                  <Controller
                    control={form.control}
                    name="business_profile"
                    render={({ field }) => (
                      <Dropdown
                        inputId="business_profile"
                        value={field.value}
                        options={profiles.map((p) => ({
                          label: profileLabel(t, p),
                          value: p.code,
                        }))}
                        placeholder={t('signup.chooseProfile')}
                        onChange={(e) => field.onChange(e.value)}
                      />
                    )}
                  />
                </FormField>
              )}
            </FormSection>
          )}

          {step === 3 && (
            <FormSection title={t('signup.steps.plan')}>
              <PlanChoice
                plans={plans}
                contactEmail={offers.data?.contact_email ?? null}
                value={values.plan_code}
                period={values.billing_period}
                onChange={(plan, period) => {
                  form.setValue('plan_code', plan, { shouldValidate: true });
                  form.setValue('billing_period', period);
                }}
              />
              {errors.plan_code && (
                <small className="p-error" role="alert">
                  {t('signup.planRequired')}
                </small>
              )}
            </FormSection>
          )}

          {step === 4 && selectedPlan && (
            <FormSection title={t('signup.steps.confirm')}>
              <dl className="sm-details" data-testid="signup-summary">
                <div>
                  <dt>{t('signup.summary.account')}</dt>
                  <dd>
                    {values.full_name} · {values.email}
                  </dd>
                </div>
                <div>
                  <dt>{t('signup.summary.company')}</dt>
                  <dd>
                    {values.name}
                    {selectedCountry && ` · ${selectedCountry.name}`} · {values.currency}
                  </dd>
                </div>
                <div>
                  <dt>{t('signup.summary.activity')}</dt>
                  <dd>{selectedProfile ? profileLabel(t, selectedProfile) : '—'}</dd>
                </div>
                <div>
                  <dt>{t('signup.summary.plan')}</dt>
                  <dd>
                    {selectedPlan.name} · {t(`billingPeriod.${values.billing_period}`)} ·{' '}
                    <PeriodPrice plan={selectedPlan} period={values.billing_period} />
                  </dd>
                </div>
              </dl>
              <Message
                severity="info"
                className="sm-block"
                text={
                  selectedPlan.trial_days > 0
                    ? t('signup.trialNotice', { count: selectedPlan.trial_days })
                    : t('signup.pendingNotice')
                }
              />
            </FormSection>
          )}

          <div className="sm-form-actions">
            {step > 0 && (
              <Button
                type="button"
                icon="pi pi-arrow-left"
                label={t('signup.previous')}
                text
                onClick={() => setStep((s) => s - 1)}
              />
            )}
            {step < STEPS.length - 1 ? (
              // Clés distinctes : sans elles, React réutiliserait le même bouton, devenu
              // « submit » pendant le clic sur « Suivant », et enverrait le formulaire sans
              // passer par la confirmation.
              <Button
                key="next"
                type="button"
                icon="pi pi-arrow-right"
                iconPos="right"
                label={t('signup.next')}
                onClick={() => void next()}
              />
            ) : (
              <Button
                key="submit"
                type="submit"
                icon="pi pi-check"
                label={t('signup.submit')}
                loading={form.formState.isSubmitting}
              />
            )}
          </div>
        </form>
      )}
      <p className="sm-auth-switch">
        {t('signup.haveAccount')} <Link to="/login">{t('auth.submit')}</Link>
      </p>
    </AuthCard>
  );
}
