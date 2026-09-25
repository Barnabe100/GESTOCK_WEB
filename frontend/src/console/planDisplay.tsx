import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { formatMoney } from '@/shared/lib/decimal';
import { StatusBadge } from '@/shared/ui/StatusBadge';

import type { Plan } from './types';

/** Montant d'un plan dans sa devise (affichage seulement ; aucune règle calculée ici). */
export function planPrice(value: string | null, currency: string | null): string {
  if (value === null) return '—';
  return currency ? formatMoney(value, currency) : value;
}

export function trialLabel(t: TFunction, days: number): string {
  return days > 0 ? t('console:plans.trialDays', { count: days }) : t('console:plans.noTrial');
}

/** Publication du plan (catalogue + paramètre commercial `listed`). */
export function PlanStatusBadge({ plan }: { plan: Plan }) {
  const { t } = useTranslation();
  if (!plan.is_active) return <StatusBadge tone="danger" label={t('console:plans.retired')} />;
  return plan.listed ? (
    <StatusBadge tone="success" icon="pi pi-eye" label={t('console:plans.listed')} />
  ) : (
    <StatusBadge tone="neutral" icon="pi pi-eye-slash" label={t('console:plans.unlisted')} />
  );
}

/** Type d'offre : `self_service` est calculé par le serveur (règle unique de l'inscription). */
export function PlanModeBadge({ plan }: { plan: Plan }) {
  const { t } = useTranslation();
  if (plan.self_service) return <StatusBadge tone="info" label={t('console:plans.selfService')} />;
  if (plan.contact_required) {
    return <StatusBadge tone="warning" label={t('console:plans.contactOnly')} />;
  }
  return <StatusBadge tone="neutral" label={t('console:plans.notSubscribable')} />;
}
