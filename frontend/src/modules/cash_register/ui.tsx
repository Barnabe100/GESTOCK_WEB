import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { formatMoney } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { CashMovementType, CashSessionStatus } from './api';

const SESSION_TONES: Record<CashSessionStatus, Tone> = { OPEN: 'success', CLOSED: 'neutral' };

export function CashSessionBadge({ status }: { status: CashSessionStatus }) {
  const { t } = useTranslation();
  return <StatusBadge tone={SESSION_TONES[status]} label={t(`cash.sessionStatus.${status}`)} />;
}

/** Entrées en succès, sorties en avertissement, fond initial neutre. */
const MOVEMENT_TONES: Record<CashMovementType, Tone> = {
  OPENING_FLOAT: 'neutral',
  SALE_CASH_IN: 'success',
  MANUAL_CASH_IN: 'info',
  SALE_CASH_REVERSAL: 'danger',
  MANUAL_CASH_OUT: 'warning',
};

export function CashMovementBadge({ type }: { type: CashMovementType }) {
  const { t } = useTranslation();
  return <StatusBadge tone={MOVEMENT_TONES[type]} label={t(`cash.movementType.${type}`)} />;
}

export type VarianceKind = 'shortage' | 'excess' | 'none';

/** Sens d'un écart d'après le signe de sa chaîne décimale (aucun calcul). */
export function varianceKind(value: string): VarianceKind {
  if (value.startsWith('-')) return 'shortage';
  return /[1-9]/.test(value) ? 'excess' : 'none';
}

const VARIANCE_TONES: Record<VarianceKind, Tone> = {
  shortage: 'danger',
  excess: 'warning',
  none: 'success',
};

/** Écart : montant signé + libellé (« Manquant », « Excédent », « Aucun écart »). */
export function CashVariance({
  value,
  currency,
  locale,
}: {
  value: string | null;
  currency: string;
  locale: string;
}) {
  const { t } = useTranslation();
  if (value === null) return <span className="sm-muted">—</span>;
  const kind = varianceKind(value);
  const amount = formatMoney(value, currency, locale);
  return (
    <span className="sm-variance">
      <span className="sm-num">{kind === 'excess' ? `+${amount}` : amount}</span>
      <StatusBadge tone={VARIANCE_TONES[kind]} label={t(`cash.variance.${kind}`)} />
    </span>
  );
}

/** Erreurs de caisse : le solde disponible renvoyé par le serveur est formaté. */
export function cashError(t: TFunction, error: unknown, currency: string, locale = 'fr'): string {
  if (error instanceof ApiError && error.code === 'cash_insufficient_balance') {
    const balance = typeof error.extra.balance === 'string' ? error.extra.balance : '0';
    return t('errors:cash_insufficient_balance', {
      balance: formatMoney(balance, currency, locale),
    });
  }
  return translateError(t, error);
}
