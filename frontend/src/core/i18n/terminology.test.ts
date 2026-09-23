import { describe, expect, it } from 'vitest';

import i18n from './index';
import { applyTerminology } from './terminology';

describe('terminologie par profil', () => {
  it('utilise les libellés par défaut', () => {
    applyTerminology(i18n, undefined);
    expect(i18n.t('modules.catalog')).toBe('Articles');
    expect(i18n.t('modules.sales')).toBe('Ventes');
  });

  it('applique les surcharges du profil, puis les réinitialise', () => {
    applyTerminology(i18n, {
      fr: { catalog: { items: 'Produits' }, sales: { sales: 'Commandes' } },
    });
    expect(i18n.t('modules.catalog')).toBe('Produits');
    expect(i18n.t('modules.sales')).toBe('Commandes');

    applyTerminology(i18n, { fr: { catalog: { item: 'Article' } } });
    expect(i18n.t('modules.catalog')).toBe('Articles');
    expect(i18n.t('modules.sales')).toBe('Ventes');
  });
});
