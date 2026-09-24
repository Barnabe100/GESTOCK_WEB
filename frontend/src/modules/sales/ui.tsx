import type { TFunction } from 'i18next';
import { Tag } from 'primereact/tag';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/core/api/client';
import { stockError } from '@/modules/stock/ui';

import type { SaleStatus } from './api';

// Annulée en rouge : distincte du brouillon (bleu) d'un coup d'œil.
const SEVERITY = { DRAFT: 'info', VALIDATED: 'success', CANCELLED: 'danger' } as const;

export function SaleStatusTag({ status }: { status: SaleStatus }) {
  const { t } = useTranslation();
  return <Tag severity={SEVERITY[status]} value={t(`sales.statuses.${status}`)} />;
}

/** Messages d'erreur : stock insuffisant détaillé, prix modifiés listés. */
export function saleError(t: TFunction, error: unknown, locale = 'fr'): string {
  if (error instanceof ApiError && error.code === 'sale_prices_changed') {
    const articles = Array.isArray(error.extra.articles) ? error.extra.articles.join(', ') : '';
    return t('errors:sale_prices_changed', { articles });
  }
  return stockError(t, error, locale);
}
