import { Button } from 'primereact/button';
import { Message } from 'primereact/message';
import { Tag } from 'primereact/tag';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router';

import { useAuth } from '@/core/auth/AuthContext';
import { translateError } from '@/shared/lib/errors';

import { AuthCard } from './AuthCard';

export function SelectTenantPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState<string | null>(null);

  if (auth.status === 'anonymous') return <Navigate to="/login" replace />;

  const choose = async (tenantId: string) => {
    setError(null);
    setPending(tenantId);
    try {
      await auth.selectTenant(tenantId);
      navigate('/', { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setPending(null);
    }
  };

  return (
    <AuthCard title={t('auth.selectTenantTitle')}>
      {auth.memberships.length === 0 ? (
        <Message severity="warn" text={t('auth.noTenant')} />
      ) : (
        <p className="sm-muted">{t('auth.selectTenantIntro')}</p>
      )}
      {error !== null && <Message severity="error" text={translateError(t, error)} />}
      <ul className="sm-tenant-list">
        {auth.memberships.map((m) => (
          <li key={m.tenant_id}>
            <Button
              className="sm-tenant-button"
              outlined
              loading={pending === m.tenant_id}
              onClick={() => void choose(m.tenant_id)}
            >
              <span>{m.tenant_name}</span>
              {m.is_owner && <Tag value={t('auth.owner')} />}
            </Button>
          </li>
        ))}
      </ul>
      <Button label={t('actions.logout')} text onClick={() => void auth.logout()} />
    </AuthCard>
  );
}
