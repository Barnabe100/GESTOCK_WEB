import { zodResolver } from '@hookform/resolvers/zod';
import { Button } from 'primereact/button';
import { Message } from 'primereact/message';
import { Password } from 'primereact/password';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { Navigate } from 'react-router';
import { z } from 'zod';

import { useAuth } from '@/core/auth/AuthContext';
import { translateError } from '@/shared/lib/errors';
import { FormField } from '@/shared/ui/FormField';

import { AuthCard } from './AuthCard';

// Indicatif : la règle de référence (longueur, etc.) est appliquée par le backend.
const MIN_LENGTH = 8;

const schema = z
  .object({
    current: z.string().min(1),
    next: z.string().min(MIN_LENGTH),
    confirm: z.string(),
  })
  .refine((v) => v.next === v.confirm, { path: ['confirm'] });
type FormValues = z.infer<typeof schema>;

export function ChangePasswordPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const [error, setError] = useState<unknown>(null);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { current: '', next: '', confirm: '' },
  });
  const errors = form.formState.errors;

  if (auth.status === 'anonymous') return <Navigate to="/login" replace />;
  if (auth.status === 'authenticated' && !auth.user?.must_change_password) {
    return <Navigate to="/" replace />;
  }

  const onSubmit = form.handleSubmit(async ({ current, next }) => {
    setError(null);
    try {
      await auth.changePassword(current, next);
    } catch (e) {
      setError(e);
    }
  });

  const passwordField = (name: keyof FormValues, id: string, autoComplete: string) => (
    <Controller
      control={form.control}
      name={name}
      render={({ field }) => (
        <Password
          inputId={id}
          value={field.value}
          onChange={(e) => field.onChange(e.target.value)}
          feedback={false}
          toggleMask
          autoComplete={autoComplete}
        />
      )}
    />
  );

  return (
    <AuthCard title={t('auth.changePasswordTitle')}>
      <p className="sm-muted">{t('auth.changePasswordIntro')}</p>
      <form onSubmit={onSubmit} className="sm-form" noValidate>
        {error !== null && <Message severity="error" text={translateError(t, error)} />}
        <FormField
          id="current"
          label={t('auth.currentPassword')}
          error={errors.current && t('auth.passwordRequired')}
        >
          {passwordField('current', 'current', 'current-password')}
        </FormField>
        <FormField
          id="next"
          label={t('auth.newPassword')}
          help={t('auth.passwordMin', { count: MIN_LENGTH })}
          error={errors.next && t('auth.passwordMin', { count: MIN_LENGTH })}
        >
          {passwordField('next', 'next', 'new-password')}
        </FormField>
        <FormField
          id="confirm"
          label={t('auth.confirmPassword')}
          error={errors.confirm && t('auth.passwordMismatch')}
        >
          {passwordField('confirm', 'confirm', 'new-password')}
        </FormField>
        <Button type="submit" label={t('actions.save')} loading={form.formState.isSubmitting} />
        <Button type="button" label={t('actions.logout')} text onClick={() => void auth.logout()} />
      </form>
    </AuthCard>
  );
}
