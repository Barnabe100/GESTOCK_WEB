import type { TFunction } from 'i18next';

import { ApiError } from '@/core/api/client';
import { stockError } from '@/modules/stock/ui';

/** Messages d'erreur : stock insuffisant détaillé, prix modifiés listés. */
export function saleError(t: TFunction, error: unknown, locale = 'fr'): string {
  if (error instanceof ApiError && error.code === 'sale_prices_changed') {
    const articles = Array.isArray(error.extra.articles) ? error.extra.articles.join(', ') : '';
    return t('errors:sale_prices_changed', { articles });
  }
  return stockError(t, error, locale);
}
