import type { ReactNode } from 'react';
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

/** Liste libellé → valeur des fiches de la console (valeur absente : « — »). */
export function Details({ items }: { items: [string, ReactNode, string?][] }) {
  return (
    <dl className="sm-details">
      {items.map(([label, value, testId]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd data-testid={testId}>{value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Jour déclaré d'un paiement (date sans heure) : affiché tel quel, sans conversion de fuseau. */
export function paymentDay(value: string): string {
  return tenantDate(value, 'UTC');
}
