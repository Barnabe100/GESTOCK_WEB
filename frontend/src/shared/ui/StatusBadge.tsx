import { useTranslation } from 'react-i18next';

import type { SubscriptionStatus } from '@/core/api/types';

/**
 * Pastille de statut : UNE représentation pour toute l'application. La couleur dépend du sens
 * (`tone`), jamais du module : un brouillon, une validation ou une annulation ont le même
 * aspect partout. Les correspondances statut → tonalité sont centralisées ci-dessous.
 */
export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

export type DocumentStatus = 'DRAFT' | 'VALIDATED' | 'CANCELLED';

export const DOCUMENT_TONES: Record<DocumentStatus, Tone> = {
  DRAFT: 'neutral',
  VALIDATED: 'success',
  CANCELLED: 'danger',
};

export const SUBSCRIPTION_TONES: Record<SubscriptionStatus, Tone> = {
  pending_activation: 'warning',
  trial: 'info',
  active: 'success',
  past_due: 'warning',
  expired: 'danger',
  suspended: 'danger',
  cancelled: 'danger',
};

/** Paiement d'abonnement déclaré à TechNova (Phase 3.3-A) : décision définitive. */
export type SubscriptionPaymentStatus = 'PENDING' | 'CONFIRMED' | 'REJECTED';

export const SUBSCRIPTION_PAYMENT_TONES: Record<SubscriptionPaymentStatus, Tone> = {
  PENDING: 'warning',
  CONFIRMED: 'success',
  REJECTED: 'danger',
};

export function StatusBadge({
  label,
  tone = 'neutral',
  icon,
}: {
  label: string;
  tone?: Tone;
  icon?: string;
}) {
  return (
    <span className={`sm-badge sm-badge--${tone}`}>
      {icon && <i className={icon} aria-hidden />}
      {label}
    </span>
  );
}

/** Statut d'un document (entrée, sortie, transfert, vente) ; libellés propres au document. */
export function DocumentStatusBadge({
  status,
  labels = 'stock.documentStatus',
}: {
  status: DocumentStatus;
  labels?: string;
}) {
  const { t } = useTranslation();
  return <StatusBadge tone={DOCUMENT_TONES[status]} label={t(`${labels}.${status}`)} />;
}

export function ActiveBadge({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return (
    <StatusBadge
      tone={active ? 'success' : 'neutral'}
      label={t(active ? 'common.active' : 'common.inactive')}
    />
  );
}

export function SubscriptionStatusBadge({ status }: { status: SubscriptionStatus }) {
  const { t } = useTranslation();
  return (
    <StatusBadge tone={SUBSCRIPTION_TONES[status]} label={t(`subscriptionStatus.${status}`)} />
  );
}

export function SubscriptionPaymentStatusBadge({ status }: { status: SubscriptionPaymentStatus }) {
  const { t } = useTranslation();
  return (
    <StatusBadge
      tone={SUBSCRIPTION_PAYMENT_TONES[status]}
      label={t(`subscriptionPaymentStatus.${status}`)}
    />
  );
}
