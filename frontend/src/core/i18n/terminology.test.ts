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

describe('terminologie des profils UX (Phase 3.1)', () => {
  it('Restauration : « Produits » dans le menu, les modules et les indicateurs', () => {
    applyTerminology(i18n, { fr: { catalog: { item: 'Produit', items: 'Produits' } } });
    expect(i18n.t('nav.articles')).toBe('Produits');
    expect(i18n.t('dashboard.outOfStock')).toBe('Produits en rupture');
    // Termes techniques stables : les ventes restent des ventes (pas de module Commandes).
    expect(i18n.t('nav.sales')).toBe('Ventes');
  });

  it('Automobile, avec surcharge du profil pneumatique', () => {
    applyTerminology(i18n, { fr: { catalog: { item: 'Pièce', items: 'Pièces' } } });
    expect(i18n.t('dashboard.lowStock')).toBe('Pièces sous le seuil');
    applyTerminology(i18n, { fr: { catalog: { item: 'Pneu', items: 'Pneus' } } });
    expect(i18n.t('modules.catalog')).toBe('Pneus');
    applyTerminology(i18n, undefined);
    expect(i18n.t('dashboard.outOfStock')).toBe('Articles en rupture');
  });

  it('traduit chaque secteur et chaque profil par son code technique', () => {
    expect(i18n.t('sectors.restaurant')).toBe('Restauration');
    expect(i18n.t('businessProfiles.restaurant.restaurant')).toBe('Restaurant');
    expect(i18n.t('businessProfiles.retail.librairie')).toBe('Librairie / Papeterie');
    expect(i18n.t('navGroups.restaurant')).toBe('Restaurant');
  });
});
