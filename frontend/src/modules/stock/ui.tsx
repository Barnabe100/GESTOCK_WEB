import type { TFunction } from 'i18next';
import { Tag } from 'primereact/tag';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { formatQuantity } from '@/shared/lib/decimal';
import { translateError } from '@/shared/lib/errors';

import type { DocumentStatus, LevelState } from './api';

const STATE_SEVERITY = {
  ok: 'success',
  low: 'warning',
  out: 'danger',
  not_stocked: 'secondary',
} as const;

export function LevelStateTag({ state }: { state: LevelState }) {
  const { t } = useTranslation();
  return <Tag severity={STATE_SEVERITY[state]} value={t(`stock.states.${state}`)} />;
}

const STATUS_SEVERITY = { DRAFT: 'info', VALIDATED: 'success', CANCELLED: 'secondary' } as const;

export function DocumentStatusTag({ status }: { status: DocumentStatus }) {
  const { t } = useTranslation();
  return <Tag severity={STATUS_SEVERITY[status]} value={t(`stock.documentStatus.${status}`)} />;
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
