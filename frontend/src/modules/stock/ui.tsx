import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { formatQuantity } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { translateError } from '@/shared/lib/errors';

import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { LevelState, LotState } from './api';

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

const LOT_TONES: Record<LotState, Tone> = {
  ok: 'success',
  expiring_soon: 'warning',
  expired: 'danger',
  no_expiry: 'neutral',
};

/** État de péremption d'un lot (Lot 3-G) — calculé par le serveur, jamais par l'interface. */
export function LotStateTag({ state }: { state: LotState }) {
  const { t } = useTranslation();
  return <StatusBadge tone={LOT_TONES[state]} label={t(`lots.states.${state}`)} />;
}

/** Lot d'une ligne ou d'un mouvement : « Lot L001 · péremption 12/12/2026 » + état. */
export function LotLabel({
  number,
  expiry,
  state,
  locale,
}: {
  number: string | null | undefined;
  expiry?: string | null;
  state?: LotState | null;
  locale: string;
}) {
  const { t } = useTranslation();
  if (!number) return null;
  return (
    <span className="sm-lot">
      <span>
        {t('lots.lotNumberShort', { number })}
        {expiry ? ` · ${t('lots.expiresOn', { date: formatDate(expiry, locale, 'UTC') })}` : ''}
      </span>
      {state && state !== 'no_expiry' && <LotStateTag state={state} />}
    </span>
  );
}

/** Message d'erreur ; « stock insuffisant » détaille les articles et le stock disponible. */
export function stockError(t: TFunction, error: unknown, locale = 'fr'): string {
  if (error instanceof ApiError && error.code === 'insufficient_lot_stock') {
    const lots = Array.isArray(error.extra.lots)
      ? (error.extra.lots as { lot_number: string | null; available: string }[])
      : [];
    const details = lots
      .map((l) =>
        t('lots.shortage', {
          number: l.lot_number ?? '?',
          available: formatQuantity(l.available, locale),
        }),
      )
      .join(', ');
    return t('errors:insufficient_lot_stock', { details });
  }
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
