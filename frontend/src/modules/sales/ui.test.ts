import { describe, expect, it } from 'vitest';

import { ApiError } from '@/core/api/client';
import i18n from '@/core/i18n';

import { saleError } from './ui';

describe('messages du module ventes', () => {
  it('liste les articles dont le prix a changé', () => {
    const error = new ApiError(409, 'sale_prices_changed', 'x', {
      articles: ['VIS-001', 'CLOU-02'],
    });
    expect(saleError(i18n.t, error)).toContain('VIS-001, CLOU-02');
  });

  it('détaille le stock insuffisant comme les documents de stock', () => {
    const error = new ApiError(422, 'insufficient_stock', 'x', {
      articles: [{ article_id: 'a', reference: 'VIS-001', available: '2.000' }],
    });
    expect(saleError(i18n.t, error)).toBe('Stock insuffisant : VIS-001 (disponible : 2).');
  });

  it('traduit les codes propres aux ventes', () => {
    expect(saleError(i18n.t, new ApiError(409, 'sale_not_draft', 'x'))).toBe(
      'Seule une vente en brouillon peut être modifiée ou validée.',
    );
  });
});
