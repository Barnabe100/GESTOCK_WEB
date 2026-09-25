import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { Password } from 'primereact/password';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Link, Navigate } from 'react-router';
import { z } from 'zod';

import { useAuth } from '@/core/auth/AuthContext';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';

import { AuthCard } from './AuthCard';

const schema = z.object({
  email: z.string().trim().email(),
  password: z.string().min(1),
});
type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const [error, setError] = useState<unknown>(null);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  });
  const errors = form.formState.errors;

  if (auth.status === 'authenticated') return <Navigate to="/" replace />;

  const onSubmit = form.handleSubmit(async ({ email, password }) => {
    setError(null);
    try {
      await auth.login(email, password);
    } catch (e) {
      setError(e);
    }
  });

  return (
    <AuthCard title={t('auth.loginTitle')}>
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {error !== null && <Message severity="error" text={translateError(t, error)} />}
        <FormField
          id="email"
          label={t('auth.email')}
          error={errors.email && t('auth.emailInvalid')}
        >
          <InputText
            id="email"
            type="email"
            autoComplete="username"
            autoFocus
            {...form.register('email')}
          />
        </FormField>
        <FormField
          id="password"
          label={t('auth.password')}
          error={errors.password && t('auth.passwordRequired')}
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
                autoComplete="current-password"
              />
            )}
          />
        </FormField>
        <Button type="submit" label={t('auth.submit')} loading={form.formState.isSubmitting} />
      </form>
      <p className="sm-auth-switch">
        {t('auth.noAccount')} <Link to="/signup">{t('auth.createCompany')}</Link>
      </p>
    </AuthCard>
  );
}
