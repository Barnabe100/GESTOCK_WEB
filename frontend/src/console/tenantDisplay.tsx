import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/shared/ui/StatusBadge';

import type { TenantStatus } from './types';

/** Statut de l'entreprise (suspension TechNova), distinct du statut de l'abonnement. */
export function TenantStatusBadge({ status }: { status: TenantStatus }) {
  const { t } = useTranslation();
  return (
    <StatusBadge
      tone={status === 'active' ? 'success' : 'danger'}
      icon={status === 'active' ? undefined : 'pi pi-ban'}
      label={t(`console:tenantStatus.${status}`)}
    />
  );
}

/** Date du jour métier dans le fuseau de l'entreprise (ADR-0028). */
export function tenantDate(iso: string, timeZone?: string): string {
  return new Intl.DateTimeFormat('fr', { dateStyle: 'medium', timeZone }).format(new Date(iso));
}
