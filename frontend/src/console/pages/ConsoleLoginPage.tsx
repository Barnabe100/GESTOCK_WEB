import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Card } from 'primereact/card';
import { InputText } from 'primereact/inputtext';
import { Message } from 'primereact/message';
import { Password } from 'primereact/password';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Navigate } from 'react-router';
import { z } from 'zod';

import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';

import { useConsoleAuth } from '../ConsoleAuth';
import { CONSOLE_BASE } from '../ConsoleLayout';

const schema = z.object({ email: z.string().trim().email(), password: z.string().min(1) });
type FormValues = z.infer<typeof schema>;

export function ConsoleLoginPage() {
  const { t } = useTranslation();
  const auth = useConsoleAuth();
  const [error, setError] = useState<unknown>(null);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
  });
  const errors = form.formState.errors;

  if (auth.status === 'authenticated') return <Navigate to={`${CONSOLE_BASE}/dashboard`} replace />;

  const onSubmit = form.handleSubmit(async ({ email, password }) => {
    setError(null);
    try {
      await auth.login(email, password);
    } catch (e) {
      setError(e);
    }
  });

  return (
    <main className="sm-auth sm-console" data-accent="technova">
      <div className="sm-auth-brand">
        <span className="sm-logo">TN</span>
        <div>
          <div className="sm-strong">{t('console:brand.product')}</div>
          <small className="sm-muted">{t('console:brand.title')}</small>
        </div>
      </div>
      <Card title={t('console:login.title')} className="sm-auth-card">
        <p className="sm-help">{t('console:login.subtitle')}</p>
        <form onSubmit={onSubmit} className="sm-form" noValidate>
          {error !== null && <Message severity="error" text={translateError(t, error)} />}
          <FormField
            id="email"
            label={t('auth.email')}
            required
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
            required
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
          <Button
            type="submit"
            label={t('console:login.submit')}
            loading={form.formState.isSubmitting}
          />
        </form>
      </Card>
    </main>
  );
}
