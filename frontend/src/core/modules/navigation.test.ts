import { describe, expect, it } from 'vitest';

import { FRONTEND_MODULES } from '@/app/modules';

import { buildNavigationSections } from './registry';
import type { UiCapabilities } from './types';

/**
 * Navigation pilotée par le profil UX (Phase 3.1). Les rubriques ci-dessous reproduisent les
 * profils UX du catalogue (backend/app/platform/catalog/data/ux_profiles) ; le menu final ne
 * dépend que de ces données, des modules actifs, des permissions et des fonctionnalités.
 */

const CORE = ['dashboard', 'organization', 'users', 'subscription', 'audit'];
const BUSINESS = [
  'catalog',
  'suppliers',
  'customers',
  'stock',
  'inventory_count',
  'sales',
  'receivables',
  'cash_register',
  'pos',
  'alerts',
];
const ALL_PERMISSIONS = FRONTEND_MODULES.flatMap((m) => m.navigation)
  .map((item) => item.permission)
  .filter((p): p is string => p !== undefined);

const ADMIN = { group: 'admin', modules: ['organization', 'users', 'subscription', 'audit'] };

const LAYOUTS = {
  alimentation: [
    { group: 'home', modules: ['dashboard'] },
    { group: 'sales', modules: ['pos', 'sales', 'customers', 'receivables'] },
    { group: 'cash', modules: ['cash_register'] },
    { group: 'catalog', modules: ['catalog', 'suppliers'] },
    { group: 'stock', modules: ['stock', 'inventory_count', 'alerts'] },
    ADMIN,
  ],
  // Tel que défini par le profil UX, rubrique « restaurant » comprise (modules planifiés) :
  // le frontend ne l'affiche pas davantage que le backend.
  restaurant: [
    { group: 'home', modules: ['dashboard'] },
    {
      group: 'restaurant',
      modules: ['restaurant.tables', 'restaurant.orders', 'restaurant.kitchen'],
    },
    { group: 'sales', modules: ['pos', 'sales', 'customers', 'receivables'] },
    { group: 'cash', modules: ['cash_register'] },
    { group: 'stock', modules: ['catalog', 'stock', 'inventory_count', 'alerts', 'suppliers'] },
    ADMIN,
  ],
  automobile: [
    { group: 'home', modules: ['dashboard'] },
    { group: 'workshop', modules: ['automobile.workshop', 'automobile.vehicles'] },
    { group: 'catalog', modules: ['catalog', 'suppliers'] },
    { group: 'stock', modules: ['stock', 'inventory_count', 'alerts'] },
    { group: 'sales', modules: ['sales', 'pos', 'customers', 'receivables'] },
    { group: 'cash', modules: ['cash_register'] },
    ADMIN,
  ],
  distribution: [
    { group: 'home', modules: ['dashboard'] },
    { group: 'stock', modules: ['stock', 'inventory_count', 'alerts'] },
    { group: 'catalog', modules: ['catalog', 'suppliers'] },
    { group: 'sales', modules: ['sales', 'customers', 'receivables', 'pos'] },
    { group: 'cash', modules: ['cash_register'] },
    ADMIN,
  ],
};

function caps(
  layout: { group: string; modules: string[] }[],
  {
    modules = [...CORE, ...BUSINESS],
    permissions = ALL_PERMISSIONS,
    features = ['stock.transfers'],
  }: { modules?: string[]; permissions?: string[]; features?: string[] } = {},
): UiCapabilities {
  return {
    modules: [
      ...modules.map((code) => ({ code, status: 'available' })),
      { code: 'restaurant.tables', status: 'planned' },
    ],
    permissions,
    features,
    navigation: layout.flatMap((g) => g.modules),
    ux: { navigation: layout },
  };
}

const outline = (c: UiCapabilities) =>
  buildNavigationSections(FRONTEND_MODULES, c).map((s) => [s.group, s.items.map((i) => i.key)]);

describe('navigation pilotée par le profil UX', () => {
  it('Commerce (alimentation) : caisse rapide en tête, puis catalogue et stock', () => {
    const sections = outline(caps(LAYOUTS.alimentation));
    expect(sections.map(([group]) => group)).toEqual([
      'home',
      'sales',
      'cash',
      'catalog',
      'stock',
      'admin',
    ]);
    expect(sections[1]).toEqual(['sales', ['pos', 'sales', 'customers', 'receivables']]);
    expect(sections[4]?.[1]).toContain('stock-transfers');
  });

  it('Restauration : aucune rubrique planifiée, produits dans « Stock »', () => {
    const sections = outline(caps(LAYOUTS.restaurant));
    expect(sections.map(([group]) => group)).toEqual(['home', 'sales', 'cash', 'stock', 'admin']);
    expect(sections[3]?.[1]?.slice(0, 2)).toEqual(['articles', 'categories']);
    expect(sections.flatMap(([, keys]) => keys).join(' ')).not.toMatch(/restaurant|table/);
  });

  it('Automobile : atelier planifié absent, catalogue avant le stock', () => {
    const groups = outline(caps(LAYOUTS.automobile)).map(([group]) => group);
    expect(groups).toEqual(['home', 'catalog', 'stock', 'sales', 'cash', 'admin']);
  });

  it('Distribution : stock en tête ; sans point de vente ni caisse (module inactif)', () => {
    const sections = outline(
      caps(LAYOUTS.distribution, {
        modules: [...CORE, ...BUSINESS.filter((m) => m !== 'pos' && m !== 'cash_register')],
      }),
    );
    expect(sections.map(([group]) => group)).toEqual([
      'home',
      'stock',
      'catalog',
      'sales',
      'admin',
    ]);
    expect(sections.flatMap(([, keys]) => keys)).not.toContain('pos');
  });

  it('permissions et fonctionnalités du plan restent appliquées', () => {
    const sections = outline(
      caps(LAYOUTS.alimentation, {
        permissions: ['sales.sale.view', 'stock.level.view', 'stock.transfer.view'],
        features: [],
      }),
    );
    expect(sections).toEqual([
      ['home', ['dashboard']],
      ['sales', ['sales']],
      // Historique des transferts consultable sans la fonctionnalité (aucune `feature`).
      ['stock', ['stock-levels', 'stock-transfers']],
    ]);
  });

  it('une entrée autorisée non placée par le profil rejoint sa rubrique par défaut', () => {
    const layout = [
      { group: 'home', modules: ['dashboard'] },
      { group: 'sales', modules: ['sales'] },
    ];
    const sections = outline(caps(layout, { modules: [...CORE, 'sales', 'customers', 'alerts'] }));
    expect(sections).toEqual([
      ['home', ['dashboard']],
      ['sales', ['sales', 'customers']],
      // Rubriques par défaut créées à la suite, dans l'ordre des modules.
      ['stock', ['stock-alerts']],
      [
        'admin',
        ['onboarding', 'company', 'sites', 'modules', 'members', 'roles', 'subscription', 'audit'],
      ],
    ]);
  });

  it('sans profil UX : regroupement par rubrique par défaut (compatibilité)', () => {
    const sections = buildNavigationSections(FRONTEND_MODULES, {
      modules: ['dashboard', 'sales'].map((code) => ({ code, status: 'available' })),
      permissions: ['sales.sale.view'],
      navigation: ['dashboard', 'sales'],
    });
    expect(sections.map((s) => s.group)).toEqual(['home', 'sales']);
  });
});
