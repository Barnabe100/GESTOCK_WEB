import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';

import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { LevelState } from './api';

const STATE_TONES: Record<LevelState, Tone> = {
  ok: 'success',
  low: 'warning',
  out: 'danger',
  not_stocked: 'neutral',
};

export function LevelStateTag({ state }: { state: LevelState }) {
  const { t } = useTranslation();
  return <StatusBadge tone={STATE_TONES[state]} label={t(`stock.states.${state}`)} />;
}

/** Message d'erreur ; « stock insuffisant » détaille les articles et le stock disponible. */
export function stockError(t: TFunction, error: unknown, locale = 'fr'): string {
  if (error instanceof ApiError && error.code === 'insufficient_stock') {
    const articles = Array.isArray(error.extra.articles)
      ? (error.extra.articles as { reference: string | null; available: string }[])
      : [];
    const details = articles
      .map((a) =>
        t('stock.shortage', {
          reference: a.reference ?? '?',
          available: formatQuantity(a.available, locale),
        }),
      )
      .join(', ');
    return t('errors:insufficient_stock', { details });
  }
  return translateError(t, error);
}

/** Seuil effectif ; « * » signale une surcharge propre au site. */
export function thresholdText(value: string | null, override: string | null, locale: string) {
  return value === null ? '—' : formatQuantity(value, locale) + (override !== null ? ' *' : '');
}
