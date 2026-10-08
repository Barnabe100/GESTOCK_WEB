import type { TFunction } from 'i18next';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { StatusBadge } from '@/shared/ui/StatusBadge';

import type { LicenseProposal, TenantListItem, TenantStatus } from './types';

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

/**
 * Profil d'activité d'une entreprise dans la liste (palier F, lecture seule) : celui de ses sites
 * actifs — un seul profil : son nom ; plusieurs : « N profils » (noms en infobulle) ; aucun
 * site : le profil d'origine, marqué comme celui de l'inscription. Présentation seulement.
 */
export function tenantProfileSummary(
  t: TFunction,
  tenant: Pick<TenantListItem, 'business_profile_name' | 'site_profiles'>,
): { label: string; title?: string } {
  const [only, ...others] = tenant.site_profiles;
  if (!only) {
    return { label: t('console:tenants.originProfile', { name: tenant.business_profile_name }) };
  }
  if (others.length === 0) return { label: only.name };
  const profiles = tenant.site_profiles;
  const names = profiles.map((p) => p.name).join(', ');
  return {
    label: t('console:tenants.profilesCount', { count: profiles.length }),
    title: t('console:tenants.profilesList', { names }),
  };
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
