import { describe, expect, it } from 'vitest';

import { buildNavigation, buildRoutes } from './registry';
import type { FrontendModule, UiCapabilities } from './types';

const Page = () => null;

const modules: FrontendModule[] = [
  {
    code: 'dashboard',
    navigation: [{ key: 'home', labelKey: 'nav.dashboard', icon: '', path: '/' }],
    routes: [{ path: '', component: Page }],
  },
  {
    code: 'users',
    navigation: [
      {
        key: 'members',
        labelKey: 'nav.members',
        icon: '',
        path: '/users/members',
        permission: 'users.member.view',
      },
      {
        key: 'roles',
        labelKey: 'nav.roles',
        icon: '',
        path: '/users/roles',
        permission: 'users.role.view',
      },
    ],
    routes: [
      { path: 'users/members', component: Page, permission: 'users.member.view' },
      { path: 'users/roles', component: Page, permission: 'users.role.view' },
    ],
  },
  {
    code: 'restaurant.tables',
    navigation: [{ key: 'tables', labelKey: 'nav.tables', icon: '', path: '/tables' }],
    routes: [{ path: 'tables', component: Page }],
  },
  {
    code: 'stock',
    navigation: [
      { key: 'stock', labelKey: 'nav.stock', icon: '', path: '/stock' },
      {
        key: 'transfers',
        labelKey: 'nav.stockTransfers',
        icon: '',
        path: '/stock/transfers',
        permission: 'stock.transfer.view',
        feature: 'stock.transfers',
      },
    ],
    routes: [
      { path: 'stock', component: Page },
      {
        path: 'stock/transfers',
        component: Page,
        permission: 'stock.transfer.view',
        feature: 'stock.transfers',
      },
    ],
  },
];

function caps(overrides: Partial<UiCapabilities>): UiCapabilities {
  return {
    modules: [
      { code: 'dashboard', status: 'available' },
      { code: 'users', status: 'available' },
    ],
    permissions: ['users.member.view', 'users.role.view'],
    navigation: ['dashboard', 'users'],
    ...overrides,
  };
}

const keys = (items: { key: string }[]) => items.map((i) => i.key);

describe('buildNavigation', () => {
  it('ne propose que les modules effectifs et disponibles', () => {
    const restaurant = caps({
      modules: [
        { code: 'dashboard', status: 'available' },
        { code: 'restaurant.tables', status: 'available' },
        { code: 'stock', status: 'planned' }, // souscrit mais pas encore implémenté
      ],
      navigation: ['dashboard', 'restaurant.tables', 'stock'],
    });
    expect(keys(buildNavigation(modules, restaurant))).toEqual(['home', 'tables']);

    const hardware = caps({
      modules: [
        { code: 'dashboard', status: 'available' },
        { code: 'stock', status: 'available' },
      ],
      navigation: ['dashboard', 'stock'],
    });
    expect(keys(buildNavigation(modules, hardware))).toEqual(['home', 'stock']);
  });

  it("respecte l'ordre fourni par le profil", () => {
    const ordered = caps({
      modules: [
        { code: 'dashboard', status: 'available' },
        { code: 'users', status: 'available' },
        { code: 'restaurant.tables', status: 'available' },
      ],
      navigation: ['restaurant.tables', 'dashboard', 'users'],
    });
    expect(keys(buildNavigation(modules, ordered))).toEqual(['tables', 'home', 'members', 'roles']);
  });

  it('masque les entrées sans permission', () => {
    expect(keys(buildNavigation(modules, caps({ permissions: ['users.member.view'] })))).toEqual([
      'home',
      'members',
    ]);
  });
});

describe('fonctionnalités de plan', () => {
  const stock = { code: 'stock', status: 'available' as const };
  const withPermission = caps({
    modules: [stock],
    navigation: ['stock'],
    permissions: ['stock.transfer.view'],
  });

  it('masque une entrée dont la fonctionnalité est absente, même avec la permission', () => {
    expect(keys(buildNavigation(modules, withPermission))).toEqual(['stock']);
    expect(buildRoutes(modules, withPermission).map((r) => r.path)).toEqual(['stock']);
  });

  it('affiche l’entrée avec la fonctionnalité ET la permission', () => {
    const enabled = { ...withPermission, features: ['stock.transfers'] };
    expect(keys(buildNavigation(modules, enabled))).toEqual(['stock', 'transfers']);
    const noPermission = { ...enabled, permissions: [] };
    expect(keys(buildNavigation(modules, noPermission))).toEqual(['stock']);
  });
});

describe('buildRoutes', () => {
  it("ne déclare pas les routes d'un module inactif ou non autorisé", () => {
    const paths = buildRoutes(modules, caps({ permissions: ['users.role.view'] })).map(
      (r) => r.path,
    );
    expect(paths).toEqual(['', 'users/roles']);
  });
});
