import type { TFunction } from 'i18next';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/shared/ui/StatusBadge';

import type { LicenseProposal, TenantStatus } from './types';

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
/**
 * Postes d'une proposition de licence (R1) : ceux de la licence actuelle, la demande explicite
 * de l'entreprise (sinon reconduction) et la proposition, que TechNova confirme ou ajuste.
 */
export function postesRows(
  t: TFunction,
  p: Pick<
    LicenseProposal,
    | 'initial_requested_activations'
    | 'current_activations'
    | 'requested_activations'
    | 'max_activations'
  >,
): [string, ReactNode, string?][] {
  const rows: [string, ReactNode, string?][] = [];
  if (p.current_activations === null) {
    rows.push([
      t('console:license.initialActivations'),
      String(p.initial_requested_activations),
      'proposal-initial',
    ]);
  } else {
    rows.push([
      t('console:license.currentActivations'),
      String(p.current_activations),
      'proposal-current',
    ]);
    rows.push([
      t('console:license.companyRequest'),
      p.requested_activations === null
        ? t('console:license.renewalSame')
        : String(p.requested_activations),
      'proposal-request',
    ]);
  }
  rows.push([t('console:license.proposedActivations'), String(p.max_activations), 'proposal-max']);
  return rows;
}

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
