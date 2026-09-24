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

describe('limite de crédit', () => {
  const normalize = (s: string) => s.replace(/\s/g, ' ');

  it('montants renvoyés par le serveur formatés (vue consolidée)', () => {
    const error = new ApiError(422, 'credit_limit_exceeded', 'x', {
      credit_limit: '100000.00',
      sale_exposure: '40000.00',
      current_exposure: '70000.00',
      available_credit: '30000.00',
    });
    const message = normalize(saleError(i18n.t, error, 'fr', 'XOF'));
    expect(message).toMatch(/^Limite de crédit du client dépassée/);
    expect(message).toMatch(/40 000 F\s?CFA à crédit pour 30 000 F\s?CFA de crédit disponible/);
    expect(message).toMatch(/limite : 100 000 F\s?CFA/);
  });

  it('sans vue consolidée : ni exposition ni crédit disponible', () => {
    const error = new ApiError(422, 'credit_limit_exceeded', 'x', {
      credit_limit: '100000.00',
      sale_exposure: '30000.00',
    });
    const message = normalize(saleError(i18n.t, error, 'fr', 'XOF'));
    expect(message).toMatch(/laisserait 30 000 F\s?CFA à crédit \(limite : 100 000 F\s?CFA\)/);
    expect(message).not.toContain('disponible');
  });
});
