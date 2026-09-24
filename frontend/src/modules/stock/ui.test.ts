import { describe, expect, it } from 'vitest';

import { ApiError } from '@/core/api/client';
import i18n from '@/core/i18n';

import { stockError, thresholdText } from './ui';

describe('messages du module stock', () => {
  it('détaille les articles en rupture pour « stock insuffisant »', () => {
    const error = new ApiError(422, 'insufficient_stock', 'Stock insuffisant', {
      articles: [
        { article_id: 'a', reference: 'VIS-001', available: '2.500' },
        { article_id: 'b', reference: 'CLOU-02', available: '0.000' },
      ],
    });
    expect(stockError(i18n.t, error)).toBe(
      'Stock insuffisant : VIS-001 (disponible : 2,5), CLOU-02 (disponible : 0).',
    );
  });

  it('traduit les autres codes via le catalogue d’erreurs', () => {
    const error = new ApiError(409, 'document_not_draft', 'x');
    expect(stockError(i18n.t, error)).toBe('Seul un brouillon peut être modifié ou validé.');
  });

  it('signale une surcharge de seuil propre au site', () => {
    expect(thresholdText('5.000', null, 'fr')).toBe('5');
    expect(thresholdText('20.000', '20.000', 'fr')).toBe('20 *');
    expect(thresholdText(null, null, 'fr')).toBe('—');
  });
});
