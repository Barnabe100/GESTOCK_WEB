import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { formatQuantity } from '@/shared/lib/decimal';
import { formatDate } from '@/shared/lib/format';
import { translateError } from '@/shared/lib/errors';

import { StatusBadge, type Tone } from '@/shared/ui/StatusBadge';

import type { LevelState, LineLot, LotState } from './api';

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

/**
 * Répartition par lot d'une ligne (Lot 3-H-A) : « Lot A · péremption … — 20 u » par lot, dans
 * l'ordre de consommation. Rien pour une ligne sans lot.
 */
export function LineLotsList({
  lots,
  unit,
  locale,
}: {
  lots: (Omit<LineLot, 'state'> & { state?: LotState | null })[];
  unit: string;
  locale: string;
}) {
  const { t } = useTranslation();
  if (lots.length === 0) return null;
  return (
    <ul className="sm-line-lots" aria-label={t('lotAllocation.lotsOfLine')}>
      {lots.map((lot) => (
        <li key={lot.lot_id}>
          <LotLabel
            number={lot.lot_number}
            expiry={lot.expiry_date}
            state={lot.state}
            locale={locale}
          />
          <span className="sm-num">{`${formatQuantity(lot.quantity, locale)} ${unit}`}</span>
        </li>
      ))}
    </ul>
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
  if (error instanceof ApiError && error.code === 'insufficient_unexpired_stock') {
    // Lot 3-H-A (O-1) : le stock suffit, mais pas ses lots non périmés.
    const details = expiredShortages(error)
      .map((a) =>
        t('lotAllocation.unexpiredShortage', {
          reference: a.reference,
          missing: formatQuantity(a.missing, locale),
          expired: formatQuantity(a.expired_available, locale),
        }),
      )
      .join(', ');
    return t('errors:insufficient_unexpired_stock', { details });
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

/** Lot périmé disponible, renvoyé par le refus `insufficient_unexpired_stock` (Lot 3-H-A). */
export interface ExpiredLotInfo {
  lot_id: string;
  lot_number: string;
  expiry_date: string | null;
  available: string;
}

/** Article dont les lots NON périmés ne suffisent pas (O-1) : manque et lots périmés. */
export interface ExpiredShortage {
  article_id: string;
  reference: string;
  missing: string;
  expired_available: string;
  expired_lots: ExpiredLotInfo[];
}

/** Détail du refus `insufficient_unexpired_stock` (vide pour toute autre erreur). */
export function expiredShortages(error: unknown): ExpiredShortage[] {
  if (!(error instanceof ApiError) || error.code !== 'insufficient_unexpired_stock') return [];
  return Array.isArray(error.extra.articles) ? (error.extra.articles as ExpiredShortage[]) : [];
}
