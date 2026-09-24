import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { stockError } from '@/modules/stock/ui';
import { formatMoney } from '@/shared/lib/decimal';
import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { PaymentStatus, SalePaymentStatus } from './api';

/** Messages d'erreur : stock insuffisant détaillé, prix modifiés listés. */
export function saleError(t: TFunction, error: unknown, locale = 'fr'): string {
  if (error instanceof ApiError && error.code === 'sale_prices_changed') {
    const articles = Array.isArray(error.extra.articles) ? error.extra.articles.join(', ') : '';
    return t('errors:sale_prices_changed', { articles });
  }
  return stockError(t, error, locale);
}

/** Tonalités : payée (succès), partiellement (avertissement), non payée (danger). */
const SALE_PAYMENT_TONES: Record<SalePaymentStatus, Tone> = {
  UNPAID: 'danger',
  PARTIALLY_PAID: 'warning',
  PAID: 'success',
};

export function SalePaymentBadge({ status }: { status: SalePaymentStatus }) {
  const { t } = useTranslation();
  return (
    <StatusBadge tone={SALE_PAYMENT_TONES[status]} label={t(`sales.paymentStatus.${status}`)} />
  );
}

const PAYMENT_TONES: Record<PaymentStatus, Tone> = {
  PENDING: 'info',
  COMPLETED: 'success',
  CANCELLED: 'danger',
};

export function PaymentStatusBadge({ status }: { status: PaymentStatus }) {
  const { t } = useTranslation();
  return <StatusBadge tone={PAYMENT_TONES[status]} label={t(`payment.status.${status}`)} />;
}

/** Erreurs d'encaissement : le reste à payer renvoyé par le serveur est formaté (devise). */
export function paymentError(
  t: TFunction,
  error: unknown,
  currency: string,
  locale = 'fr',
): string {
  if (error instanceof ApiError && error.code === 'payment_exceeds_balance') {
    const remaining = typeof error.extra.remaining === 'string' ? error.extra.remaining : '';
    return t('errors:payment_exceeds_balance', {
      remaining: formatMoney(remaining, currency, locale),
    });
  }
  return saleError(t, error, locale);
}
